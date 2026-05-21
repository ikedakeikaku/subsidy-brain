"""Self-improving knowledge store.

Compact, file-system backed implementation of the learning layer. The local
filesystem is the database; any human or any other agent can read and edit
the JSONL files directly.

Three things accumulate over time:

  1. ExecutionLog       — every agent invocation (input/output hashes, timing,
                          which skills were used). The raw audit trail.
  2. SkillEntry         — distilled know-how (prompt patterns, rubrics,
                          industry-specific examples). Scored 0–1; promoted
                          when used successfully, demoted on failure.
  3. FeedbackInput      — adoption / rejection results with reviewer comments.
                          The ground-truth signal that drives skill scoring.

Storage is JSONL under ``<root>/.skill_store/`` (configurable via
``SKILL_STORE_ROOT`` env var). Plain files mean any human or any other agent
can read/inspect/edit them; no opaque DB.

The store is intentionally additive. We don't delete; we re-score and version
so that the history of what worked and what didn't is preserved.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from schemas.skill import (
    ExecutionLog,
    FeedbackInput,
    SkillEntry,
    SkillSearchQuery,
    SkillType,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash(data: Any) -> str:
    canon = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class _Paths:
    root: Path
    skills: Path
    logs: Path
    feedback: Path
    knowledge: Path

    @classmethod
    def for_root(cls, root: Path) -> _Paths:
        root.mkdir(parents=True, exist_ok=True)
        return cls(
            root=root,
            skills=root / "skills.jsonl",
            logs=root / "execution_logs.jsonl",
            feedback=root / "feedback.jsonl",
            knowledge=root / "knowledge",
        )


class SkillStore:
    """File-backed skill store. Thread-safe for append operations."""

    def __init__(self, root: str | Path | None = None) -> None:
        root_str = str(root) if root else os.getenv("SKILL_STORE_ROOT", ".skill_store")
        self._paths = _Paths.for_root(Path(root_str))
        self._lock = threading.Lock()
        self._paths.knowledge.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Execution logs — the raw audit trail
    # ------------------------------------------------------------------

    def save_execution_log(self, log: ExecutionLog | dict) -> str:
        entry = log.model_dump(mode="json") if isinstance(log, ExecutionLog) else dict(log)
        if not entry.get("id"):
            entry["id"] = uuid.uuid4().hex[:12]
        entry.setdefault("created_at", _now_iso())
        self._append(self._paths.logs, entry)
        return entry["id"]

    def iter_execution_logs(
        self, agent_id: str | None = None, applicant_id: str | None = None
    ) -> list[dict]:
        rows = list(self._read_jsonl(self._paths.logs))
        if agent_id:
            rows = [r for r in rows if r.get("agent_id") == agent_id]
        if applicant_id:
            rows = [r for r in rows if r.get("applicant_id") == applicant_id]
        return rows

    # ------------------------------------------------------------------
    # Skills — distilled know-how with scoring
    # ------------------------------------------------------------------

    def add_skill(self, skill: SkillEntry | dict) -> str:
        entry = skill.model_dump(mode="json") if isinstance(skill, SkillEntry) else dict(skill)
        if not entry.get("id"):
            entry["id"] = uuid.uuid4().hex[:12]
        entry.setdefault("created_at", _now_iso())
        entry["updated_at"] = _now_iso()
        self._append(self._paths.skills, entry)
        return entry["id"]

    def search_skills(self, query: SkillSearchQuery | dict) -> list[dict]:
        q = (
            query.model_dump()
            if isinstance(query, SkillSearchQuery)
            else dict(query)
        )
        rows = list(self._read_jsonl(self._paths.skills))
        # Keep only the latest version per skill id.
        latest: dict[str, dict] = {}
        for r in rows:
            sid = r.get("id")
            if not sid:
                continue
            existing = latest.get(sid)
            if not existing or r.get("version", 1) >= existing.get("version", 1):
                latest[sid] = r
        candidates = list(latest.values())
        if q.get("agent_id"):
            candidates = [c for c in candidates if c.get("agent_id") == q["agent_id"]]
        if q.get("industry"):
            candidates = [
                c for c in candidates if (c.get("industry") or "") == q["industry"]
            ]
        if q.get("subsidy_type"):
            candidates = [
                c
                for c in candidates
                if (c.get("subsidy_type") or "") == q["subsidy_type"]
            ]
        min_score = q.get("min_score", 0.7)
        candidates = [c for c in candidates if c.get("score", 0.0) >= min_score]
        candidates.sort(key=lambda c: c.get("score", 0.0), reverse=True)
        return candidates[: q.get("limit", 5)]

    def search_similar_skills(
        self, agent_id: str, input_dict: dict[str, Any], limit: int = 3
    ) -> list[dict]:
        """Lightweight similarity search by industry / subsidy type tags.

        A real implementation would embed the input and use a vector index; the
        public build uses tag-equality as a deliberate, debuggable baseline.
        """
        industry = (
            input_dict.get("industry")
            or input_dict.get("company", {}).get("industry")
            or input_dict.get("hearing", {}).get("industry")
        )
        subsidy = input_dict.get("subsidy_type") or input_dict.get("subsidy")
        q = SkillSearchQuery(
            agent_id=agent_id,
            industry=industry,
            subsidy_type=subsidy,
            min_score=0.5,
            limit=limit,
        )
        return self.search_skills(q)

    def increment_usage(self, skill_id: str, *, success: bool | None = None) -> None:
        """Append a usage event. Score adjusted lazily on reads via feedback."""
        self._append(
            self._paths.skills,
            {
                "id": skill_id,
                "_event": "usage",
                "success": success,
                "ts": _now_iso(),
            },
        )

    # ------------------------------------------------------------------
    # Knowledge — opinionated free-form notes (markdown / json blobs)
    # ------------------------------------------------------------------

    def get_knowledge(self, key: str) -> dict:
        path = self._paths.knowledge / f"{key}.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning("knowledge read failed for %s: %s", key, e)
            return {}

    def set_knowledge(self, key: str, value: dict) -> None:
        path = self._paths.knowledge / f"{key}.json"
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Feedback — adoption results drive skill promotion / demotion
    # ------------------------------------------------------------------

    def record_feedback(self, fb: FeedbackInput | dict) -> str:
        entry = fb.model_dump(mode="json") if isinstance(fb, FeedbackInput) else dict(fb)
        entry.setdefault("id", uuid.uuid4().hex[:12])
        entry.setdefault("created_at", _now_iso())
        self._append(self._paths.feedback, entry)
        self._reweight_skills_after_feedback(entry)
        return entry["id"]

    def _reweight_skills_after_feedback(self, feedback: dict) -> None:
        applicant_id = feedback.get("applicant_id")
        adopted = bool(feedback.get("adopted"))
        # Find every execution log for this applicant
        used_skill_ids: set[str] = set()
        for log in self.iter_execution_logs(applicant_id=applicant_id):
            for sid in log.get("used_skill_ids", []) or []:
                used_skill_ids.add(sid)
        if not used_skill_ids:
            return
        # Append a new skill version with adjusted score (additive, immutable)
        for skill in list(self._read_jsonl(self._paths.skills)):
            sid = skill.get("id")
            if sid not in used_skill_ids:
                continue
            if skill.get("_event"):
                continue
            old_score = float(skill.get("score", 0.5))
            delta = 0.10 if adopted else -0.10
            new_score = max(0.0, min(1.0, old_score + delta))
            updated = dict(skill)
            updated["score"] = round(new_score, 4)
            updated["version"] = int(skill.get("version", 1)) + 1
            updated["updated_at"] = _now_iso()
            updated["last_feedback"] = {
                "applicant_id": applicant_id,
                "adopted": adopted,
                "delta": delta,
            }
            self._append(self._paths.skills, updated)

    # ------------------------------------------------------------------
    # Storage primitives
    # ------------------------------------------------------------------

    def _append(self, path: Path, entry: dict) -> None:
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with self._lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    @staticmethod
    def _read_jsonl(path: Path):
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as e:
                    logger.warning("malformed JSONL row in %s: %s", path, e)
                    continue


# Module-level singleton so existing imports keep working.
skill_store = SkillStore()


__all__ = [
    "SkillStore",
    "skill_store",
    "ExecutionLog",
    "SkillEntry",
    "SkillSearchQuery",
    "FeedbackInput",
    "SkillType",
    "_hash",
]
