"""Forward-compatibility interfaces for the Company Brain expansion.

These are Protocol-based shape contracts that downstream modules (CFO ledger
reader, freee MCP connector, monthly P/L watcher, drive sync) can implement
without changing the agent layer. They live here so the public build shows
**the seams** that the private build will fill in.

Why now: the BootCamp submission demonstrates that the codebase has been
architected for the company-brain trajectory from day one — Phase 2 (CFO) and
Phase 3 (data integration) are not bolted-on later, they are pre-declared.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

# ============================================================================
# Phase 2 — CFO interfaces
# ============================================================================


class MonthlyPL(BaseModel):
    """A single month of P&L, normalised across accounting backends."""

    month: date
    revenue: int = Field(default=0)
    cogs: int = Field(default=0)
    gross_profit: int = Field(default=0)
    sga: int = Field(default=0)
    operating_profit: int = Field(default=0)
    raw: dict = Field(default_factory=dict, description="Source-system payload")


class CashflowEvent(BaseModel):
    """A scheduled or projected cash movement."""

    event_date: date
    direction: str = Field(description="'in' | 'out'")
    amount: int
    counterparty: str = ""
    category: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class SubsidyMatch(BaseModel):
    """A subsidy programme matched against a company profile."""

    program_id: str
    program_name: str
    deadline: date | None = None
    eligibility_score: float = Field(ge=0.0, le=1.0)
    estimated_award_yen: int
    reasoning: str


@runtime_checkable
class CFOLedgerReader(Protocol):
    """Read monthly P/L history from an accounting backend.

    Implementations are expected for freee, Money Forward, raw CSV, and a
    Claude Code "ファイルシステムをDB" path that reads Excel directly.
    """

    async def fetch_monthly_pl(
        self, company_id: str, *, months: int = 24
    ) -> list[MonthlyPL]: ...


@runtime_checkable
class CashflowProjector(Protocol):
    """Project the next N months of cash movements."""

    async def project(
        self, company_id: str, *, horizon_months: int = 6
    ) -> list[CashflowEvent]: ...


@runtime_checkable
class SubsidyMatcher(Protocol):
    """Match a company profile to subsidy programmes in scope right now."""

    async def match(
        self, company_profile: dict[str, Any], *, limit: int = 10
    ) -> list[SubsidyMatch]: ...


# ============================================================================
# Phase 3 — Data integration interfaces
# ============================================================================


class DataSourceMetadata(BaseModel):
    """Metadata for a data source under the company brain's purview."""

    source_id: str = Field(description="Stable id (e.g. 'gmail', 'drive', 'freee')")
    last_synced_at: str = ""
    record_count: int = 0
    schema_version: str = ""


@runtime_checkable
class DataConnector(Protocol):
    """A connector that exposes one external data source as a local namespace
    under the filesystem-as-DB pattern.

    The contract is intentionally narrow: pull the source down to local files
    in a deterministic layout, return what changed.
    """

    source_id: str

    async def sync(self, *, full: bool = False) -> DataSourceMetadata: ...

    def local_root(self) -> str: ...


@runtime_checkable
class CompanyBrainContext(Protocol):
    """The aggregate read-only context surface that downstream agents see.

    Phase 1 (subsidy) only needs hearing + financial. Phase 2 (CFO) adds
    monthly_pl + cashflow. Phase 3 unifies all connectors under one root.
    """

    company_id: str

    def list_sources(self) -> list[DataSourceMetadata]: ...

    def resolve(self, source_id: str, path: str) -> str:
        """Return absolute local path for a record in a given source."""
        ...


# ============================================================================
# Convenience: a tiny in-memory stub usable from tests and the public demo
# ============================================================================


class InMemoryCompanyBrain:
    """Minimal in-memory implementation. Not for production; for tests/demo."""

    def __init__(self, company_id: str) -> None:
        self.company_id = company_id
        self._sources: dict[str, DataSourceMetadata] = {}
        self._paths: dict[tuple[str, str], str] = {}

    def list_sources(self) -> list[DataSourceMetadata]:
        return list(self._sources.values())

    def register_source(self, meta: DataSourceMetadata) -> None:
        self._sources[meta.source_id] = meta

    def attach_path(self, source_id: str, path: str, absolute: str) -> None:
        self._paths[(source_id, path)] = absolute

    def resolve(self, source_id: str, path: str) -> str:
        return self._paths.get((source_id, path), "")
