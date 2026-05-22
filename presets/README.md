# presets/ (optional override layer)

This directory is **deliberately empty** in the shipped repo.

The agent system is designed to research each subsidy from scratch, the
way a human consultant would. The natural-language entry point
``demo/run_natural_demo.py`` invokes:

  1. ``SubsidyDiscoverer`` to find the publishing body's actual URLs via
     Anthropic / Perplexity web search.
  2. ``ProfileSynthesizer`` to read the guideline and decide section
     structure, character limits, charts, tables, and 加点項目.
  3. ``GuidelineFetcher`` to download the official 様式 docx / xlsx files.
  4. ``profile_cache`` to remember the synthesised profile so subsequent
     runs are instant.

Nothing in here is required for that flow.

## When to put a file here

Two narrow cases. Both are user-curated overrides, not factory defaults:

**Hand-tuned profile.** If the synthesised profile for some subsidy is
not what you want — perhaps the publishing body's guideline left section
character limits ambiguous and the agent chose poorly — drop a YAML
named ``<program_id>_profile.yaml`` here. The pipeline will load it in
preference to running the synthesiser again.

**Hand-curated registry entry.** If you want to point the pipeline at
exact known URLs (e.g. a particular published 公募要領 PDF and its
様式 docx files) without going through web search every time, drop
``<program_id>.yaml`` here. ``YamlSubsidyRegistry`` will read it.

Both are entirely optional. The shipped CI / demo runs without either.

## Why this directory used to contain example YAMLs

Earlier iterations of this project committed hand-written stubs for
持続化補助金 第19回, ものづくり補助金 第18次, and 省力化投資補助金 第2回.
Those stubs **were guesses** — the URLs were placeholders, the character
limits were estimates, the form lists weren't verified against the
publishing body. They were misleading: a reader could believe the system
had factual knowledge about each subsidy when in fact it had a static
file someone wrote by hand.

The honest design is: **don't ship guesses**. Synthesise on demand from
research, and use a profile cache for repeat runs. If a user wants to
lock in a specific structure, they commit it here themselves with full
awareness that it's an override.
