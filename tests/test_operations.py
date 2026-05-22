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


def test_document_builder_works_for_three_subsidies() -> None:
    import yaml

    from agents.document_builder import DocumentBuilder
    from demo.mock_story import MOCK_STORY
    from schemas.document_build import DocumentBuildInput

    company = yaml.safe_load(
        (ROOT / "demo" / "sample_company.yaml").read_text(encoding="utf-8")
    )

    expected_section_counts = {
        "jizoku_19": 11,
        "monozukuri_v18": 10,
        "shoryokuka_v2": 8,
    }

    for preset_id, expected_n in expected_section_counts.items():
        inp = DocumentBuildInput(
            story=MOCK_STORY,
            expenses=company.get("expenses", {}),
            financial_data=company.get("financial", {}),
            hearing_data=company,
            template_id=preset_id,
            applicant_id="SAMPLE",
        )
        result = asyncio.run(DocumentBuilder().execute(inp))
        assert len(result.documents) >= 1, preset_id
        out = Path(result.documents[0].file_path)
        assert out.exists(), preset_id
        assert out.stat().st_size > 30_000, preset_id
        # The profile drives section count, so it differs per subsidy
        assert result.metadata.total_pages == expected_n, (
            f"{preset_id}: expected {expected_n} sections, "
            f"got {result.metadata.total_pages}"
        )


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
