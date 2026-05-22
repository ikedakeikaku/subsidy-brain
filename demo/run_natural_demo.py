"""Natural-language entry point: "make me an application for X subsidy".

The user passes a subsidy name (or ID) on the command line. The pipeline
then does what a human consultant would do without prior memorisation:

  1. ProfileSynthesizer asks Claude (with web search) what shape the
     application should have for this subsidy. Returns a SubsidyProfile.
  2. profile_cache stores the synthesised profile so subsequent runs are
     instantaneous and free.
  3. SubsidyDiscoverer finds the publishing body's actual form URLs
     (optional; falls through silently if web search unavailable).
  4. GuidelineFetcher pulls the guideline PDF + 様式 docx/xlsx files into
     a local cache.
  5. AdoptionResearcher accumulates adoption-case knowledge for the
     industry under inspection.
  6. TemplateSynthesizer picks an official template if downloaded, else
     synthesises one from the profile.
  7. The application story is built (live Claude in ``--live`` mode,
     deterministic mock otherwise).
  8. RefinementLoop keeps editing weak sections until the adoption
     estimator clears the profile's quality threshold.
  9. document_assembler renders the final .docx with charts and tables.

No ``presets/<id>.yaml`` is required for any of this — every fact the
agent uses about the subsidy comes from research or from sensible defaults.
``presets/`` remains available as optional curated examples and as the
fallback in fully-offline CI runs.

Usage::

    uv run python demo/run_natural_demo.py "持続化補助金 第19回"
    uv run python demo/run_natural_demo.py --live "ものづくり補助金 第18次"
    uv run python demo/run_natural_demo.py --no-cache "省力化投資補助金"
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
from agents.bonus_evaluator import BonusEvaluator  # noqa: E402
from agents.guideline_fetcher import GuidelineFetcher  # noqa: E402
from agents.profile_synthesizer import ProfileSynthesizer  # noqa: E402
from agents.subsidy_discoverer import discover_subsidy  # noqa: E402
from agents.template_synthesizer import TemplateSynthesizer  # noqa: E402
from demo.mock_story import MOCK_STORY  # noqa: E402
from demo.story_builder_live import build_story_live  # noqa: E402
from schemas.skill import ExecutionLog  # noqa: E402
from schemas.subsidy_registry import (  # noqa: E402
    SubsidyProgram,
    YamlSubsidyRegistry,
)
from tools.adoption_estimator import estimate_adoption_probability  # noqa: E402
from tools.document_assembler import assemble_document  # noqa: E402
from tools.length_validator import validate_lengths  # noqa: E402
from tools.profile_cache import profile_cache  # noqa: E402
from tools.refinement_loop import refine_until_threshold  # noqa: E402
from tools.skill_store import skill_store  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("nl_demo")


async def run(query: str, *, live: bool, use_cache: bool) -> None:
    company = yaml.safe_load(
        (ROOT / "demo" / "sample_company.yaml").read_text(encoding="utf-8")
    )

    # ----- 1. Try to discover the actual subsidy URLs --------------------
    program = await discover_subsidy(query)
    if program is None:
        # No web-search credential — fall back to a minimal SubsidyProgram
        # so the rest of the pipeline still has a target to point at.
        program = SubsidyProgram(
            program_id=_slug(query),
            canonical_name=query,
            round_number=0,
            issuing_body="（要確認）",
            landing_url=None,
            guideline_pdf_url=None,
            forms=[],
            keywords_for_research=[query],
        )
        logger.info(
            "discoverer: no web result; using bare program shell for %s", query
        )
    logger.info(
        "discoverer: %s (forms=%d)", program.canonical_name, len(program.forms)
    )

    # ----- 2. Synthesize SubsidyProfile (the real "judgment" step) -------
    profile = profile_cache.load(program.program_id) if use_cache else None
    if profile is None:
        profile = await ProfileSynthesizer().synthesize(program.canonical_name)
        # Lock the profile to the same program_id so caches line up
        profile = profile.model_copy(update={"program_id": program.program_id})
        profile_cache.save(profile)
    logger.info(
        "profile: sections=%d, charts=%d, tables=%d, target=%d字",
        len(profile.sections),
        len(profile.charts),
        len(profile.tables),
        profile.total_target_chars,
    )

    # ----- 3. Fetch official guideline + forms ---------------------------
    fetch_manifest: dict = {}
    if program.guideline_pdf_url or program.forms:
        # GuidelineFetcher wants a registry to look up. Build an in-memory
        # one-program registry for the call.
        reg_path = Path(".cache/tmp_registry.yaml")
        reg_path.parent.mkdir(parents=True, exist_ok=True)
        reg_path.write_text(
            yaml.safe_dump(
                {"programs": [program.model_dump(mode="json")]},
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        fetcher = GuidelineFetcher(registry=YamlSubsidyRegistry(reg_path))
        fetch_manifest = await fetcher.fetch(program.program_id)
    else:
        logger.info("fetcher: no URLs in program shell; skipping")

    # ----- 4. Research adoption examples ---------------------------------
    research = await AdoptionResearcher().research(
        program, industry=company["company"]["industry"]
    )

    # ----- 5. Build the story --------------------------------------------
    if live:
        from config.settings import settings
        if not (settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")):
            raise SystemExit(
                "--live requires ANTHROPIC_API_KEY (in .env or environment)"
            )
        # Use the discovered guideline text if we have it, else a short
        # research summary fetched via AdoptionResearcher.
        guideline_text = (
            research.get("findings", "")
            or f"対象補助金: {program.canonical_name}（最新の公募要領を参照してください）"
        )
        logger.info("story: calling Claude (live)")
        live_story = await build_story_live(company, guideline_text)
        story = {
            "section_1_1": live_story.get("company_overview", ""),
            "section_1_2": live_story.get("sales_situation", ""),
            "section_1_3": live_story.get("challenges", ""),
            "section_4_2": live_story.get("strategy", ""),
            "section_effect": live_story.get("expected_outcome", ""),
            "bonus_env_change": live_story.get("bonus_env_change", ""),
        }
        for sec_id, txt in MOCK_STORY.items():
            story.setdefault(sec_id, txt)
    else:
        logger.info("story: offline mock LLM (pass --live for real Claude)")
        story = dict(MOCK_STORY)
    # Make sure every section the synthesised profile expects has *some*
    # text — backfill with the mock equivalent if missing.
    for spec in profile.sections:
        story.setdefault(spec.section_id, MOCK_STORY.get(spec.section_id, ""))

    # ----- 5b. Per-subsidy bonus-item evaluation ------------------------
    # profile.bonus_items differs by subsidy (持続化補助金 has 事業環境変化,
    # ものづくり has 賃上げ・サイバーセキュリティ・知的財産, 省力化 has
    # 賃上げ・大幅賃上げ・地域経済牽引, etc.). Generate the right body
    # text per item, applying only the items this company qualifies for.
    bonus_results = await BonusEvaluator().evaluate(
        profile, company, mode="live" if live else "mock"
    )
    for r in bonus_results:
        if r.applicable and r.body_text:
            story[f"bonus_{r.item_id}"] = r.body_text
    logger.info(
        "bonus_items: %d total / %d applicable",
        len(bonus_results),
        sum(1 for r in bonus_results if r.applicable),
    )

    length_report = validate_lengths(profile, story)
    initial = estimate_adoption_probability(profile, company, story)

    # ----- 6. Refinement loop --------------------------------------------
    refinement = await refine_until_threshold(
        profile, company, story,
        max_iterations=4,
        mode="live" if live else "mock",
    )
    story = refinement["final_story"]
    final_estimate = estimate_adoption_probability(profile, company, story)

    # ----- 7. Pick / synthesise the template -----------------------------
    # The official 様式 docx is used if GuidelineFetcher actually downloaded
    # it from the publishing body. Otherwise we either use a hand-curated
    # template the user committed under templates/<program_id>/ or
    # synthesise a clearly-labelled DRAFT skeleton from the profile.
    template_path, template_source = TemplateSynthesizer().get_template(
        profile,
        fetched_form_paths=fetch_manifest.get("form_paths", {}),
        templates_root="templates",
    )
    logger.info("template: %s (source=%s)", template_path, template_source)

    # ----- 8. Assemble the final docx ------------------------------------
    out_path = ROOT / "demo" / "output" / f"{program.program_id}_application.docx"

    # When the template is a real official 様式 (or a user-committed local
    # one), fill it preserving all formatting via OfficialFormFiller.
    # Otherwise build the document from the profile via document_assembler.
    if template_source in {"official", "local"}:
        from tools.official_form_filler import fill_official_form
        fill_report = fill_official_form(
            template_path=template_path,
            out_path=out_path,
            profile=profile,
            story=story,
            company=company,
        )
        assemble_result = {
            "output": str(out_path),
            "sections_rendered": fill_report["sections_filled"],
            "charts_inserted": fill_report.get("charts_appended", []),
            "tables_inserted": fill_report["tables_appended"],
            "fill_method": "official_form_filler",
            "template_source": template_source,
            "sections_not_found": fill_report["sections_not_found"],
            "applicant_cells_filled": fill_report["applicant_cells_filled"],
        }
    else:
        assemble_result = assemble_document(
            profile=profile,
            company=company,
            story=story,
            out_path=out_path,
            extra_metadata={
                "補助金": profile.canonical_name,
                "申請者": company["company"]["name"],
                "生成日時": datetime.now().isoformat(timespec="seconds"),
                "live_llm": "yes" if live else "no",
            },
            quality_block=None,
        )
        assemble_result["fill_method"] = "document_assembler"
        assemble_result["template_source"] = template_source

    # ----- 9. Log the run ------------------------------------------------
    log_id = skill_store.save_execution_log(
        ExecutionLog(
            applicant_id=company.get("applicant_id", "DEMO"),
            agent_id="#nl",
            input_summary=query,
            output_summary=(
                f"docx={out_path.name}, "
                f"length={length_report['compliance_pct']}%, "
                f"score={final_estimate['total']}"
            ),
            quality_score=final_estimate["total"] / 100,
        )
    )

    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "query": query,
        "subsidy": {
            "program_id": program.program_id,
            "canonical_name": profile.canonical_name,
        },
        "profile": {
            "sections": len(profile.sections),
            "charts": len(profile.charts),
            "tables": len(profile.tables),
            "total_target_chars": profile.total_target_chars,
            "source": "synthesized" if not use_cache or not profile_cache.load(
                program.program_id
            ) else "cache",
        },
        "fetch": fetch_manifest,
        "research": research,
        "length_validation": length_report,
        "initial_estimate": initial,
        "refinement": {
            "passed": refinement["passed"],
            "final_score": refinement["final_score"],
            "iterations": refinement["iterations"],
        },
        "final_estimate": final_estimate,
        "assembly": assemble_result,
        "execution_log_id": log_id,
        "live_llm": live,
    }
    manifest_path = out_path.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    print()
    print("============================================================")
    print(" ✓ Natural-language pipeline complete")
    print(f"   query              : {query!r}")
    print(f"   resolved subsidy   : {profile.canonical_name}")
    print(f"   profile source     : "
          f"{'cache' if (use_cache and profile_cache.load(program.program_id)) else 'synthesized'}")
    print(f"   docx               : {out_path.relative_to(ROOT)}")
    print(f"   docx size          : {out_path.stat().st_size:,} bytes")
    print(f"   sections           : {len(profile.sections)}")
    print(f"   chars              : {length_report['total_actual_chars']:,} / "
          f"{length_report['total_target_chars']:,} "
          f"({length_report['compliance_pct']:.0f}% compliance)")
    print(f"   charts inserted    : "
          f"{', '.join(assemble_result['charts_inserted']) or '—'}")
    print(f"   tables inserted    : "
          f"{', '.join(assemble_result['tables_inserted']) or '—'}")
    print(f"   fill method        : {assemble_result.get('fill_method', '—')}")
    print(f"   template source    : {assemble_result.get('template_source', '—')}")
    print(f"   adoption probability: {final_estimate['total']}/100 "
          f"({'達成' if final_estimate['passed'] else '未達'})")
    if refinement["iterations"]:
        print(f"   refinement         : "
              f"{initial['total']}→{final_estimate['total']} "
              f"over {len(refinement['iterations'])} iter(s)")
    print(f"   manifest           : {manifest_path.relative_to(ROOT)}")
    print("============================================================")


def _slug(name: str) -> str:
    import re
    return re.sub(r"\W+", "_", name.lower()).strip("_") or "subsidy"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="補助金の名称（例：持続化補助金 第19回）")
    parser.add_argument("--live", action="store_true", help="実Claude呼び出し")
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="profile_cache を無視して毎回 synthesize する",
    )
    args = parser.parse_args()
    asyncio.run(run(args.query, live=args.live, use_cache=not args.no_cache))


if __name__ == "__main__":
    main()
