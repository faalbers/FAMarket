---
name: make_filters
description: This skill only gets triggered with /make_filters command. ask me to continue with this skill before going ahead.
disable-model-invocation: true
---

# make_filters

Creates valid FAMarket `.filt` filter sets from the plain-English instructions in
`filters/create_filters.md`, then writes an End Report about what was created.
Follow the Procedure steps below.

## Purpose

Create FAMarket filters the user can run to find stocks in the database universe.

## When to use

Only when the user types the `/make_filters` command. (Also enforced by
`disable-model-invocation: true` in the frontmatter — never auto-trigger from
natural language.)

## Required reading — get up to date FIRST

- **Read `.claude/docs/screening_system.md` first.** It is the compressed knowledge base
  for this job: every parameter (meaning, unit, applicability, scoring rule), the
  block model + `.filt` JSON shape, the units table, the N/A gotchas + calibration
  values (§7), the practice-evidence notes (§8) and the sort-guidance table (§9).
- **The code stays the source of truth.** If the doc is missing or contradicts the
  code, fall back to `ui/filter_engine.py` (block model + persistence),
  `ui/filter_registry.py` (per-screen_type applicability),
  `analysis_layer/scoring_rules.py` (Score variant) and `config/param_hints.py`
  (units) — and tell the user the doc needs a refresh pass.
- Filter variants (Value / vs Sector / vs Industry / Score): use them where the
  instructions make them sensible.
- Filters are saved in `settings.FILTERS_DIR`.
- Web research: ONLY for stock-analysis concepts `screening_system.md` §8 does not
  already cover — don't re-research what the doc records.

## Reading create_filters.md

`filters/create_filters.md` is the briefs file this skill builds from. How to read it:

- Everything **above the first `---`** is guidance for the user writing briefs
  (format spec + a checklist of dimensions) — it is NOT a filter brief. The
  checklist mirrors **Clarify the brief** below: a dimension the brief already
  answers needs no question.
- Each `---`-delimited entry is one filter: `filter: <name>` + `instructions:`
  free text.
- A brief may contain one or more dated **"Update (…)"** sections under the
  original text. The WHOLE instructions text (original + Updates) is the spec;
  where an Update contradicts the original, **the Update wins** (it is the user's
  later clarification).
- `ai_instructions` gets the whole instructions text **verbatim — original AND
  Updates**, unedited.
- A brief that gained an Update after its filter was built is rebuilt as the next
  `_vN` version via the "All filters" scope (the save step handles versioning).

## Inputs you need from the user

- **FIRST, before anything else, ask the scope question (Procedure step 3):** build
  **only new filters** (the DEFAULT — ones whose name does not already exist in
  `settings.FILTERS_DIR`) or **all of them** (including ones that already exist).
- Then go through the chosen filters one by one. For each filter, before creating it:
  - give the user the name and a comprehensible brief of what it will do;
  - ask the clarifying questions from **Clarify the brief** below (if any apply);
  - ask whether to save it or chat about it first.

## Units — express EVERY threshold in the param's own unit (do this FIRST)

Before writing the value of ANY numeric block, look up the param's `"unit"` (in
`screening_system.md` §3 or `config/param_hints.py`). The filter engine compares your
literal against the **raw stored column value with no unit conversion**
(`ui/filter_engine.py`), so a threshold written in the wrong scale silently
matches nothing. Map the unit through this table (mirrors `screening_system.md` §2):

| `unit`   | Meaning / scale | Write a threshold as… |
|----------|-----------------|------------------------|
| `$`      | **raw dollars** (price × shares, statement items) | full magnitudes — `1_000_000_000` for $1B, **never** `1000` |
| `%`      | **percent-number** | `12.5` means 12.5% — **never** `0.125` |
| `x`      | ratio / multiple | e.g. `1.5`, `2.0` |
| `days`   | day count | e.g. `90` |
| `yr`     | year count (e.g. `div_consecutive_years`) | e.g. `5` |
| `""`     | dimensionless — a 0-100 score/`_goodness`, a 0-100/1-99 rank or indicator (`rs_rank`, `rsi_14`), a raw count (`analyst_count`), a z-score (`altman_z`), or a category string (`sector`, `trend`) | use that column's natural scale; check `what_it_is` / the data |

**Worked example — `market_cap` (`unit: "$"`):** a "$1B–$80B" band must be written as
`market_cap between 1_000_000_000 and 80_000_000_000`. Writing `between 1000 and
80000` (as if the column were in millions) means "$1,000–$80,000 of market cap" and
matches **zero** real companies — this is the exact bug that made an early
`emerging_dominators.filt` return no results.

## Metric gotchas & calibration — apply screening_system.md §7

The N/A traps (eps_cagr sign-change, 5y-history gaps, altman_z for tech, margin
trends for pre-profit names, no-debt NULLs) and the field-tested calibration values
(e.g. `current_ratio` floor 1.1, `atr_pct` ceiling 5.5) live in **one place**:
`.claude/docs/screening_system.md` §7. Apply every rule there — in particular the
mandatory OR-child fallbacks — to every filter you build. Remember the engine rule
behind them: **NULL fails every operator except `is null` / `is not null`.**

**Updating rule:** when a Filter Fail review with the user reveals a new trap or a
better calibration value, add it to `screening_system.md` §7 — NOT here. This file
stays procedure-only, so the knowledge cannot drift between two homes.

## Clarify the brief (ask BEFORE building)

A plain-English brief is usually silent on dimensions that change the blocks. After
reading a brief, scan it against this checklist:

- **Pre-profit tolerance** — should not-yet-profitable companies pass? (decides §7
  `is null` fallbacks vs hard gates on margins/EPS)
- **Size band** — market-cap floor/ceiling?
- **Risk ceiling** — max volatility (`atr_pct`)?
- **Liquidity floor** — minimum volume (`vol_20d_avg`)?
- **Result size** — broad discovery list (~100–500) or a strict shortlist (~20–100)?
- **Dividend relevance** — income goal, or ignore dividends?
- **Screen types** — standard only, or also banks / insurance / REITs?
- **Sort intent** — what should "best" mean on the Output page?

Rules:

- Ask **at most 2–3 questions per filter**, ONLY where the brief is silent AND the
  answer changes a block, threshold or fallback. A complete brief gets zero questions.
- Ask **one question at a time**, each with a proposed default so the user can just
  say yes (e.g. "The brief allows 'not yet profitable' — I'll add is-null fallbacks
  on the margin metrics. OK?"). Keep option labels short (the user's terminal cuts
  off long options).
- Record the answers in the filter's `comment` and in the End Report.
  `ai_instructions` stays the verbatim brief — never edit answers into it.

## AI instructions — ALWAYS fill the `.filt` `ai_instructions` field

Every `.filt` carries a top-level **`ai_instructions`** string (alongside `comment`,
`selected_types`, `blocks`). It round-trips through
`filter_engine.save_filterset_to` / `load_filterset_from` and shows **read-only and
collapsed** under **Comment** on both the Filter and Output pages.

Set it to the **verbatim plain-English `instructions:` text** for that specific filter
from `filters/create_filters.md` — the original spec the filter was built from,
**including any "Update (…)" sections** — as a single JSON string using `\n` for line
breaks (real newlines aren't valid in JSON).

**Keep it distinct from `comment`** — fill both, don't merge them:
- **`ai_instructions`** = the *origin spec / source ask*, copied **verbatim and unedited**.
- **`comment`** = the *usage writeup* (what it does / how to tweak / how to sort) — see below.

## Filter notes — ALWAYS fill the `.filt` `comment` field

Every `.filt` carries a free-text **`comment`** string (a top-level key in the JSON,
alongside `selected_types` and `blocks`). It round-trips through
`filter_engine.save_filterset_to` / `load_filterset_from`, shows in the Filter page's
collapsible **📝 Notes** box, and appears read-only on the **Output** page where the user
sorts and picks. **This is the canonical, travels-with-the-filter writeup** — always
populate it (the `filters/create_filters_report.md` End Report still gets written too).

Write it **dyslexia-friendly** (short sentences, bullets, **bold** anchors; see how the
user prefers docs) with these three sections, as a single JSON string using `\n` for line
breaks (real newlines aren't valid in JSON):

1. **What it does** — plain-language summary of the filter's intent + why these blocks.
   Fold in any **Clarify-the-brief answers** (the choices the user made).
2. **How to tweak** — which thresholds to loosen/tighten for which nuance (e.g. "raise the
   `market_cap` floor to skip micro-caps", "drop `rs_rank` to 50 for earlier entries"). Add
   your own suggestions where useful.
3. **How to sort for best picks** — the category score / param to **sort by** (a primary +
   a tiebreaker) to surface the strongest names, AND which to **avoid** and why. Match the
   filter's thesis — use the thesis→sort table in `screening_system.md` §9. Example for a
   growth/"emerging dominator" screen: *sort by `growth_score` (the thesis), tiebreak on
   `momentum_score` (market recognition); avoid `overall_score`, `quality_score`,
   `value_score` — they reward mature/cheap/profitable names and bury the early-stage
   growers this screen targets.*

Worked shape of the JSON value:
`"comment": "**What it does**\n<…>\n\n**How to tweak**\n- <…>\n\n**How to sort for best picks**\n- Sort by `growth_score`, tiebreak `momentum_score`\n- Avoid `overall_score`/`quality_score`/`value_score` because <…>"`

## Validate + dry-run — NEVER ship an unchecked filter

One Python session does both. Reuse the existing engine and validator — do not
reimplement either:

```python
from core.database import Database
from config import settings
from ui import filter_engine as FE
from scripts.validate_filt import validate_payload

types  = ["standard"]          # the filter's selected_types
blocks = [...]                 # the block list you built (same shape as the .filt)
payload = {"selected_types": types, "blocks": blocks,
           "comment": comment, "ai_instructions": ai_instructions}

# 1) structural validation — must return ZERO errors before anything else
errors, warns = validate_payload(payload)
print(errors, warns)

# 2) dry-run counts over the SAME rows the filter will see (scoped to its types)
df = Database(settings.ANALYSIS_DB).read("analysis")
scoped = df[df["screen_type"].isin(types)]
running = scoped
for i, b in enumerate(blocks, 1):
    m = FE._block_mask(scoped, b)
    alone = int(m.sum()) if m is not None else len(scoped)
    rm = FE._block_mask(running, b)
    running = running[rm] if rm is not None else running
    print(f"[{i}] {b['param']}: alone={alone}  survivors={len(running)}")
print("TOTAL:", len(FE.run_filter(df, set(types), blocks)))
```

(A saved file can be re-checked any time with
`python -m scripts.validate_filt path/to/file.filt`, or `--all` for every filter.)

How to read the numbers:

- **`validate_payload` errors** → fix FIRST (typo'd param, type-applicability
  mismatch, missing column, incomplete block). Warnings are worth a look — the
  unit-scale ones catch the classic $-in-millions / %-as-fraction bugs.
- **TOTAL 0 or implausibly tiny** → the block with `alone`≈0 is the culprit —
  almost always a wrong unit/scale (Units table), an over-tight threshold
  (§7 calibration), or a missing N/A fallback (§7 gotchas).
- **`survivors`** shows which block kills the set IN COMBINATION even when its
  standalone count looks healthy.
- **Healthy screens usually land ~20–500 matches.** Over ~1000 → probably too
  loose; tighten, or confirm with the user that a broad list is intended.
- Report the per-block table and the TOTAL to the user before saving.

## End Report

After creating the filters, create a full report that replaces or creates
`filters/create_filters_report.md` with the following information:

- List of all the filters created, with their names.
- A description for each filter explaining your thinking pattern during creation,
  including the Clarify-the-brief answers the user gave.
- Each filter's dry-run match count (and any block counts worth noting).
- A list of parameters the user can sort by to find the best results from that
  filter, and how to interpret them.

## Procedure

1. **Get up to date:** read `.claude/docs/screening_system.md` (see Required reading);
   fall back to the code files if it is missing or looks stale.
2. Read the plain-English instructions in `filters/create_filters.md` (per the
   **Reading create_filters.md** section — guidance header, Update sections, and
   Update-wins rule).
3. **Decide scope — ask the user FIRST, before building anything.** From the filter
   names in `create_filters.md`, list which already exist in `settings.FILTERS_DIR`
   (a `<name>.filt`, or any `<name>_v*.filt`) and which are new. Then ask the user to
   choose — make **"only new" the DEFAULT** (assume it if the user doesn't specify):
   - **Only new filters (DEFAULT)** — skip any whose name already exists; build only
     the ones that don't exist yet.
   - **All filters** — build every filter in `create_filters.md`, even ones that
     already exist (existing files are versioned per the save step, never overwritten).
   Only the chosen filters go through the rest of the procedure.
4. Web-search only the stock-analysis concepts `screening_system.md` §8 does not
   already cover.
5. For each filter: run **Clarify the brief** (max 2–3 targeted questions, one at a
   time, each with a proposed default), then present the name + brief and ask
   whether to save or chat about it first (see Inputs).
6. Build the blocks: Units table for every threshold; §7 gotcha fallbacks; §7
   calibration values as informed starting points (tighter for strict value screens,
   looser for broad growth screens, per the filter's stated intent).
7. **Validate + dry-run** (section above): zero `validate_payload` errors, then the
   per-block counts; fix and re-run until the result set is sensible. Report the
   counts to the user. Never ship a zero-result filter silently.
8. Fill the **`comment`** AND **`ai_instructions`** fields (sections above).
9. **Save with versioning.** Check whether `<name>.filt` already exists in FILTERS_DIR:
   - It does NOT exist (and no `<name>_v2.filt` etc. either) → save as `<name>.filt`.
   - It exists → find the next free version suffix (`_v2`, `_v3`, …) and save there —
     AND first show the user a short **diff vs the newest existing version** (blocks
     added/removed, thresholds changed, types changed) so they can decide later
     whether to delete the superseded file. Existing files are never overwritten.
   - The saved JSON must include the top-level `version`, `selected_types`, `blocks`,
     `comment` AND `ai_instructions` keys so everything round-trips into the
     Filter/Output Comment + AI-instructions displays.
10. Write the End Report (include each filter's dry-run match count and the
    clarification answers).
