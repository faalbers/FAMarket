# FAMarket Screening System — Claude Session Knowledge

Audience: **Claude Code** (not the user). Read when the user explicitly asks, or when a
skill that references this doc runs (e.g. `/make_filters`) — otherwise never auto-load at
session start. Reading it reloads the full working understanding of (a) every parameter the analysis phase creates, (b) the filter
system, and (c) how to author `.filt` files correctly. This doc is a compressed cache of
knowledge derived from the codebase + external research (2026-07-28). The code remains
the source of truth — if this doc and the code disagree, the code wins; flag the drift.

Source files this doc summarizes (re-read them only when detail beyond this doc is needed):

| Topic | File |
|---|---|
| Param hints (name/category/unit/usage — canonical registry) | `config/param_hints.py` |
| Screen types + per-type metric applicability | `services/filter_registry.py` |
| Filter block model, operators, `.filt` persistence | `services/filter_engine.py` |
| Scoring rules → 0-100 goodness (the "Score" variant) | `analysis_layer/scoring_rules.py` |
| Filter build procedure, gotchas, calibration | `.claude/skills/make_filters/SKILL.md` |
| Worked `.filt` examples | `filters/*.filt` |

---

## 1. System context (minimum needed)

- `analysis.db` (table `analysis`) holds one row per symbol with ~229 columns: raw metrics,
  growth windows, peer-relative `_vs_sector`/`_vs_industry` columns, per-metric `_goodness`
  (0-100) columns, category `*_score`s, `rs_rank`, `rs_raw`, and a `screen_type` column.
- Filters run **in pandas over that one table** (`filter_engine.run_filter`): restrict rows
  to the filter's `selected_types` (via `screen_type`), then AND the blocks.
- All derived/filterable values are **precomputed and stored** in analysis.db — the filter
  engine only compares stored columns against literals or other columns; it never computes.

### Screen types (`analysis_layer/screen_type.py` keys, via sector/industry classification)

