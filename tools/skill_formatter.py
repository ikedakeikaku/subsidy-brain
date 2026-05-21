"""Render skill entries as few-shot examples for prompt injection."""
from __future__ import annotations

import json
from typing import Any, Iterable


def format_as_few_shot(skills: Iterable[Any]) -> str:
    """Convert a list of skill dicts (or SkillEntry) into a few-shot block.

    The output is a markdown section that can be appended to a system prompt.
    Returns an empty string if no skills are provided so callers can simply
    concatenate the result.
    """
    rows = list(skills)
    if not rows:
        return ""

    lines: list[str] = ["", "## 過去の高評価パターン（few-shot）"]
    for i, raw in enumerate(rows, start=1):
        entry = raw if isinstance(raw, dict) else raw.__dict__
        meta_bits: list[str] = []
        if industry := entry.get("industry"):
            meta_bits.append(f"業種={industry}")
        if subsidy := entry.get("subsidy_type"):
            meta_bits.append(f"補助金={subsidy}")
        if score := entry.get("score"):
            meta_bits.append(f"score={score:.2f}")
        meta = " / ".join(meta_bits) if meta_bits else "—"
        lines.append(f"### 例 #{i} ({meta})")
        content = entry.get("content")
        if isinstance(content, dict):
            lines.append("```json")
            lines.append(json.dumps(content, ensure_ascii=False, indent=2))
            lines.append("```")
        elif content:
            lines.append(str(content))
    lines.append("")
    return "\n".join(lines)
