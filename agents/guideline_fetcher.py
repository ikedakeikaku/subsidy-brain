"""Auto-fetch guideline PDF and official forms for a subsidy program.

Publishers rarely expose stable direct URLs to 様式 docx files — they
serve them from "資料ダウンロード" landing pages whose URLs the LLM
discovers, but which return HTML rather than the actual binary. The
fetcher therefore has a two-step strategy:

  1. Download the URL as-is.
  2. If the response is HTML when we expected a binary (.docx / .xlsx /
     .pdf), parse the HTML for actual download links and try again.

The cache lives at ``.cache/guidelines/<program_id>/`` so repeated runs
are free, and politeness defaults (20s timeout, strict user agent,
sequential requests) avoid hammering the publishing body.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from schemas.subsidy_registry import SubsidyProgram, SubsidyRegistry

logger = logging.getLogger(__name__)

_CACHE_ROOT = Path(".cache/guidelines")
_USER_AGENT = "subsidy-brain/0.1 (+https://github.com/ikedakeikaku/subsidy-brain)"


# Signatures of real binary file types (first few bytes)
_DOCX_MAGIC = b"PK\x03\x04"  # ZIP (docx/xlsx share this)
_PDF_MAGIC = b"%PDF-"


class GuidelineFetcher:
    """Resolve a subsidy program and bring its documents to disk."""

    agent_id = "#fetch"
    agent_name = "GuidelineFetcher"

    def __init__(
        self,
        registry: SubsidyRegistry,
        cache_root: Path | str | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.registry = registry
        self.cache_root = Path(cache_root or _CACHE_ROOT)
        self.timeout = timeout

    async def fetch(self, query: str) -> dict[str, Any]:
        """Resolve and download. Returns a manifest."""
        program = self._resolve(query)
        if program is None:
            return {
                "program": None,
                "guideline_path": "",
                "form_paths": {},
                "from_cache": False,
                "error": f"No program found for query: {query!r}",
            }

        program_dir = self.cache_root / program.program_id
        program_dir.mkdir(parents=True, exist_ok=True)
        was_cached = True

        guideline_path = ""
        if program.guideline_pdf_url:
            guideline_path = str(program_dir / "guideline.pdf")
            if not Path(guideline_path).exists():
                was_cached = False
                await self._download_with_fallback(
                    str(program.guideline_pdf_url),
                    guideline_path,
                    expected_kinds=("pdf",),
                    link_pattern=r"\.pdf(\?|$)",
                )

        form_paths: dict[str, str] = {}
        for form in program.forms:
            local = await self._fetch_form_entry(form, program_dir)
            if local is None:
                continue
            form_paths[form.form_id] = local
            if not (program_dir / Path(local).name).is_file():
                was_cached = False

        additional_paths: dict[str, str] = {}
        for doc in program.additional_documents:
            local = await self._fetch_form_entry(doc, program_dir, subfolder="docs")
            if local is None:
                continue
            additional_paths[doc.form_id] = local

        return {
            "program": program.model_dump(mode="json"),
            "guideline_path": guideline_path,
            "form_paths": form_paths,
            "additional_paths": additional_paths,
            "from_cache": was_cached,
        }

    async def _fetch_form_entry(
        self,
        form: Any,
        program_dir: Path,
        *,
        subfolder: str | None = None,
    ) -> str | None:
        """Download one form / additional document entry. Returns local
        path on success (even if the bytes are wrong — downstream
        validates), or None when the entry has no URL."""
        if not form.url:
            return None
        url_lc = str(form.url).lower()
        if url_lc.endswith(".docx"):
            suffix, expected_kinds, pattern = ".docx", ("docx",), r"\.docx(\?|$)"
        elif url_lc.endswith(".xlsx"):
            suffix, expected_kinds, pattern = ".xlsx", ("xlsx",), r"\.xlsx(\?|$)"
        elif url_lc.endswith(".pdf"):
            suffix, expected_kinds, pattern = ".pdf", ("pdf",), r"\.pdf(\?|$)"
        else:
            suffix = Path(form.local_path).suffix or ".docx"
            expected_kinds = ("docx", "xlsx", "pdf")
            pattern = r"\.(docx|xlsx|pdf)(\?|$)"

        target_dir = program_dir / subfolder if subfolder else program_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        local = target_dir / f"{form.form_id}{suffix}"
        if not local.exists():
            await self._download_with_fallback(
                str(form.url),
                str(local),
                expected_kinds=expected_kinds,
                link_pattern=pattern,
            )
        return str(local)

    # ------------------------------------------------------------------

    def _resolve(self, query: str) -> SubsidyProgram | None:
        direct = self.registry.get(query)
        if direct:
            return direct
        hits = self.registry.search(query)
        return hits[0] if hits else None

    async def _download_with_fallback(
        self,
        url: str,
        dest: str,
        *,
        expected_kinds: tuple[str, ...],
        link_pattern: str,
    ) -> None:
        """Download ``url``; if the response is HTML when a binary was
        expected, scrape the HTML for links matching ``link_pattern`` and
        download the first one that yields the right kind of file.

        This is the workaround for publishers that hide their 様式 files
        behind "資料ダウンロード" landing pages instead of stable direct
        URLs.
        """
        logger.info("guideline_fetcher: downloading %s", url)
        content = await self._http_get(url)
        if content is None:
            Path(dest).write_bytes(b"")
            return

        if self._content_kind(content) in expected_kinds:
            Path(dest).write_bytes(content)
            return

        # Got something else — try to parse it as HTML and recover.
        text = self._safe_decode(content)
        if not text:
            Path(dest).write_bytes(content)
            return

        candidate_links = self._extract_links(text, base_url=url, pattern=link_pattern)
        if not candidate_links:
            logger.info(
                "guideline_fetcher: no recovery links matching %s in HTML at %s",
                link_pattern,
                url,
            )
            Path(dest).write_bytes(content)
            return

        for cand in candidate_links[:5]:
            logger.info(
                "guideline_fetcher: trying recovered link %s (kind=%s expected)",
                cand,
                "/".join(expected_kinds),
            )
            sub = await self._http_get(cand)
            if sub is None:
                continue
            if self._content_kind(sub) in expected_kinds:
                Path(dest).write_bytes(sub)
                logger.info(
                    "guideline_fetcher: recovered %s from HTML landing page",
                    Path(dest).name,
                )
                return

        # No recovery succeeded — write whatever we got first so cache
        # check stops retrying, and let downstream agents validate.
        logger.warning(
            "guideline_fetcher: could not recover a real %s from %s",
            "/".join(expected_kinds),
            url,
        )
        Path(dest).write_bytes(content)

    async def _http_get(self, url: str) -> bytes | None:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                headers={"User-Agent": _USER_AGENT},
                follow_redirects=True,
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.content
        except Exception as e:  # noqa: BLE001
            logger.warning("download failed (%s): %s", url, e)
            await asyncio.sleep(0)
            return None

    @staticmethod
    def _content_kind(content: bytes) -> str | None:
        """Return 'docx' / 'xlsx' / 'pdf' / 'html' / None based on the
        first few bytes of the response body."""
        if not content:
            return None
        if content.startswith(_PDF_MAGIC):
            return "pdf"
        if content.startswith(_DOCX_MAGIC):
            # docx and xlsx share the ZIP magic — to disambiguate would
            # need to inspect the zip contents. For our purposes, either
            # is acceptable when the expected list includes both.
            try:
                import zipfile
                from io import BytesIO

                with zipfile.ZipFile(BytesIO(content)) as z:
                    names = z.namelist()
                if any(n.startswith("xl/") for n in names):
                    return "xlsx"
                if any(n.startswith("word/") for n in names):
                    return "docx"
            except Exception:  # noqa: BLE001
                pass
            return "docx"  # safe default
        try:
            head = content[:200].decode("utf-8", errors="ignore").lower()
        except Exception:  # noqa: BLE001
            return None
        if "<html" in head or "<!doctype" in head:
            return "html"
        return None

    @staticmethod
    def _safe_decode(content: bytes) -> str:
        try:
            return content.decode("utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _extract_links(html: str, *, base_url: str, pattern: str) -> list[str]:
        """Pull every href whose path ends in the expected extension.

        Sorted so direct ``.docx`` links come before ones that need
        further redirects (heuristic: shorter path = closer to the file).
        """
        # Find href="..." and href='...' patterns
        href_re = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
        ext_re = re.compile(pattern, re.IGNORECASE)
        seen: set[str] = set()
        results: list[str] = []
        for m in href_re.finditer(html):
            raw = m.group(1).strip()
            if not raw or raw.startswith("javascript:"):
                continue
            if not ext_re.search(raw):
                continue
            absolute = urljoin(base_url, raw)
            if absolute in seen:
                continue
            seen.add(absolute)
            results.append(absolute)
        results.sort(key=len)
        return results