| Key | Label | Metric philosophy |
|---|---|---|
| `standard` | Common Stock — Standard | Full metric set (incl. ADRs) |
| `bank` | Bank / Financial | Debt is raw material → hide sales/EV/cash-flow multiples & debt/liquidity ratios; lead with ROE/ROA, P/B, book-value growth |
| `insurance` | Insurance | Same treatment as banks |
| `reit` | REIT | GAAP depreciation crushes earnings → hide P/E, margins, DCF; screen on P/B, leverage, revenue growth, dividends. Rides as `security_type='stock'` + Real Estate sector |
| `etf` | ETF | Only price/technicals, yield, RS rank |
| `cef` | Closed-End Fund | Same as ETF for now (premium/discount to NAV is planned) |
| `mutual_fund` | Mutual Fund | Flat daily NAV (O=H=L=C, vol=0) → no volume/ATR technicals; MAs, RSI, MACD, BB, yield still apply |
| `preferred` | Preferred Stock | Yield + div rate + technicals only (payment doesn't grow) |
| `minimal` | SPAC/Warrant/Unit/Index | Price + technicals only |

Registry groupings: `COMPANY` = {standard, bank, insurance, reit}; `FUNDS` = {etf, cef,
mutual_fund}; `ALL_TRADED` = all 9; `TRADED_NO_MF` = all but mutual_fund;
`DIVIDEND_PAYERS` = all but minimal; `FUND_DIV` = DIVIDEND_PAYERS minus preferred.

---

## 2. Unit conventions — CHECK BEFORE WRITING ANY THRESHOLD

The filter engine compares literals against **raw stored column values, no conversion**.
A wrong-scale threshold silently matches nothing (the historical `emerging_dominators`
zero-result bug). Look up `unit` in `config/param_hints.py` for every numeric block.

| `unit` | Meaning | Threshold form |
|---|---|---|
| `$` | raw dollars (market cap, price, statement items) | full magnitude: `1_000_000_000` for $1B — NEVER `1000` meaning millions |
| `%` | percent-number | `12.5` means 12.5% — NEVER `0.125` |
| `x` | ratio/multiple | `1.5`, `2.0` |
| `days` | day count | `90` |
| `yr` | year count | `5` |
| `""` | dimensionless: 0-100 scores/goodness, 1-99 rank (`rs_rank`), RSI 0-100, raw counts (`analyst_count`, `vol_20d_avg`), z-scores (`altman_z`, `beneish_m_score`), or text categories | column's natural scale |

---

## 3. Parameter reference (by category)

Format: `key` — essentials. Applicability = which screen types the Filter page offers it
for (from `filter_registry.BASES`); the stored column is simply NULL where not applicable.
Rule = default scoring-rule shape/anchor from `scoring_rules.DEFAULT_RULES` (drives the
`_goodness` "Score" variant and heatmap coloring).

### Price / Size
- `price` ($, ALL_TRADED) — adj close of last **completed** session, never intraday. Floor filter (`> 5`) drops penny stocks.
- `market_cap` ($, COMPANY) — price × shares. Band filters: micro <300M, small/mid 300M–10B, large >10B.

### Valuation (all: lower = cheaper; rule lower_better/peer unless noted)
- `pe` (x; standard/bank/insurance) — trailing P/E; `positive_only` (negative P/E = N/A not cheap).
- `forward_pe` (x; same types) — price / analyst next-12m EPS; below trailing P/E ⇒ expected growth.
- `peg` (x; same) — trailing P/E ÷ 3y EPS CAGR%. Rule: absolute anchor at **1.0** (Lynch: ≈1 fair, <1 growth at a discount). NULL when growth ≤ 0.
- `pb` (x; + REIT) — the core multiple for banks/insurers/REITs; <1 = below book, check why.
- `ps`, `p_fcf`, `ev_ebitda`, `ev_revenue` (x; **standard only**) — sales/cash/EV multiples; hidden for financials & REITs by design. EV/EBITDA rough bands: <8 cheap, >15 expensive (sector-dependent). P/FCF NULL when FCF < 0.
- `eps_ttm` ($; standard/bank/insurance) — raw earnings input; negative disables P/E, PEG, Graham, Lynch.

### Profitability (rule higher_better/peer; margins standard-only, returns incl. financials)
- `roe`, `roa` (%; standard/bank/insurance) — 15%+ sustained ROE = quality; banks: ROA ~1%+ solid. High ROE + high D/E = leverage not skill.
- `roic` (%; standard) — NOPAT / invested capital; >~10% (cost of capital) creates value. The cleanest quality metric.
- `gross_margin`, `operating_margin`, `fcf_margin` (%; standard), `net_margin` (%; + financials).
- `gross_margin_trend_3y`, `operating_margin_trend_3y` (pp change; standard; universe-ranked) — direction signals: widening = pricing power / efficiency; **N/A for pre-revenue or short-history names** (gotcha §7).
- DuPont trio (standard/bank/insurance): `asset_turnover` (x, revenue/assets — efficiency), `equity_multiplier` (x, assets/equity — 1.0 = no debt, high = leverage-inflated ROE), `roe_roa_gap` (pp — how much ROE is debt-driven; small positive = real ROE, large = leverage, negative = losses amplified).

### Balance Sheet (mostly standard-only; leverage also REIT)
- `debt_to_equity` (x; standard/reit) — <0.5 conservative, 1-2 typical, >2 leveraged; REITs high by design.
- `debt_to_ebitda` (x; standard/reit) — lender yardstick: <2 comfortable, 2-4 manageable, >4-5 strained.
- `interest_coverage` (x; standard/reit) — EBIT/interest: >5 comfortable, <2 danger (absolute zone matters more than peers).
- `current_ratio` (x; standard) — sweet spot 1.5-3; **calibrated floor for broad screens: 1.1** (retail/e-commerce run structurally below textbook 1.2 — that's model strength, not risk).
- `quick_ratio` (x; standard) — sweet spot 1.0-2.0. `cash_ratio` (x; standard) — sweet spot 0.2-0.75; 0.2-0.5 is normal, use to find fortress balance sheets.
- `altman_z` (unitless; standard) — bankruptcy risk: >3 safe, 1.8-3 grey, <1.8 distress (absolute). Designed for manufacturers; **N/A for asset-light tech/software** (gotcha §7).
- `beneish_m_score` (unitless; standard) — earnings-manipulation likelihood; **lower/more negative = cleaner** (opposite direction to Altman). Healthy ≈ -2 to -3; > **-1.78** flags likely manipulator. Treat a flag as "look closer" — fast legit growers drift upward. Research: unreliable for financial & healthcare sectors (registry already limits to standard).
- `quick_health_score` (0-7 count; standard, Common-Stock-Standard/ADR only, NaN otherwise) — 7 YoY pass/fail checks (revenue up, COGS not outgrowing revenue, gross profit up, assets > liabilities, liabilities not outgrowing assets, cash ratio ≥ 0.2, OCF trending up). Filter `>= 5` or `>= 6`, not `= 7`. Missing input = fail for that check; banks/BDCs with no COGS/gross-profit concept get whole-score NaN.

### Growth
Four growth **bases** — `revenue` (types: +REIT), `eps`, `book_value` (standard/bank/insurance), `fcf` (standard) — each expand to windows (column = `{base}_{window}`):
`cagr_1y`, `cagr_3y`, `cagr_5y`, `yoy_q` (latest quarter YoY), `growth_vol` (%, lower better), `growth_r2` (0-1 fit quality, higher better), `growth_cv` (%, lower better).
Not every base has every window (dividends have no `yoy_q`); `filter_registry.growth_windows()` is data-driven.
- `book_value` growth is THE growth metric for banks/insurers (earnings compound into book; ~10%+ + reasonable P/B = classic financial screen).
- `fcf` growth from deep annual history; feeds DCF (5y CAGR capped at 15%).
- `revenue_accel`, `eps_accel` (pp; self-relative) — latest quarter YoY minus 3y CAGR; positive = accelerating (rule: pivot at 0). Acceleration often precedes price runs (CANSLIM logic).
- `share_count_chg_1y` (%; lower better) — negative = net buybacks (per-share tailwind); filter `< 0` for shrinkers.

### Estimates (forward analyst data; COMPANY types; from signals.db `estimates` table)
- `forward_eps_growth`, `forward_rev_growth` (%) — consensus next-FY vs current-FY. Forecasts, not facts.
- `forward_peg` (x) — forward P/E ÷ next-year EPS growth; absolute anchor 1.0; NULL when growth ≤ 0. (No usable per-stock long-term growth rate from yfinance, so 1y-forward is used.)
- `eps_revision_1m`, `eps_revision_3m` (%; pivot 0) — consensus EPS estimate change over 30/90 days. **One of the strongest documented forward-return signals** (Zacks-rank family; revisions momentum). Both positive = sustained upgrade cycle.
- `eps_revision_breadth` (count; pivot 0) — analysts raising minus cutting over 30 days; filter `> 0`.
- `analyst_count` (count) — coverage depth; floor `>= 3` to trust estimate-based blocks; also the under-coverage input to `orphan_score`.

### Earnings (surprise history; COMPANY types)
- `earnings_surprise_avg`, `earnings_surprise_last` (%; pivot 0) — actual vs estimate EPS, last 4 quarters avg / most recent. Positive surprises drive **post-earnings-announcement drift** (price keeps moving in surprise direction for weeks/months — the anomaly this category exploits).
- `earnings_beat_rate` (%; universe-ranked) — share of last 4 quarters beaten; 100 = beat all 4. Consistency > single lucky beat.
- `days_to_next_earnings` (days) — **risk gate, not quality**: avoid initiating right before a report, or target post-report windows. No scoring rule (filter-only).

### Ownership (COMPANY types)
- `insider_net_buy_pct` (%; pivot 0) — net insider buying last ~6 months as % of insider-held shares. Buys are the informative side (sells have many motives). Research: abnormal returns follow net buying, strongest in small caps and banks, weaker in tech. Filter `> 0`.
- `institutions_count` (count; filter-only) — liquidity/attention context, not a buy signal; low = under-the-radar.

### Income (types vary — see registry; yield/rate all payers, payout/coverage company-only)
- `div_yield_ttm` (%) — sweet spot 2-6 (REIT override 4-10). Unusually high often = falling price (yield trap).
- `div_rate_ttm` ($) — `> 0` is the simplest "pays a dividend" filter.
- `div` growth base (%; FUND_DIV) — windows like revenue minus `yoy_q`. 5-10%+ CAGR with safe payout = dividend-growth sweet spot.
- `div_consecutive_years` (yr) — RAISING streak (not years-paid); 10/25+ = achiever/aristocrat territory; limited by our price-history depth ("at least this many").
- `div_consistency` (%) — share of YoY steps held-or-grew; softer than streak (one old cut doesn't zero it).
- `div_payout_ratio` (%) — sweet spot 30-60 (REIT override 80-95, they're legally high payers); >80 thin safety, >100 paid from reserves/debt. **Explodes for low-NI payers — prefer coverage.**
- `div_coverage` (x; anchor 1.0) — FCF / dividends paid; >2 comfortable, <1 = not cash-funded. Cash-based mirror of payout; trust it more (earnings payout can hide a 290%-payout Walgreens-style trap that FCF coverage exposes).

### Technical (ALL_TRADED; volume/ATR exclude mutual_fund)
- `ma_50/150/200` ($) — price levels; mainly used via P-mode comparisons (MA50 > MA150 > MA200 stacks a trend template).
- `price_vs_ma_50/150/200` (%; pivot 0) — distance above/below each MA. `> 0` on MA200 = the classic long-term-uptrend health gate; >15-20% above MA50 = extended.
- `pct_from_52w_high` (%; 0 = at high; pivot 0) — momentum screens want `> -15`…`> -25`; deep value hunts `< -50`.
- `pct_from_52w_low` (%; universe-ranked) — strong stocks sit `> 30` above their low.
- `rsi_14` (0-100; sweet spot 40-70) — >70 overbought, <30 oversold; confirmation not trigger for long-term screens.
- `macd_line`, `macd_signal`, `macd_hist` ($) — price-scaled, don't compare across symbols; `macd_hist` rule pivot 0.
- `macd_crossover` (text: `bullish`/`bearish`/`none` — histogram zero-cross within 5 sessions); `macd_hist_trend` (text: `growing`/`shrinking`/`flat`).
- `bb_pct` (%B; sweet spot 0.3-0.7; can exceed 0/1), `bb_position` (text: `above_upper`/`near_upper`/`middle`/`near_lower`/`below_lower`), `bb_width` ($; scales with price — use squeeze instead), `bb_squeeze` (bool-ish: tightest 20% of ~6 months = breakout setup, direction unknown; pair with trend/MACD).
- `trend` (text: `strong_uptrend`/`weak_uptrend`/`sideways`/`weak_downtrend`/`strong_downtrend`) — swing-point (peak-detection) classification over the last year.
- `vol_20d_avg` (share count) — liquidity gate, e.g. `> 100000`. `vol_ratio` (x; >2 = unusual attention). `vol_trend` (text: `increasing`/`decreasing`/`flat`).
- `atr_pct` (%; TRADED_NO_MF) — daily movement size: 1-2% calm large cap, 5%+ very volatile. **Calibrated ceiling for broad screens: 5.5** (5.0 clips solid industrials).
- `history_years` (yr, fractional) — data-quality gate: require `> 5` for 5y CAGRs to be meaningful; low = recent IPO/thin backfill.

### Intrinsic Value (standard/bank/insurance; DCF standard-only)
- `intrinsic_value_dcf` ($) — 10y FCF projection at historical CAGR (cap 15%), discount = risk-free + beta×5%, + terminal, − net debt. Most complete, most assumption-sensitive. NULL when FCF < 0.
- `intrinsic_value_graham` ($) — √(22.5 × EPS × BVPS); conservative; NULL if EPS or BV negative.
- `intrinsic_value_lynch` ($) — EPS × 3y-EPS-CAGR% (cap 25) as fair P/E (PEG=1 embodied); needs positive EPS + growth.
- `margin_of_safety` (%; pivot 0) — (avg available fair values − price)/fair × 100; 30+ = classic value bar; fewer contributing models = noisier.

### Relative Strength
- `rs_rank` (0-99; used as-is by the rule) — IBD-style weighted trailing return (four ~3-month windows, 40/20/20/20 recent-heavy), **ranked WITHIN security type** (universe is ~65% mutual funds — cross-type ranking would distort). 80+ leads; NULL under ~1y history. `rs_raw` is the persisted input (for subset-run re-ranking; not a filter metric).

### Scores (0-100; derived RESULTS of scoring rules — not rules themselves)
- `overall_score` (ALL_TRADED) — weighted blend: Quality 25, Value 22, Momentum 20, Growth 18, Income 15 (Settings-adjustable); empty categories drop out and re-weight.
- `value_score`, `quality_score` (COMPANY) — peer-relative (industry→sector).
- `growth_score` (COMPANY) — universe-ranked (rev/EPS 3y+5y CAGR, FCF 3y, latest YoYs, steadiness).
- `momentum_score` (ALL_TRADED) — RS rank + MA distances + 52w-high distance.
- `income_score` (DIVIDEND_PAYERS) — absolute income targets; NaN for non-payers.
- `orphan_score` (standard/reit only) — growth_score gated to under-covered (no estimates, or analyst_count below own peer-group median) + solvent (current_ratio ≥ 1.1) names; the "neglected firm effect"; NaN otherwise.

### Classification (text; multi-pick filters)
- `sector`, `industry` (COMPANY), `fund_family` (FUNDS; ~96% filled from fund_overview). Filtered via `is any of` / `is none of` value lists.

### Statement items (raw $ line items — Fundamentals chart only, NOT filter metrics)
`diluted_eps`, `ebitda`, `free_cash_flow` (OCF + capex — derived, yfinance's own column is sparse), `gross_profit`, `net_income`, `operating_income`, `stockholders_equity`, `total_assets`, `total_debt`, `total_revenue`.

---

## 4. Scoring rules → goodness (the "Score" filter variant)

`analysis_layer/scoring_rules.py` turns each metric into a stored 0-100 `_goodness`
column (100 = strong). A rule = **shape** + **anchor**:

- Shapes: `higher_better`, `lower_better`, `sweet_spot` (100 inside `[lo, hi]`, linear falloff scaled by 1.5×IQR fence).
- Anchors: `peer` (percentile within industry→sector→universe tiers), `universe` (percentile across all), `absolute` (a pivot `value` → 50 at the line, or a band, or used as-is when already 0-100 like `rs_rank`).
- `positive_only`: negative values masked to NaN first (negative P/E ≠ cheap).
- Sparse per-screen-type `overrides` (today: REIT yield band 4-10, REIT payout band 80-95).
- User deviations live in machine-local `scoring_rules.json`; `metric_goodness()` is the ONE code path (never reimplement strong/weak logic).
- Filter `compare: "score"` resolves to `{column}_goodness` — useful for "top decile regardless of raw scale" conditions (e.g. `pe` score ≥ 80 means "cheaper than ~80% by the rule", peer-aware, without picking a raw P/E number).

---

## 5. Filter system — block model and semantics

### Block JSON shape (`services/filter_engine.py`)

```json
{
  "enabled": true,
  "param":   "roe",            // base key from filter_registry.BASE_BY_KEY
  "window":  null,             // growth suffix for growth bases, e.g. "cagr_3y"
  "compare": "value",          // "value" | "vs_sector" | "vs_industry" | "score"
  "op":      ">",              // see operators below
  "vmode":   "V",              // "V" literal | "P" another parameter's column
  "value":   12,               // number/text; a base-key when vmode == "P"; a LIST for is any of/none of
  "vmode2":  "V", "value2": 30, // second operand, only for op == "between"
  "or_children": [ { /* same shape, no or_children nesting deeper */ } ]
}
```

### Column resolution
`resolve_column(param, window, compare)`:
- growth base + window → `{param}_{window}` (e.g. `revenue` + `cagr_3y` → `revenue_cagr_3y`)
- compare `vs_sector`/`vs_industry` → append suffix (`roe_vs_sector`); offered only when the column exists in analysis.db (data-driven)
- compare `score` → `{col}_goodness`
- P-mode (`vmode: "P"`) resolves `value` as another base key's **raw value column** — enables cross-parameter comparisons like `price > ma_200` or `ma_50 > ma_200` (golden cross), `intrinsic_value_dcf > price`.

### Operators
`>` `<` `>=` `<=` `=` `!=` `between` (two operands) | `is null` `is not null` |
`starts_with` `contains` (text) | `is any of` `is none of` (value = list; offered for
low-cardinality/categorical columns — text classifications and tiny numeric enums).
Text equality (`= bullish` on `macd_crossover`) works via `=`/`!=` on non-numeric columns.

### Evaluation semantics (critical for authoring)
1. Top-level enabled blocks **AND** together.
2. A block passes when its own condition **OR any enabled `or_children` condition** is true — children are fallbacks, one nesting level only.
3. **NULL fails every operator except `is null`/`is not null`** — including `!=` and `is none of` (forced). This is why N/A fallbacks (§7) are essential.
4. Incomplete blocks (missing value) are skipped, not failed.
5. Missing column → block condition all-False (a typo'd param silently kills all rows — dry-run catches this).
6. `run_filter` first restricts rows to `selected_types` via `screen_type`, then applies blocks.

### Applicability gating
The Filter page offers only metrics meaningful for **ALL** selected types (strict
intersection, `bases_for_types`). When authoring: choose `selected_types` first, then use
only params whose registry `applies` covers every selected type — otherwise a block
references a column that's NULL for one whole type and silently zeroes it out.

### `.filt` file format
Location: `settings.FILTERS_DIR` (project `filters/`). Plain JSON, hand-editable:

```json
{
  "version": 1,
  "saved_at": "<UTC ISO timestamp>",
  "selected_types": ["standard"],
  "comment": "<usage writeup — markdown, \\n line breaks>",
  "ai_instructions": "<verbatim origin spec — markdown, \\n line breaks>",
  "blocks": [ /* block list, no _id fields */ ]
}
```

- `comment` = travels-with-the-filter usage writeup, three sections: **What it does** /
  **How to tweak** / **How to sort for best picks** (name a primary sort + tiebreaker
  matching the thesis, AND which scores to avoid and why). Shown editable on Filter,
  read-only on Output.
- `ai_instructions` = the verbatim plain-English source spec the filter was built from
  (or a manual-build note). Read-only + collapsed in the UI. Keep distinct from comment;
  fill BOTH. Manual re-saves stamp a dated provenance note and preserve skill specs.
- Never overwrite an existing `<name>.filt` — save as `<name>_v2.filt`, `_v3`, … (next free).
- Save via `filter_engine.save_filterset_to(path, selected_types, blocks, comment, ai_instructions)`.

---

## 6. Filter authoring procedure (condensed from make_filters SKILL.md)

The `/make_filters` skill reads specs from `filters/create_filters.md`; ad-hoc chat
requests follow the same discipline. The SKILL.md is canonical for the procedure —
this is the condensed mirror:

1. Choose `selected_types`; verify every param's applicability covers them all.
2. **Clarify the brief** where it is silent on block-changing dimensions (pre-profit
   tolerance, size band, risk ceiling, liquidity floor, result size, dividend
   relevance, screen types, sort intent): at most 2-3 questions, one at a time, each
   with a proposed default. A complete brief gets zero questions.
3. Express every threshold in the param's stored unit (§2). This is the #1 failure mode.
4. Add N/A fallbacks per §7.
5. **Validate + dry-run against analysis.db BEFORE saving — never ship a zero-result
   or structurally broken filter** (one Python session does both):

```python
from core.database import Database
from config import settings
from services import filter_engine as FE
from scripts.validate_filt import validate_payload   # structural checks

types  = ["standard"]
blocks = [...]                                       # same shape as the .filt
payload = {"selected_types": types, "blocks": blocks,
           "comment": comment, "ai_instructions": ai_instructions}
errors, warns = validate_payload(payload)            # must be ZERO errors
print(errors, warns)

df = Database(settings.ANALYSIS_DB).read("analysis")
scoped = df[df["screen_type"].isin(types)]           # the rows the filter will see
running = scoped
for i, b in enumerate(blocks, 1):
    m = FE._block_mask(scoped, b)
    alone = int(m.sum()) if m is not None else len(scoped)
    rm = FE._block_mask(running, b)
    running = running[rm] if rm is not None else running
    print(f"[{i}] {b['param']}: alone={alone}  survivors={len(running)}")
print("TOTAL:", len(FE.run_filter(df, set(types), blocks)))
```

   TOTAL ≈ 0 → the block with `alone`≈0 is the culprit: wrong unit/scale, over-tight
   threshold, or missing N/A fallback. `survivors` shows which block kills the set in
   combination. Healthy screens usually land ~20-500 matches; >1000 = probably too
   loose. (Saved files re-check via `python -m scripts.validate_filt <file>` / `--all`.)
6. Fill `comment` + `ai_instructions`; version-suffix if the name exists (show the
   user a diff vs the newest existing version first); write/refresh the End Report at
   `filters/create_filters_report.md` (names, reasoning, clarification answers, sort
   guidance, dry-run counts).
7. The Output page's **🔍 Filter Fail** action (view=filter_fail) shows per-symbol
   per-block pass/fail with actual values — the calibration feedback loop. New
   findings from it get recorded in §7 below.

---

## 7. Metric gotchas — N/A traps (NULL fails everything)

**This section is the CANONICAL, LIVING home for N/A traps and calibration values.**
The `/make_filters` skill reads it (its own file stays procedure-only), and
`scripts/validate_filt.py` warns on the missing-fallback patterns below. When a
Filter Fail review reveals a new trap or a better calibration, add it HERE.

| Trap | Cause | Mandatory fix |
|---|---|---|
| `eps_cagr_1y/3y/5y` N/A | base- or current-year EPS ≤ 0 (loss↔profit transition) — drops AMZN/TSLA-style names | ALWAYS add `forward_eps_growth >= <same threshold>` as an OR child |
| `*_cagr_5y` N/A | <5y of data (recent IPOs, thin backfill) | add the `cagr_3y` variant at the same threshold as an OR child |
| `altman_z` N/A | formula needs manufacturer-style tangible assets; asset-light tech/software can't compute it | when other health blocks exist (debt_to_ebitda, interest_coverage, current_ratio), add `altman_z is null` as an OR child |
| margin/trend metrics N/A (`gross_margin_trend_3y` etc.) | pre-revenue or short history | if the brief allows not-yet-profitable names, never make these a sole hard gate — add `is null` OR child or a fallback the early-stage name can satisfy |
| `debt_to_ebitda`/`interest_coverage` N/A | no debt / no interest expense (often the HEALTHIEST names) | add `is null` OR child (see quality_compounders.filt) |
| `atr_pct` NULL for mutual funds | flat NAV, no intraday range | exclude mutual_fund from types or fallback |
| `rs_rank` NULL | <~1y price history | pair with `history_years` gate or accept the drop |

Calibrations learned from Filter Fail reviews (starting points, not hard rules):
`current_ratio` floor **1.1** for broad growth screens; `atr_pct` ceiling **5.5**.

---

## 8. Domain knowledge — how these metrics are used in practice (web research)

Compressed findings that inform threshold choices and filter design:

- **Minervini Trend Template / IBD momentum** (maps to our MA + 52w + rs_rank params):
  price > MA150 & MA200; MA150 > MA200; MA200 rising ≥ 1 month; MA50 > MA150 > MA200;
  price > MA50; ≥ 30% above 52w low; within 25% of 52w high; RS rank ≥ 70 (ideally 80-90+).
  In P-mode our engine can express the full MA stack (`ma_50 > ma_150`, etc.).
- **Estimate revisions & PEAD** (our Estimates/Earnings categories): upward EPS revisions
  are among the strongest documented short-horizon return signals (the Zacks-rank engine);
  post-earnings-announcement drift means positive surprises keep paying for weeks-months —
  `earnings_surprise_last` + `eps_revision_1m/3m` + `eps_revision_breadth` together
  replicate this factor stack.
- **Forensic scores**: Beneish M cutoff -1.78 (above = flag); unreliable for financials/
  healthcare. Altman Z zones 3 / 1.8; manufacturer-designed. Both are absolute-threshold
  metrics — never peer-compare them. Used as due-diligence gates, not ranking signals.
- **GARP / Lynch**: PEG < 1 with growth 15-30%, ROE > 15%, D/E < 0.6, current ratio > 1 is
  the canonical GARP shape; our `peg`/`forward_peg` absolute anchor at 1.0 encodes it.
- **Quality compounders**: high & durable ROIC (> cost of capital ~10%), fat stable gross
  margin (pricing power), low debt/EBITDA, steady growth (high R², low CV) — quality
  screens rarely look cheap, so sorting them by `value_score` buries the best names.
- **Insider signal**: net BUYING is the informative side (selling has many motives);
  effect strongest in small caps and banks, weakest in tech; `> 0` filter is the standard use.
- **Dividend safety**: FCF-based coverage beats earnings payout ratio (accounting-
  manipulable; the Walgreens 290%-payout aristocrat trap). Standard screen shape: yield > 2%,
  payout < 70%, 10+ year streak, coverage headroom. Long streaks alone are not safety.
- **Neglected-firm effect** (`orphan_score`): under-covered stocks can be systematically
  mispriced; requires a solvency gate so "neglected" isn't just "distressed".

---

## 9. Category → sort guidance for Output (matching filter theses)

When writing a `.filt` comment's "How to sort" section, match the sort to the thesis:

| Filter thesis | Sort primary | Tiebreak | Avoid (and why) |
|---|---|---|---|
| Growth / emerging | `growth_score` | `momentum_score` (market recognition) | `overall/quality/value_score` — reward mature/cheap names, bury early growers |
| Quality compounder | `quality_score` | `momentum_score` (hot industries) | `income_score` (not the goal), `value_score` (compounders rarely cheap) |
| Value / margin of safety | `value_score` or `margin_of_safety` | `quality_score` (avoid value traps) | `momentum_score` (value often means out of favor) |
| Income | `income_score` | `quality_score` / `div_coverage` | `growth_score` |
| Momentum / trend | `momentum_score` or `rs_rank` | `growth_score` or `eps_revision_1m` | `value_score` |
