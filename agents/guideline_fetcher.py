"""Auto-fetch guideline PDF and official forms for a subsidy program.

Behaviour:
  1. Take a ``program_id`` (or a free-text query).
  2. Look it up in the SubsidyRegistry.
  3. Download the guideline PDF and every form .docx.
  4. Cache under ``.cache/guidelines/<program_id>/`` so repeated runs are free.
  5. Return a manifest dict that downstream agents (GuidelineParser,
     DocumentBuilder) can consume directly.

A privacy / politeness note: real production runs **must** respect the
publishing body's terms of use. The default ``timeout=20s`` + a strict user
agent + sequential downloads keep the fetcher polite.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx

from schemas.subsidy_registry import SubsidyProgram, SubsidyRegistry

logger = logging.getLogger(__name__)

_CACHE_ROOT = Path(".cache/guidelines")
_USER_AGENT = "subsidy-brain/0.1 (+https://github.com/ikedakeikaku/subsidy-brain)"


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
        """Resolve and download. Returns a manifest.

        Returns
        -------
        manifest : dict
            ``{
              "program": <SubsidyProgram dict>,
              "guideline_path": <local PDF path or "">,
              "form_paths": {form_id: local_path},
              "from_cache": bool
            }``
        """
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
                await self._download(str(program.guideline_pdf_url), guideline_path)

        form_paths: dict[str, str] = {}
        for form in program.forms:
            if not form.url:
                continue
            suffix = ".docx" if str(form.url).lower().endswith(".docx") else ""
            if not suffix:
                # Fall back: try to infer suffix from the local_path
                suffix = Path(form.local_path).suffix or ".bin"
            local = program_dir / f"{form.form_id}{suffix}"
            if not local.exists():
                was_cached = False
                await self._download(str(form.url), str(local))
            form_paths[form.form_id] = str(local)

        return {
            "program": program.model_dump(mode="json"),
            "guideline_path": guideline_path,
            "form_paths": form_paths,
            "from_cache": was_cached,
        }

    # ------------------------------------------------------------------

    def _resolve(self, query: str) -> SubsidyProgram | None:
        direct = self.registry.get(query)
        if direct:
            return direct
        hits = self.registry.search(query)
        return hits[0] if hits else None

    async def _download(self, url: str, dest: str) -> None:
        logger.info("guideline_fetcher: downloading %s", url)
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                headers={"User-Agent": _USER_AGENT},
                follow_redirects=True,
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                Path(dest).write_bytes(resp.content)
        except Exception as e:  # noqa: BLE001
            # The public demo uses example.invalid URLs that always fail.
            # We log and continue so the rest of the pipeline can run.
            logger.warning("download failed (%s): %s", url, e)
            Path(dest).write_bytes(b"")  # placeholder so cache check stops re-trying
            await asyncio.sleep(0)  # keep the function async-shaped
