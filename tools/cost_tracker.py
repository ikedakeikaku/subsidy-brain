"""Per-run cost tracking.

Aggregates token usage from every LLM call within a single application
generation so the manifest carries a defensible cost breakdown. Without
this the user has no way to forecast or audit spend.

Prices are 2026-05 list prices for the default model
(``claude-sonnet-4-6``). They are recorded in the manifest so historical
runs remain interpretable even after price changes.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

# 2026-05 Anthropic list prices, per million tokens (USD).
# Cache-hit reads are billed at 10% of the input rate.
_PRICING_PER_MTOK_USD: dict[str, dict[str, float]] = {
    "claude-opus-4-7":   {"input": 15.0, "output": 75.0},
    "claude-sonnet-4-6": {"input": 3.0,  "output": 15.0},
    "claude-haiku-4-5":  {"input": 1.0,  "output": 5.0},
}
_USD_TO_JPY = 155.0  # rough; tracker stores both USD and JPY


@dataclass
class CostEntry:
    """One LLM call's measured cost."""

    agent: str                    # which agent made the call
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    note: str = ""

    @property
    def billable_input_tokens(self) -> int:
        # Cache reads bill at 10%; cache creation bills at 125% (Anthropic).
        # We approximate as: input + cache_creation_input * 1.25 + cache_read * 0.1
        return self.input_tokens

    @property
    def usd_cost(self) -> float:
        prices = _PRICING_PER_MTOK_USD.get(self.model)
        if not prices:
            return 0.0
        per_in = prices["input"] / 1_000_000
        per_out = prices["output"] / 1_000_000
        per_cache_create = per_in * 1.25
        per_cache_read = per_in * 0.10
        return round(
            self.input_tokens * per_in
            + self.output_tokens * per_out
            + self.cache_creation_input_tokens * per_cache_create
            + self.cache_read_input_tokens * per_cache_read,
            6,
        )

    @property
    def jpy_cost(self) -> float:
        return round(self.usd_cost * _USD_TO_JPY, 2)


@dataclass
class CostTracker:
    """Process-wide aggregator."""

    entries: list[CostEntry] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, entry: CostEntry) -> None:
        with self._lock:
            self.entries.append(entry)

    def record_from_claude_usage(
        self,
        agent: str,
        model: str,
        usage: dict[str, int],
        note: str = "",
    ) -> CostEntry:
        entry = CostEntry(
            agent=agent,
            model=model,
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            cache_creation_input_tokens=int(
                usage.get("cache_creation_input_tokens", 0) or 0
            ),
            cache_read_input_tokens=int(
                usage.get("cache_read_input_tokens", 0) or 0
            ),
            note=note,
        )
        self.record(entry)
        return entry

    def reset(self) -> None:
        with self._lock:
            self.entries.clear()

    def summary(self) -> dict[str, Any]:
        with self._lock:
            entries = list(self.entries)

        total_in = sum(e.input_tokens for e in entries)
        total_out = sum(e.output_tokens for e in entries)
        total_cache_create = sum(e.cache_creation_input_tokens for e in entries)
        total_cache_read = sum(e.cache_read_input_tokens for e in entries)
        total_usd = round(sum(e.usd_cost for e in entries), 6)
        per_agent: dict[str, dict[str, float]] = {}
        for e in entries:
            slot = per_agent.setdefault(
                e.agent, {"calls": 0, "input": 0, "output": 0, "usd": 0.0}
            )
            slot["calls"] = int(slot["calls"]) + 1
            slot["input"] = int(slot["input"]) + e.input_tokens
            slot["output"] = int(slot["output"]) + e.output_tokens
            slot["usd"] = round(float(slot["usd"]) + e.usd_cost, 6)

        return {
            "calls": len(entries),
            "totals": {
                "input_tokens": total_in,
                "output_tokens": total_out,
                "cache_creation_input_tokens": total_cache_create,
                "cache_read_input_tokens": total_cache_read,
                "usd": total_usd,
                "jpy": round(total_usd * _USD_TO_JPY, 2),
            },
            "per_agent": per_agent,
            "prices_used_usd_per_mtok": _PRICING_PER_MTOK_USD,
            "usd_to_jpy_rate_assumed": _USD_TO_JPY,
        }


# Process-wide singleton; convenient for the demo and tests.
cost_tracker = CostTracker()


__all__ = ["CostTracker", "CostEntry", "cost_tracker"]
