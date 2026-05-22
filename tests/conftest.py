"""Shared pytest fixtures.

After the sample-data purge, tests no longer rely on hand-written
``presets/<id>_profile.yaml`` or ``demo/sample_profile.yaml``. Instead
they get a fresh ``SubsidyProfile`` from the synthesiser's fallback path,
which is deterministic and credential-free.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def sample_profile():
    """A reusable SubsidyProfile synthesised from a stable subsidy name."""
    from agents.profile_synthesizer import ProfileSynthesizer
    from config import settings as settings_mod

    saved_anthropic = settings_mod.settings.anthropic_api_key
    saved_perplexity = settings_mod.settings.perplexity_api_key
    settings_mod.settings.anthropic_api_key = ""
    settings_mod.settings.perplexity_api_key = ""
    try:
        return asyncio.run(
            ProfileSynthesizer().synthesize("テスト補助金 第1回")
        )
    finally:
        settings_mod.settings.anthropic_api_key = saved_anthropic
        settings_mod.settings.perplexity_api_key = saved_perplexity


@pytest.fixture
def sample_company():
    """Load the fictional applicant company used across the demo + tests."""
    return yaml.safe_load(
        (ROOT / "demo" / "sample_company.yaml").read_text(encoding="utf-8")
    )
