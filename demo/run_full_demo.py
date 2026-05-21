"""End-to-end demo: subsidy id → fetched guideline → adoption research →
Claude-built story → official template filled → .docx.

Stages
------

1. Resolve a subsidy program from the registry.
2. GuidelineFetcher pulls the guideline PDF and form .docx files into a
   local cache. In the public demo these URLs are example.invalid so the
   cache placeholders are empty — the rest of the pipeline still runs.
3. AdoptionResearcher queries Perplexity for adoption examples and persists
   the findings to the skill store. Without an API key this is a no-op.
4. The story builder runs (offline mock or live Claude) and produces the
   six section bodies plus the bonus-point block.
5. The official sample template is generated from
   ``templates/build_sample_template.py`` so the demo has a real .docx with
   placeholders. The template filler substitutes the LLM output without
   reconstructing tables, fonts, or page settings — fidelity is preserved.
6. The skill store records the run and the demo prints a summary.

Run modes
---------

* ``python demo/run_full_demo.py``           offline mock LLM, no fetch / research
* ``python demo/run_full_demo.py --live``    real Claude call (needs ANTHROPIC_API_KEY)

Output
------

``demo/output/full_pipeline_application.docx`` plus a manifest JSON.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.adoption_researcher import AdoptionResearcher  # noqa: E402
from agents.guideline_fetcher import GuidelineFetcher  # noqa: E402
from demo.run_demo import MOCK_STORY, build_story_live  # noqa: E402
from schemas.skill import ExecutionLog  # noqa: E402
from schemas.subsidy_registry import YamlSubsidyRegistry  # noqa: E402
from tools.skill_store import skill_store  # noqa: E402
from tools.template_filler import fill_template  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("demo")


def _ensure_template_built() -> Path:
    """Generate the sample template on first run."""
    target = ROOT / "templates" / "sample_hanro_kaitaku_v1" / "様式2.docx"
    if not target.exists():
        subprocess.run(
            [sys.executable, str(ROOT / "templates" / "build_sample_template.py")],
            check=True,
        )
    return target


def _expense_table_csv(company: dict) -> str:
    """Flatten the expense breakdown to one line, since the sample template
    uses a 2-row table (header + body). A production template would have one
    row per item; the public demo keeps the structure minimal."""
    items = (company.get("expenses") or {}).get("breakdown", [])
    return " / ".join(
        f"{i['category']}: {i['item']} ({i['amount']:,}円)" for i in items
    )


def _substitutions(company: dict, story: dict) -> dict[str, str]:
    pl = company.get("financial", {}).get("past_3y_pl", []) or [{}, {}, {}]
    pl = (pl + [{}, {}, {}])[:3]  # pad to 3 rows
    exp = company.get("expenses") or {}
    company_block = company.get("company") or {}
    return {
        "company_name": company_block.get("name", ""),
        "representative": company_block.get("representative", ""),
        "business_address": (
            f"{company_block.get('prefecture','')}{company_block.get('city','')}"
        ),
        "employee_count": f"{company_block.get('employees', '')}名",
        "bonus_env_change": story.get("bonus_env_change", ""),
        "section_1_1": story.get("company_overview", ""),
        "section_1_2": story.get("sales_situation", ""),
        "section_1_3": story.get("challenges", ""),
        "section_4_2": story.get("strategy", ""),
        "section_effect": story.get("expected_outcome", ""),
        "pl_y1_year": str(pl[0].get("year", "")),
        "pl_y1_revenue": f"{pl[0].get('revenue', 0):,}",
        "pl_y1_profit": f"{pl[0].get('operating_profit', 0):,}",
        "pl_y2_year": str(pl[1].get("year", "")),
        "pl_y2_revenue": f"{pl[1].get('revenue', 0):,}",
        "pl_y2_profit": f"{pl[1].get('operating_profit', 0):,}",
        "pl_y3_year": str(pl[2].get("year", "")),
        "pl_y3_revenue": f"{pl[2].get('revenue', 0):,}",
        "pl_y3_profit": f"{pl[2].get('operating_profit', 0):,}",
        "expense_table_csv": _expense_table_csv(company),
        "subsidy_amount": f"{exp.get('subsidy_amount', 0):,}",
        "self_funding": f"{exp.get('self_funding', 0):,}",
    }


async def run(live: bool) -> None:
    company = yaml.safe_load(
        (ROOT / "demo" / "sample_company.yaml").read_text(encoding="utf-8")
    )

    # 1. Registry lookup
    registry = YamlSubsidyRegistry(ROOT / "demo" / "sample_registry.yaml")
    program = registry.get("sample_hanro_kaitaku_v1")
    if program is None:
        raise SystemExit("Sample program not found in registry")
    logger.info("program: %s", program.canonical_name)

    # 2. Auto-fetch guideline + forms (will fail silently on example.invalid)
    fetcher = GuidelineFetcher(registry=registry)
    fetch_manifest = await fetcher.fetch(program.program_id)
    logger.info(
        "fetched: guideline=%s, forms=%s, from_cache=%s",
        bool(fetch_manifest.get("guideline_path")),
        list(fetch_manifest.get("form_paths", {}).keys()),
        fetch_manifest.get("from_cache"),
    )

    # 3. Adoption research (no-op without PERPLEXITY_API_KEY)
    researcher = AdoptionResearcher()
    research_manifest = await researcher.research(
        program, industry=company["company"]["industry"]
    )
    logger.info(
        "adoption research: available=%s, knowledge_key=%s",
        research_manifest.get("available"),
        research_manifest.get("knowledge_key") or "—",
    )

    # 4. Story building
    if live:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise SystemExit("--live requires ANTHROPIC_API_KEY")
        guideline_text = (
            ROOT / "demo" / "sample_guideline.md"
        ).read_text(encoding="utf-8")
        logger.info("calling Claude (live mode)...")
        story = await build_story_live(company, guideline_text)
    else:
        logger.info("offline mock LLM (pass --live for real Claude call)")
        story = MOCK_STORY

    # 5. Fill the official-style template
    template_path = _ensure_template_built()
    out_path = ROOT / "demo" / "output" / "full_pipeline_application.docx"
    fill_report = fill_template(
        template_path=template_path,
        out_path=out_path,
        substitutions=_substitutions(company, story),
    )
    logger.info(
        "template fill: replaced=%s, unique=%s, missing=%s",
        fill_report["replaced"],
        fill_report["unique_keys_used"],
        fill_report["missing_keys"],
    )

    # 6. Record into the skill store so we can demonstrate "gets smarter"
    log_id = skill_store.save_execution_log(
        ExecutionLog(
            applicant_id=company.get("applicant_id", "DEMO"),
            agent_id="#13",
            input_summary=program.canonical_name,
            output_summary=f"docx={out_path.name}, sections={len(story)}",
            quality_score=None,
        )
    )

    # 7. Write a manifest for the user (and for the CI artifact)
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "subsidy": {
            "program_id": program.program_id,
            "name": program.canonical_name,
            "deadline": (
                program.application_deadline.isoformat()
                if program.application_deadline
                else None
            ),
        },
        "fetch": {
            "guideline_path": fetch_manifest.get("guideline_path", ""),
            "forms_fetched": list(fetch_manifest.get("form_paths", {}).keys()),
            "from_cache": fetch_manifest.get("from_cache", False),
        },
        "research": {
            "available": research_manifest.get("available"),
            "knowledge_key": research_manifest.get("knowledge_key"),
            "citations": research_manifest.get("citations", []),
        },
        "story_sections": list(story.keys()),
        "template_fill": fill_report,
        "output_docx": str(out_path.relative_to(ROOT)),
        "execution_log_id": log_id,
        "live_llm": live,
    }
    manifest_path = out_path.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print()
    print("============================================================")
    print(" ✓ Full pipeline complete")
    print(f"   subsidy   : {program.canonical_name}")
    print(f"   docx      : {out_path.relative_to(ROOT)}")
    print(f"   manifest  : {manifest_path.relative_to(ROOT)}")
    print(f"   live_llm  : {live}")
    print(f"   sections  : {', '.join(story.keys())}")
    print("============================================================")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Call Claude API (needs ANTHROPIC_API_KEY).",
    )
    args = parser.parse_args()
    asyncio.run(run(args.live))


if __name__ == "__main__":
    main()
