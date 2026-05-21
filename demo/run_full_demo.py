"""End-to-end demo: subsidy id → fetch → research → story → assemble docx.

This is the canonical public-facing demo. Stages:

1. Resolve subsidy program via SubsidyRegistry.
2. Auto-fetch guideline PDF and form .docx files via GuidelineFetcher.
3. Research adoption examples via AdoptionResearcher.
4. Build the application story (offline mock or live Claude).
5. Validate section lengths against the SubsidyProfile.
6. Score the draft (0–100, four axes).
7. Assemble the final .docx with charts + tables placed where the profile
   declares (revenue trend after 1-2, schedule after 4-2, etc.).
8. Persist an ExecutionLog so the run feeds back into the skill store.

Output: ``demo/output/full_pipeline_application.docx`` + manifest JSON.

Run modes
---------
* ``python demo/run_full_demo.py``           offline mock LLM, no fetch / research
* ``python demo/run_full_demo.py --live``    real Claude call (needs ANTHROPIC_API_KEY)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.adoption_researcher import AdoptionResearcher  # noqa: E402
from agents.guideline_fetcher import GuidelineFetcher  # noqa: E402
from demo.mock_story import MOCK_STORY  # noqa: E402
from demo.run_demo import build_story_live  # noqa: E402
from schemas.skill import ExecutionLog  # noqa: E402
from schemas.subsidy_profile import load_profile  # noqa: E402
from schemas.subsidy_registry import YamlSubsidyRegistry  # noqa: E402
from tools.adoption_estimator import estimate_adoption_probability, format_estimate_block  # noqa: E402
from tools.document_assembler import assemble_document  # noqa: E402
from tools.length_validator import validate_lengths  # noqa: E402
from tools.refinement_loop import refine_until_threshold  # noqa: E402
from tools.skill_store import skill_store  # noqa: E402
from tools.xlsx_filler import fill_xlsx_template, write_sample_xlsx_template  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("demo")


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

    # 2. Auto-fetch guideline + forms
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

    # 4. Story building
    if live:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise SystemExit("--live requires ANTHROPIC_API_KEY")
        guideline_text = (
            ROOT / "demo" / "sample_guideline.md"
        ).read_text(encoding="utf-8")
        logger.info("calling Claude (live mode)...")
        live_story = await build_story_live(company, guideline_text)
        # Live story uses different section IDs; remap to profile IDs.
        story = {
            "section_1_1": live_story.get("company_overview", ""),
            "section_1_2": live_story.get("sales_situation", ""),
            "section_1_3": live_story.get("challenges", ""),
            "section_4_2": live_story.get("strategy", ""),
            "section_effect": live_story.get("expected_outcome", ""),
            "bonus_env_change": live_story.get("bonus_env_change", ""),
        }
        # Fill the remaining profile sections from the mock so the doc is
        # complete even if the live response is narrower.
        for sec_id, mock_text in MOCK_STORY.items():
            story.setdefault(sec_id, mock_text)
    else:
        logger.info("offline mock LLM (pass --live for real Claude call)")
        story = dict(MOCK_STORY)

    # 5. Load profile and validate
    profile = load_profile(ROOT / "demo" / "sample_profile.yaml")
    length_report = validate_lengths(profile, story)
    logger.info(
        "length compliance: %.1f%% (%d/%d chars)",
        length_report["compliance_pct"],
        length_report["total_actual_chars"],
        length_report["total_target_chars"],
    )

    # 6. Adoption-probability estimate (initial)
    initial_estimate = estimate_adoption_probability(profile, company, story)
    logger.info(
        "initial adoption probability: %d/%d %s",
        initial_estimate["total"],
        initial_estimate["target"],
        "✓" if initial_estimate["passed"] else "(refinement needed)",
    )

    # 6b. Self-improvement loop (runs only if below threshold)
    refinement = await refine_until_threshold(
        profile,
        company,
        story,
        max_iterations=4,
        mode="live" if live else "mock",
    )
    if not initial_estimate["passed"]:
        logger.info(
            "refinement: %d → %d over %d iter(s), passed=%s",
            initial_estimate["total"],
            refinement["final_score"],
            len(refinement["iterations"]),
            refinement["passed"],
        )
    story = refinement["final_story"]
    quality_report = estimate_adoption_probability(profile, company, story)

    # 7. Fill the Excel expense-detail template (format-preserving)
    expense_template = ROOT / "templates" / "sample_hanro_kaitaku_v1" / "経費明細書.xlsx"
    if not expense_template.exists():
        write_sample_xlsx_template(expense_template)
    breakdown = (company.get("expenses") or {}).get("breakdown") or []
    expense_subs: dict[str, str] = {
        "applicant_name": company["company"]["name"],
        "representative": company["company"]["representative"],
        "expense_total": f"{company['expenses'].get('total', 0):,}",
        "subsidy_amount": f"{company['expenses'].get('subsidy_amount', 0):,}",
        "self_funding": f"{company['expenses'].get('self_funding', 0):,}",
    }
    for i, item in enumerate(breakdown[:5], start=1):
        expense_subs[f"expense_{i}_category"] = item.get("category", "")
        expense_subs[f"expense_{i}_item"] = item.get("item", "")
        expense_subs[f"expense_{i}_amount"] = f"{item.get('amount', 0):,}"
        expense_subs[f"expense_{i}_note"] = item.get("note", "")
    # Pad empty rows with blanks so no {{placeholder}} survives in the output
    for i in range(len(breakdown[:5]) + 1, 6):
        for field in ("category", "item", "amount", "note"):
            expense_subs[f"expense_{i}_{field}"] = ""
    expense_xlsx = ROOT / "demo" / "output" / "経費明細書.xlsx"
    xlsx_report = fill_xlsx_template(expense_template, expense_xlsx, expense_subs)
    logger.info(
        "xlsx fill: replaced=%d, files_touched=%s, missing=%s",
        xlsx_report["replaced"],
        xlsx_report["files_touched"],
        xlsx_report["missing_keys"],
    )

    # 8. Assemble the final docx
    out_path = ROOT / "demo" / "output" / "full_pipeline_application.docx"
    assemble_result = assemble_document(
        profile=profile,
        company=company,
        story=story,
        out_path=out_path,
        extra_metadata={
            "補助金": program.canonical_name,
            "申請者": company["company"]["name"],
            "生成日時": datetime.now().isoformat(timespec="seconds"),
            "live_llm": "yes" if live else "no",
        },
        quality_block=format_estimate_block(quality_report),
    )
    logger.info(
        "assembled: charts=%s, tables=%s, size=%dB",
        assemble_result["charts_inserted"],
        assemble_result["tables_inserted"],
        out_path.stat().st_size,
    )

    # 8. Record into the skill store
    log_id = skill_store.save_execution_log(
        ExecutionLog(
            applicant_id=company.get("applicant_id", "DEMO"),
            agent_id="#13",
            input_summary=program.canonical_name,
            output_summary=(
                f"docx={out_path.name}, "
                f"length_compliance={length_report['compliance_pct']}%, "
                f"quality={quality_report['total']}"
            ),
            quality_score=quality_report["total"] / 100,
        )
    )

    # 9. Write the manifest for CI / users
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
        },
        "story_sections": list(story.keys()),
        "length_validation": length_report,
        "initial_adoption_probability": initial_estimate,
        "refinement": {
            "passed": refinement["passed"],
            "final_score": refinement["final_score"],
            "iterations": refinement["iterations"],
        },
        "quality_score": quality_report,
        "xlsx_expense_fill": xlsx_report,
        "assembly": assemble_result,
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
    print(f"   subsidy            : {program.canonical_name}")
    print(f"   docx               : {out_path.relative_to(ROOT)}")
    print(f"   docx size          : {out_path.stat().st_size:,} bytes")
    print(f"   sections           : {len(story)}")
    print(f"   chars              : {length_report['total_actual_chars']:,} / "
          f"{length_report['total_target_chars']:,} "
          f"({length_report['compliance_pct']:.0f}% compliance)")
    print(f"   charts inserted    : {', '.join(assemble_result['charts_inserted']) or '—'}")
    print(f"   tables inserted    : {', '.join(assemble_result['tables_inserted']) or '—'}")
    print(f"   adoption probability: {quality_report['total']}/100 "
          f"({'達成' if quality_report['passed'] else '未達'})")
    if refinement["iterations"]:
        print(f"   refinement         : "
              f"{initial_estimate['total']}→{refinement['final_score']} "
              f"over {len(refinement['iterations'])} iter(s)")
    print(f"   xlsx (経費明細)    : {expense_xlsx.relative_to(ROOT)} "
          f"({xlsx_report['replaced']} cells replaced, "
          f"format preserved)")
    print(f"   manifest           : {manifest_path.relative_to(ROOT)}")
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
