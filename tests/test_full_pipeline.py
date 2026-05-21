"""Tests for the auto-fetch / template-filling extensions.

These exercise the new Phase 1.5 surfaces — subsidy registry, guideline
fetcher (cache behaviour), adoption researcher (no-key path), template
filler, and the unified ``run_full_demo`` entrypoint.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Subsidy registry
# ---------------------------------------------------------------------------


def test_registry_loads_and_resolves() -> None:
    from schemas.subsidy_registry import SubsidyRegistry, YamlSubsidyRegistry

    reg = YamlSubsidyRegistry(ROOT / "demo" / "sample_registry.yaml")
    assert isinstance(reg, SubsidyRegistry)

    prog = reg.get("sample_hanro_kaitaku_v1")
    assert prog is not None
    assert prog.canonical_name.startswith("販路開拓支援補助金")
    assert prog.max_award_yen == 1_000_000
    assert len(prog.forms) == 2

    # Free-text search should also resolve.
    hits = reg.search("販路開拓")
    assert any(h.program_id == "sample_hanro_kaitaku_v1" for h in hits)


# ---------------------------------------------------------------------------
# Guideline fetcher
# ---------------------------------------------------------------------------


def test_guideline_fetcher_caches_locally(tmp_path: Path) -> None:
    from agents.guideline_fetcher import GuidelineFetcher
    from schemas.subsidy_registry import YamlSubsidyRegistry

    reg = YamlSubsidyRegistry(ROOT / "demo" / "sample_registry.yaml")
    fetcher = GuidelineFetcher(registry=reg, cache_root=tmp_path)

    first = asyncio.run(fetcher.fetch("sample_hanro_kaitaku_v1"))
    assert first["program"]["program_id"] == "sample_hanro_kaitaku_v1"
    assert first["from_cache"] is False  # first run populated cache

    # Cache placeholders exist even when downloads fail (example.invalid).
    assert Path(first["guideline_path"]).exists()
    for path in first["form_paths"].values():
        assert Path(path).exists()

    second = asyncio.run(fetcher.fetch("販路開拓"))
    assert second["from_cache"] is True


def test_guideline_fetcher_handles_unknown_query() -> None:
    from agents.guideline_fetcher import GuidelineFetcher
    from schemas.subsidy_registry import YamlSubsidyRegistry

    reg = YamlSubsidyRegistry(ROOT / "demo" / "sample_registry.yaml")
    fetcher = GuidelineFetcher(registry=reg, cache_root=ROOT / ".cache_test_unknown")
    result = asyncio.run(fetcher.fetch("does-not-exist"))
    assert result["program"] is None
    assert "error" in result


# ---------------------------------------------------------------------------
# Adoption researcher — no-API-key path must be safe
# ---------------------------------------------------------------------------


def test_adoption_researcher_noop_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from agents.adoption_researcher import AdoptionResearcher
    from config import settings as settings_mod
    from schemas.subsidy_registry import YamlSubsidyRegistry

    # Force empty key regardless of environment.
    monkeypatch.setattr(settings_mod.settings, "perplexity_api_key", "")
    reg = YamlSubsidyRegistry(ROOT / "demo" / "sample_registry.yaml")
    prog = reg.get("sample_hanro_kaitaku_v1")
    assert prog is not None

    result = asyncio.run(
        AdoptionResearcher().research(prog, industry="飲食業（カフェ）")
    )
    assert result["available"] is False
    assert result["knowledge_key"] == ""


# ---------------------------------------------------------------------------
# Template filler — fidelity-preserving placeholder substitution
# ---------------------------------------------------------------------------


def _ensure_template() -> Path:
    target = ROOT / "templates" / "sample_hanro_kaitaku_v1" / "様式2.docx"
    if not target.exists():
        subprocess.run(
            [sys.executable, str(ROOT / "templates" / "build_sample_template.py")],
            check=True,
        )
    return target


def test_template_filler_substitutes_every_placeholder(tmp_path: Path) -> None:
    from tools.template_filler import fill_template

    template = _ensure_template()
    out = tmp_path / "out.docx"

    subs = {
        "company_name": "Test Co",
        "representative": "Test Person",
        "business_address": "Tokyo",
        "employee_count": "5名",
        "bonus_env_change": "BONUS",
        "section_1_1": "S11",
        "section_1_2": "S12",
        "section_1_3": "S13",
        "section_4_2": "S42",
        "section_effect": "EFF",
        "pl_y1_year": "2022",
        "pl_y1_revenue": "1",
        "pl_y1_profit": "2",
        "pl_y2_year": "2023",
        "pl_y2_revenue": "3",
        "pl_y2_profit": "4",
        "pl_y3_year": "2024",
        "pl_y3_revenue": "5",
        "pl_y3_profit": "6",
        "expense_table_csv": "items",
        "subsidy_amount": "10",
        "self_funding": "20",
    }
    report = fill_template(template, out, subs)

    assert out.exists()
    assert report["missing_keys"] == [], f"unresolved placeholders: {report}"
    assert report["unique_keys_used"] == len(subs)


# ---------------------------------------------------------------------------
# Full pipeline runs end-to-end and emits a manifest
# ---------------------------------------------------------------------------


def test_full_pipeline_produces_docx_and_manifest() -> None:
    from demo.run_full_demo import run

    asyncio.run(run(live=False))

    out_docx = ROOT / "demo" / "output" / "full_pipeline_application.docx"
    manifest_path = out_docx.with_suffix(".manifest.json")

    assert out_docx.exists()
    assert out_docx.stat().st_size > 10_000
    assert manifest_path.exists()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["subsidy"]["program_id"] == "sample_hanro_kaitaku_v1"
    assert manifest["template_fill"]["missing_keys"] == []
    assert manifest["live_llm"] is False
