# Subsidy presets (optional)

**These files are no longer required**. The agent system synthesises a
SubsidyProfile on the fly from a natural-language subsidy name via
`agents/profile_synthesizer.py`, exactly as a human consultant would
research a new programme they've never seen before.

The main entry point demonstrates this:

```bash
uv run python demo/run_natural_demo.py "持続化補助金 第19回"
uv run python demo/run_natural_demo.py --live "ものづくり補助金 第18次"
uv run python demo/run_natural_demo.py --no-cache "省力化投資補助金 第2回"
```

Internally:

  1. `SubsidyDiscoverer` finds the publishing body's official URLs.
  2. `ProfileSynthesizer` decides the section structure / character limits
     / required charts and tables by reading the guideline.
  3. `profile_cache` (`.cache/profiles/<id>.json`) stores the result so
     subsequent runs are instant.

## What these YAML files are for

The files in this directory remain available as:

  * **Offline-CI fixtures** — when no `ANTHROPIC_API_KEY` or
    `PERPLEXITY_API_KEY` is configured, the synthesiser falls back to a
    generic default profile. The named presets here are richer than that
    fallback and let tests verify multi-subsidy behaviour without making
    network calls.
  * **Schema examples** — a new user reading the code can look at
    `jizoku_19_profile.yaml` to understand what the synthesiser emits.
  * **Override surface** — if you disagree with the synthesised profile
    for a given subsidy, save your own under
    `presets/<id>_profile.yaml`. The runtime will load this in
    preference to re-synthesising.

| File | Subsidy | Issuing body |
|---|---|---|
| `jizoku_19.yaml` / `jizoku_19_profile.yaml` | 小規模事業者持続化補助金 第19回 | 全国商工会連合会 / 日本商工会議所 |
| `monozukuri_v18.yaml` / `monozukuri_v18_profile.yaml` | ものづくり・商業・サービス補助金 第18次 | 全国中小企業団体中央会 |
| `shoryokuka_v2.yaml` / `shoryokuka_v2_profile.yaml` | 中小企業省力化投資補助金 第2回 | 中小企業基盤整備機構 |

The deadlines, URLs, and award amounts in these files are placeholders.
For real submission, override them with verified values from the publishing
body's website.
