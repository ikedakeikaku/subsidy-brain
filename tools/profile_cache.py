"""On-disk cache for synthesised SubsidyProfile objects.

Profile synthesis runs Claude web search, which is expensive and slow.
Once we have a usable profile for a given subsidy we serialise it to
``.cache/profiles/<id>.json`` with an issued-at timestamp. Subsequent
runs for the same subsidy reuse the cached profile until ``ttl_seconds``
elapses or the user explicitly evicts it.

This is the "memorise what I researched yesterday" half of the
human-consultant model — the agent still does the research the first
time it meets a new subsidy.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from schemas.subsidy_profile import SubsidyProfile

logger = logging.getLogger(__name__)

_DEFAULT_ROOT = Path(".cache/profiles")
_DEFAULT_TTL = 60 * 60 * 24 * 7  # 1 week


class ProfileCache:
    """Tiny on-disk cache keyed by program_id."""

    def __init__(
        self, root: Path | str | None = None, ttl_seconds: int = _DEFAULT_TTL
    ) -> None:
        self.root = Path(root or _DEFAULT_ROOT)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_seconds

    # ------------------------------------------------------------------

    def load(self, program_id: str) -> SubsidyProfile | None:
        path = self._path_for(program_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning("profile cache read failed for %s: %s", program_id, e)
            return None
        issued_at = float(data.get("_issued_at", 0))
        # ttl == 0 means "expire immediately on the next read";
        # ttl < 0 disables the TTL check entirely;
        # ttl > 0 expires after the configured number of seconds.
        if self.ttl >= 0 and time.time() - issued_at > self.ttl:
            logger.info("profile cache stale for %s; expiring", program_id)
            return None
        payload = data.get("profile")
        if not payload:
            return None
        try:
            return SubsidyProfile.model_validate(payload)
        except Exception as e:  # noqa: BLE001
            logger.warning("profile cache invalid for %s: %s", program_id, e)
            return None

    def save(self, profile: SubsidyProfile) -> Path:
        path = self._path_for(profile.program_id)
        path.write_text(
            json.dumps(
                {
                    "_issued_at": time.time(),
                    "profile": profile.model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        return path

    def evict(self, program_id: str) -> bool:
        path = self._path_for(program_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def _path_for(self, program_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in program_id)
        return self.root / f"{safe}.json"


# Process-wide singleton convenient for the demo / pipeline
profile_cache = ProfileCache()


__all__ = ["ProfileCache", "profile_cache"]
