"""Smoke tests — the public build must import and exercise its key surfaces.

These are intentionally low-fidelity: they verify the *shape* of the system
(every agent can be instantiated, the skill store round-trips, the demo
generates a valid .docx) rather than the business correctness of the LLM
output. Business correctness lives in the private build's adoption history.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# 1. Every agent is importable and instantiable.
# ---------------------------------------------------------------------------


def test_all_agents_can_be_constructed() -> None:
    from agents.document_builder import DocumentBuilder
    from agents.expense_calc import ExpenseCalc
    from agents.fact_checker import FactChecker
    from agents.financial_reader import FinancialReader
    from agents.guideline_parser import GuidelineParser
    from agents.orchestrator import Orchestrator
    from agents.quality_check import QualityChecker
    from agents.story_builder import StoryBuilder

    agents = [
        Orchestrator(),
        GuidelineParser(),
        FinancialReader(),
        ExpenseCalc(),
        StoryBuilder(),
        FactChecker(),
        DocumentBuilder(),
        QualityChecker(),
    ]
    # Every agent has a stable identity that the orchestrator depends on.
    ids = {a.agent_id for a in agents}
    assert ids == {"#4", "#5", "#6", "#7", "#8", "#12", "#13", "#14"}


# ---------------------------------------------------------------------------
# 2. The skill store round-trips and the feedback loop actually re-scores.
#    This is the central self-improvement signal.
# ---------------------------------------------------------------------------


def test_skill_store_learns_from_feedback() -> None:
    from schemas.skill import (
        ExecutionLog,
        FeedbackInput,
        SkillEntry,
        SkillSearchQuery,
        SkillType,
    )
    from tools.skill_store import SkillStore

    with tempfile.TemporaryDirectory() as tmpdir:
        store = SkillStore(root=tmpdir)

        skill_id = store.add_skill(
            SkillEntry(
                skill_type=SkillType.PROMPT_SKILL,
                agent_id="#8",
                industry="飲食業",
                subsidy_type="販路開拓",
                content={"tip": "自家焙煎の品質訴求が効く"},
                score=0.60,
            )
        )

        store.save_execution_log(
            ExecutionLog(
                applicant_id="A001",
                agent_id="#8",
                used_skill_ids=[skill_id],
                skill_injected=True,
            )
        )

        before = store.search_skills(
            SkillSearchQuery(agent_id="#8", min_score=0.5, limit=10)
        )
        assert len(before) == 1
        assert before[0]["score"] == 0.60

        # Positive feedback should raise the skill's score.
        store.record_feedback(FeedbackInput(applicant_id="A001", adopted=True))
        after_pos = store.search_skills(
            SkillSearchQuery(agent_id="#8", min_score=0.5, limit=10)
        )
        assert after_pos[0]["score"] == pytest.approx(0.70)
        assert after_pos[0]["version"] == 2

        # Then a rejection should lower it again.
        store.save_execution_log(
            ExecutionLog(
                applicant_id="A002",
                agent_id="#8",
                used_skill_ids=[skill_id],
                skill_injected=True,
            )
        )
        store.record_feedback(FeedbackInput(applicant_id="A002", adopted=False))
        after_neg = store.search_skills(
            SkillSearchQuery(agent_id="#8", min_score=0.5, limit=10)
        )
        assert after_neg[0]["score"] == pytest.approx(0.60)
        assert after_neg[0]["version"] == 3


# ---------------------------------------------------------------------------
# 3. Demo pipeline produces a valid Word document end-to-end.
# ---------------------------------------------------------------------------


def test_demo_run_offline_generates_docx() -> None:
    import asyncio

    from demo.run_demo import main_async

    out_dir = ROOT / "demo" / "output"
    if out_dir.exists():
        shutil.rmtree(out_dir)

    asyncio.run(main_async(live=False))

    docx_path = out_dir / "sample_application.docx"
    json_path = out_dir / "sample_application.story.json"
    assert docx_path.exists(), "demo run did not produce the .docx file"
    assert docx_path.stat().st_size > 5_000, "generated .docx is suspiciously small"
    assert json_path.exists(), "story.json sidecar missing"


# ---------------------------------------------------------------------------
# 4. The forward-compatibility Protocols cover the Company-Brain expansion.
# ---------------------------------------------------------------------------


def test_integration_protocols_are_runtime_checkable() -> None:
    from schemas.integrations import (
        CompanyBrainContext,
        DataSourceMetadata,
        InMemoryCompanyBrain,
    )

    brain = InMemoryCompanyBrain(company_id="SAMPLE_001")
    brain.register_source(DataSourceMetadata(source_id="freee", record_count=42))
    brain.register_source(DataSourceMetadata(source_id="drive", record_count=10))

    assert isinstance(brain, CompanyBrainContext)
    assert len(brain.list_sources()) == 2
    assert {s.source_id for s in brain.list_sources()} == {"freee", "drive"}


# ---------------------------------------------------------------------------
# 5. The Claude client modernization is backwards-compatible.
# ---------------------------------------------------------------------------


def test_claude_client_lazy_init_does_not_require_key() -> None:
    """Importing the client without an API key must not crash."""
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        # Force a reimport with no key.
        for mod in list(sys.modules):
            if mod.startswith("tools.claude_client") or mod == "config.settings":
                del sys.modules[mod]
        from tools.claude_client import last_usage  # noqa: F401

        # Just touching the public symbols is the contract.
        assert isinstance(last_usage, dict)
        assert {"input_tokens", "output_tokens"} <= set(last_usage)
    finally:
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved
