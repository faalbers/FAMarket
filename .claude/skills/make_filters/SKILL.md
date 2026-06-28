---
name: make_filters
description: This skill only gets triggered with /make_filters command. ask me to continue with this skill before going ahead.
---

# make_filters

This skill will create valid FAMarket `.filt` filter set from plain-English instructions that you only read in dev_docs/create_filters.md
It will also create a report at the end about the created filters with
Follow the Procedure steps explained below

## Purpose

Create FAMarket filters that I can use to find stocks in the database universe.

## When to use

it is very clearly explained in the description:

## When NOT to use

if it the prompt did NOT use the skill command make_filters.

## Inputs you need from the user

List what you must know before producing output, and what to ask for if missing.
- **FIRST, before anything else, ask the scope question (Procedure step 3):** build
  **only new filters** (the DEFAULT — ones whose name does not already exist in
  `settings.FILTERS_DIR`) or **all of them** (including ones that already exist).
- go through each filter you want o create one by one and do the following things before creating them
- make sure you give me the name and a comprehensible brief of what each filter does you are about to create.
- becore creating the filter, ask me the options to save it or chat about it first.

## Reference: how filters work in this repo

First you need to prepare yourself with the following steps:
- Pull the real details from the codebase so the skill stays accurate.
- You will first figure out all the filter system in the project and all the ways you can create filters using available filter parameters.
- You will figure out what all these filter parameters mean and what they represent.
- You will look at the analysis_layer scoring_rules how to interprete these filters well.
- You will also have a total understanding on the Category Scores and how they are created and how they are used in the filter system.
- Block model + `.filt` JSON: `ui/filter_engine.py`
- Per-`screen_type` metric applicability: `ui/filter_registry.py`
- Filter variants (Value / vs Sector / vs Industry / Score): Make sure to use these if it makes sense per instructions
- Where filters are saved: `settings.FILTERS_DIR`

## Units — express EVERY threshold in the param's own unit (do this FIRST)

Before writing the value of ANY numeric block, look up the param in
`config/param_hints.py` and read its `"unit"`. The filter engine compares your
literal against the **raw stored column value with no unit conversion**
(`ui/filter_engine.py`), so a threshold written in the wrong scale silently
matches nothing. Map the unit through this table:

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

## Metric gotchas

**eps_cagr_1y / 3y / 5y — sign-change gives N/A**
These metrics return N/A when either the base-year or current-year EPS is ≤ 0
(a loss-to-profit or profit-to-loss transition makes the CAGR undefined).
This means the block silently fails for companies that were previously loss-making,
even if they are now highly profitable (e.g. AMZN, TSLA).

Rule: whenever you write a block using any `eps_cagr_*` metric, always add
`forward_eps_growth` at the same threshold as an OR child. This lets companies
that cannot show historical EPS CAGR pass on analyst consensus for future EPS
growth instead.

Example — instead of:
  eps_cagr_3y >= 10  (no fallback)

Write:
  eps_cagr_3y >= 10  OR  forward_eps_growth >= 10

**revenue_cagr_5y / eps_cagr_5y — missing history gives N/A**
5-year CAGR metrics need 5+ years of annual data. Recent IPOs or data gaps produce
N/A, which silently fails the block (same symptom as the eps sign-change issue, but
caused by short history, not a sign change).

Rule: whenever you write a block using any 5y CAGR metric, add the 3y variant at
the same threshold as an OR child.

Example:
  revenue_cagr_5y >= 7  OR  revenue_cagr_3y >= 7

**altman_z — N/A for tech/software companies**
Altman Z-score was designed for manufacturing companies. It is not computable for
asset-light tech, software, or streaming businesses (the formula relies on tangible-
asset ratios that don't apply), so it returns N/A and silently fails those stocks
even when they are financially healthy by every other measure.

Rule: if the filter already includes other financial health blocks (debt_to_ebitda,
interest_coverage, current_ratio), add `altman_z is null` as an OR child. Companies
for which the metric is inapplicable are not penalised; the other blocks still guard
financial health.

Example:
  altman_z >= 2.5  OR  altman_z is null

**Trend / margin metrics — N/A for pre-revenue or short-history names**
Margin- and trend-based metrics (e.g. `gross_margin_trend_3y`, and margin trends
generally) return N/A when a company has no revenue/margin yet or too little
history to compute a 3-year trend. Because NULL fails every operator, a hard block
on one of these silently drops exactly the early-stage "not yet profitable" names
that growth/emerging theses are meant to catch (this is what dropped BEAM from
`emerging_dominators`).

Rule: whenever a brief explicitly allows not-yet-profitable companies, never make a
trend/margin metric a sole hard gate. Pair it with an `OR <metric> is null` child
(or an `OR <fallback>` on a metric the early-stage name can satisfy) so a
legitimately pre-margin company isn't excluded.

Example:
  gross_margin_trend_3y >= 0  OR  gross_margin_trend_3y is null

## Calibration guidance

This section records threshold values that turned out to be too tight in practice,
discovered by reviewing Filter Fail results together. It will be updated over time
as new findings come up — if a future Filter Fail review reveals another param that
needs a better default, add it here.

Use these as informed starting points, not hard rules. Adjust up or down based on
the filter's stated intent (a strict value screen may want tighter values; a broad
growth screen should use the looser ones below).

**current_ratio**
Textbook floor is 1.2, but retailers and e-commerce companies (e.g. AMZN) structurally
run below this — they collect cash from customers before paying suppliers, so a low
current ratio is a sign of business model strength, not risk.
Recommended floor for broad growth screens: **1.1**

**atr_pct**
A 5.0% ceiling is very tight and catches solid industrials and construction companies
by small margins (e.g. FIX at 5.08%). These are not high-risk stocks — the ATR just
reflects normal sector volatility.
Recommended ceiling for broad growth screens: **5.5%**

## AI instructions — ALWAYS fill the `.filt` `ai_instructions` field

Every `.filt` carries a top-level **`ai_instructions`** string (alongside `comment`,
`selected_types`, `blocks`). It round-trips through
`filter_engine.save_filterset_to` / `load_filterset_from` and shows **read-only and
collapsed** under **Comment** on both the Filter and Output pages.

Set it to the **verbatim plain-English `instructions:` text** for that specific filter from
`dev_docs/create_filters.md` — the original spec the filter was built from — as a single
JSON string using `\n` for line breaks (real newlines aren't valid in JSON).

**Keep it distinct from `comment`** — fill both, don't merge them:
- **`ai_instructions`** = the *origin spec / source ask*, copied **verbatim and unedited**.
- **`comment`** = the *usage writeup* (what it does / how to tweak / how to sort) — see below.

## Filter notes — ALWAYS fill the `.filt` `comment` field

Every `.filt` carries a free-text **`comment`** string (a top-level key in the JSON,
alongside `selected_types` and `blocks`). It round-trips through
`filter_engine.save_filterset_to` / `load_filterset_from`, shows in the Filter page's
collapsible **📝 Notes** box, and appears read-only on the **Output** page where the user
sorts and picks. **This is the canonical, travels-with-the-filter writeup** — always
populate it (the `dev_docs/filters_report.md` End Report still gets written too).

Write it **dyslexia-friendly** (short sentences, bullets, **bold** anchors; see how the
user prefers docs) with these three sections, as a single JSON string using `\n` for line
breaks (real newlines aren't valid in JSON):

1. **What it does** — plain-language summary of the filter's intent + why these blocks.
2. **How to tweak** — which thresholds to loosen/tighten for which nuance (e.g. "raise the
   `market_cap` floor to skip micro-caps", "drop `rs_rank` to 50 for earlier entries"). Add
   your own suggestions where useful.
3. **How to sort for best picks** — the category score / param to **sort by** (a primary +
   a tiebreaker) to surface the strongest names, AND which to **avoid** and why. Match the
   filter's thesis. Example for a growth/"emerging dominator" screen: *sort by `growth_score`
   (the thesis), tiebreak on `momentum_score` (market recognition); avoid `overall_score`,
   `quality_score`, `value_score` — they reward mature/cheap/profitable names and bury the
   early-stage growers this screen targets.*

Worked shape of the JSON value:
`"comment": "**What it does**\n<…>\n\n**How to tweak**\n- <…>\n\n**How to sort for best picks**\n- Sort by `growth_score`, tiebreak `momentum_score`\n- Avoid `overall_score`/`quality_score`/`value_score` because <…>"`

## End Report

After creating the filters, create a full report that replaces or creates a file under dev_docs/filters_report.md with the following information:
- List of all the filters created with their names
- Add a description for each filter explaining what your thinking pattern was during creation.
- Add a list of parameters that i can sort to find the best results from that filter and how I should interprete these parameters.

## Procedure

1. Go through the reference so you get up to date first on what you need to know.
2. Read the plain-English instructions that you only read in dev_docs/create_filters.md to create the filters
3. **Decide scope — ask the user FIRST, before building anything.** From the filter
   names in `create_filters.md`, list which already exist in `settings.FILTERS_DIR`
   (a `<name>.filt`, or any `<name>_v*.filt`) and which are new. Then ask the user to
   choose — make **"only new" the DEFAULT** (assume it if the user doesn't specify):
   - **Only new filters (DEFAULT)** — skip any whose name already exists; build only
     the ones that don't exist yet.
   - **All filters** — build every filter in `create_filters.md`, even ones that
     already exist (existing files are versioned per the save step, never overwritten).
   Only the chosen filters go through the rest of the procedure.
4. Find all the stock market analysis knowledge you can find with web search to get the best results on the instructions in create_filters.md
5. Go through Inputs you need from the user explained above before creating the filters
6. **Dry-run every filter against `analysis.db` BEFORE saving it — never ship a
   zero-result filter silently.** Reuse the existing engine (do not reimplement it):

   ```python
   from core.database import Database
   from config import settings
   from ui import filter_engine as FE

   df = Database(settings.ANALYSIS_DB).read("analysis")          # full universe
   blocks = [...]            # the block list you just built (same shape as the .filt)
   types  = ["standard"]     # the filter's selected_types

   total = len(FE.run_filter(df, set(types), blocks))            # final match count
   scoped = df  # optionally restrict to `types` first via run_filter's logic
   for i, b in enumerate(blocks, 1):                             # per-block standalone count
       m = FE._block_mask(scoped, b)
       n = int(m.sum()) if m is not None else len(scoped)
       print(f"[{i}] {b['param']}: {n} match")
   print("TOTAL:", total)
   ```

   Report the final match count and the per-block counts to the user. If the total is
   **0 or implausibly tiny**, find the block whose standalone count is ~0 — that block
   is the culprit. Fix it (almost always a wrong **unit/scale** per the Units table, or
   an over-tight threshold per Calibration guidance, or a missing N/A fallback) and
   re-run the dry-run until it returns a sensible set. Only then proceed to save.
7. **Fill the `comment` AND `ai_instructions` fields** for each filter:
   - `comment` (see "Filter notes" above) — what it does, how to tweak, how to sort for
     best picks — as a `\n`-delimited JSON string.
   - `ai_instructions` (see "AI instructions" above) — that filter's **verbatim**
     `instructions:` text from `create_filters.md`, as a `\n`-delimited JSON string.
8. Before saving, check whether `<name>.filt` already exists in FILTERS_DIR.
   - If it does NOT exist (and no `<name>_v2.filt` etc. exist either): save as `<name>.filt`.
   - If `<name>.filt` exists: find the next free version suffix (`_v2`, `_v3`, …) and save there.
   - The saved JSON must include the top-level `comment` AND `ai_instructions` keys (and
     `version`, `selected_types`, `blocks`) so both round-trip into the Filter/Output
     Comment + AI-instructions displays.
   Then write the End Report (include the dry-run match count for each filter).

