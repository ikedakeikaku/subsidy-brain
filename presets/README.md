# Subsidy presets

Starter registry entries for major Japanese SMB subsidies.

**Important:** these presets contain **structural skeletons** — the canonical
name, issuing body, expected form list, scoring axes — but the URLs are
**placeholders** that must be filled in with the actual public URLs for the
specific round you're applying to.

## Why structures, not URLs

Subsidy URLs change every round (e.g. the 持続化補助金 changes its
guideline PDF URL between 第18回 and 第19回, even though the form
structure is largely stable). Hard-coding URLs in a public OSS repo would
go stale within months.

## How to use a preset

```bash
# 1. Copy a preset to your local registry
cp presets/jizoku_19.yaml my_registry/

# 2. Open the YAML and fill in `guideline_pdf_url`, `forms[*].url`, and
#    `application_deadline` with the actual values from the official
#    公募要領 page.

# 3. Point subsidy-brain at your registry
SUBSIDY_REGISTRY=my_registry/jizoku_19.yaml uv run python demo/run_full_demo.py
```

## Or: discover them automatically

If `PERPLEXITY_API_KEY` is set, the `SubsidyDiscoverer` agent can populate
the URLs by Web search:

```bash
PERPLEXITY_API_KEY=... uv run python -m agents.subsidy_discoverer "持続化補助金 第19回"
```

Always verify the discovered URLs against the official source before
submitting.

## Available presets

| File | Subsidy | Issuing body |
|---|---|---|
| `jizoku_19.yaml` | 小規模事業者持続化補助金 第19回 | 全国商工会連合会 / 日本商工会議所 |
| `monozukuri_v18.yaml` | ものづくり・商業・サービス生産性向上促進補助金 第18次 | 全国中小企業団体中央会 |
| `it_donyu_2026.yaml` | IT導入補助金 2026年枠 | 中小企業基盤整備機構 |
| `jigyou_saikouchiku_v12.yaml` | 事業再構築補助金 第12回 | 中小企業庁 |

Each preset comes with a matching `<id>_profile.yaml` declaring the
section structure, target word counts, and required charts / tables for
that subsidy.
