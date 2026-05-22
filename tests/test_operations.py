"""Tests for operational quality: web_search, cost_tracker, observability,
multi-subsidy support."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Web search — unified provider with safe no-op fallback
# ---------------------------------------------------------------------------


def test_web_search_returns_none_provider_without_keys(monkeypatch) -> None:
    from config import settings as settings_mod
    from tools.web_search import web_search

    monkeypatch.setattr(settings_mod.settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings_mod.settings, "perplexity_api_key", "")
    res = asyncio.run(web_search("持続化補助金"))
    assert res.provider == "none"
    assert res.answer == ""
    assert res.citations == []


# ---------------------------------------------------------------------------
# Cost tracker — prices and aggregation
# ---------------------------------------------------------------------------


def test_cost_tracker_aggregates_per_agent() -> None:
    from tools.cost_tracker import CostEntry, CostTracker

    tracker = CostTracker()
    tracker.record(CostEntry(agent="#8", model="claude-sonnet-4-6", input_tokens=10_000, output_tokens=2_000))
    tracker.record(CostEntry(agent="#8", model="claude-sonnet-4-6", input_tokens=8_000, output_tokens=1_500))
    tracker.record(CostEntry(agent="#13", model="claude-sonnet-4-6", input_tokens=2_500, output_tokens=800))

    s = tracker.summary()
    assert s["calls"] == 3
    assert s["totals"]["input_tokens"] == 20_500
    assert s["totals"]["output_tokens"] == 4_300
    assert s["totals"]["usd"] > 0
    assert set(s["per_agent"]) == {"#8", "#13"}
    assert s["per_agent"]["#8"]["calls"] == 2


# ---------------------------------------------------------------------------
# Multi-subsidy: the same DocumentBuilder handles 3 different programmes
# ---------------------------------------------------------------------------


def test_natural_pipeline_handles_three_distinct_subsidies(
    monkeypatch, sample_company
) -> None:
    """Same engine, three different subsidy names — all must produce a
    valid .docx without any preset YAML or curated template."""
    import shutil

    from agents.profile_synthesizer import ProfileSynthesizer
    from config import settings as settings_mod
    from demo.mock_story import MOCK_STORY
    from tools.document_assembler import assemble_document

    monkeypatch.setattr(settings_mod.settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings_mod.settings, "perplexity_api_key", "")
    monkeypatch.chdir(ROOT)

    out_dir = ROOT / "demo" / "output"
    shutil.rmtree(out_dir, ignore_errors=True)

    for query in ["持続化補助金 第19回", "ものづくり補助金 第18次", "省力化投資補助金 第2回"]:
        profile = asyncio.run(ProfileSynthesizer().synthesize(query))
        out = out_dir / f"{profile.program_id}_test.docx"
        assemble_document(
            profile=profile,
            company=sample_company,
            story=MOCK_STORY,
            out_path=out,
            extra_metadata={},
            quality_block=None,
        )
        assert out.exists(), query
        assert out.stat().st_size > 30_000, query


# ---------------------------------------------------------------------------
# Structured logging configures without throwing and renders JSON
# ---------------------------------------------------------------------------


def test_observability_emits_structured_logs(capsys) -> None:
    from tools.observability import (
        bind_run_context,
        configure_logging,
        get_logger,
        new_run_id,
    )

    configure_logging(json_output=True, level="INFO")
    bind_run_context(run_id=new_run_id(), subsidy="test_subsidy")
    log = get_logger("test.smoke")
    log.info("hello", k=1)
    captured = capsys.readouterr()
    # JSON output must include the bound context and event
    assert "test_subsidy" in captured.out
    assert "hello" in captured.out
