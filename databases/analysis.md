# analysis.db — computed screening metrics, one row per security

The main analysis output: for every analysable security, ~290 columns of valuation, profitability, health, growth, dividend, technical, intrinsic-value, analyst and ranking metrics.
All values are computed from the other databases. Rebuilt from scratch on each full run, so it is a snapshot "as of" one price date.

Join key: `symbol` (see `symbols.md` for shared conventions). Snapshot when written (2026-11-09): ~38.6k rows, prices as of 2026-11-06.

## Table `analysis_meta` — one row

- **`analyzed_at`** (UTC build time), **`prices_as_of`** (the price date every metric uses), **`n_symbols`**.

## Table `analysis` — one row per symbol

**Who is in it**: symbols with `is_active = 1` and `is_validated = 1` in `symbols.db`, excluding indices. That includes funds, ETFs, warrants etc. — most fundamental columns are simply NULL for them. Around 60% of rows are mutual funds.

## Global conventions

- **Price** = `adj_close` of the last completed trading session (`ohlcv.db`). Never an intraday price.
- **TTM** = sum of the last 4 **consecutive** quarters (no gap over ~120 days), else NULL. Balance-sheet items = latest reported value (quarterly, falling back to annual).
- **Percent metrics are stored as percent numbers** (12.5 = 12.5%). Multiples (P/E, ratios marked "x") are plain numbers.
- **Market cap** = price × shares, where shares = Yahoo `sharesOutstanding`, else the latest reported `ordinary_shares_number`, else `share_issued`.
- **NULL = not applicable or not computable** (missing inputs, wrong security type, sign change, outlier guard). It is never a hidden zero.
- **Currency guard** — `valuation_basis`:
  - `computed` = ratios computed from the statements in `financials.db` (the normal case).
  - `yfinance` = the company reports in a different currency than its price (foreign filers). Price-based ratios then fall back to Yahoo's own (`trailingPE`, `priceToBook`, …), `p_fcf` is NULL, and all intrinsic values, `altman_z` and `wacc` are NULL.
- **Gates by `screen_type`**: some metrics are only meaningful for ordinary operating companies and are NULL for banks, insurers, REITs and funds (noted below as "standard only").

## 1. Identity columns

- **`symbol`**, **`name`**, **`security_type`** (from `symbols.db`).
- **`screen_type`** — the grouping used for screening:
  - stock/adr split by Yahoo sector/industry: `reit` (sector Real Estate or industry contains "REIT"), `bank` (industry contains "bank", "mortgage finance" or "capital markets"), `insurance` (industry contains "insurance"), else `standard`.
  - `etf`, `mutual_fund`, `closed_end_fund`, `preferred` map directly; `spac`/`warrant`/`unit` → `minimal`.
- **`sector`**, **`industry`**, **`country`** — Yahoo tags from `quotes.db`. **`fund_family`** — fund sponsor (funds only).
- **`price`** — the canonical price (above).
- **`rs_raw`** — weighted trailing return, the input to `rs_rank` (see 9).

## 2. Valuation (TTM)

- **`market_cap`**.
- **`pe`** = market cap / TTM net income. **`ps`** = / TTM revenue. **`pb`** = / latest stockholders' equity. **`p_fcf`** = / TTM free cash flow.
- **`ev_ebitda`**, **`ev_revenue`** — enterprise value = market cap + total debt − cash.
- **`eps_ttm`** = TTM net income / shares.
- **`forward_pe`** = price / Yahoo `forwardEps`.
- **`peg`** = `pe` / `eps_growth_trend` (only when growth > 0).
- Negative multiples are kept here (a loss gives a negative P/E). Peer comparisons and scores ignore non-positive P/E, P/B, P/S, P/FCF, EV/EBITDA, debt ratios.

## 3. Profitability and returns (TTM, percent)

- **`gross_margin`**, **`operating_margin`**, **`net_margin`**, **`fcf_margin`** — over TTM revenue.
- **`roe`** = TTM net income / equity. **`roa`** = / total assets.
- **`asset_turnover`** (revenue / assets, x), **`equity_multiplier`** (assets / equity, x), **`roe_roa_gap`** (percentage points of ROE coming from leverage).
- **`roic`** = NOPAT / invested capital; NOPAT = TTM operating income × (1 − effective tax rate, clipped 0-100%). NULL when invested capital ≤ 0 or |ROIC| > 500%.
- **`wacc`** (standard only, percent) — CAPM: cost of equity = 10y Treasury + beta × 5%; cost of debt = TTM interest expense / total debt × (1 − tax). NULL when beta is missing or |beta| > 10, or cost of debt > 50%.
- **`roic_vs_wacc`** — ROIC − WACC (pp); > 0 = creating value. **`roic_vs_wacc_5y`** — median annual ROIC of the last 5 years − today's WACC. **`roic_trend_3y`** — 3-year change in annual ROIC (pp). All standard only.
- **`ocf_to_ni_3y`**, **`ocf_to_ni_5y`** (standard only) — average operating cash flow / net income over the last 3 / 5 fiscal years, in percent. Loss years and years above 500% are excluded; needs 2+ years. ~100%+ = earnings backed by cash.
- **`gross_margin_trend_3y`**, **`operating_margin_trend_3y`** — change in annual margin over ~3 years (pp).

## 4. Financial health

- **`debt_to_equity`** (x), **`debt_to_ebitda`** (x), **`interest_coverage`** = TTM operating income / |TTM interest expense| (x).
- **`current_ratio`**, **`quick_ratio`** (cash + short-term investments + receivables, or current assets − inventory, over current liabilities), **`cash_ratio`**.
- **`altman_z`** — Altman Z-score, manufacturing form: 1.2·WC/TA + 1.4·RE/TA + 3.3·EBIT/TA + 0.6·MarketCap/Liabilities + 1.0·Sales/TA. Above ~3 safe, below ~1.8 distressed. NULL for large profitable firms whose retained earnings are deeply negative from buybacks.
- **`beneish_m_score`** — Beneish M-score from 8 year-over-year ratios of the last two fiscal years. Above −1.78 = possible earnings manipulation. NULL when |score| > 10.
- **`quick_health_score`** (standard only, 0-7) — count of passed checks, last fiscal year vs the one before: revenue up; cost of revenue not outgrowing revenue; gross profit up; assets > liabilities; liabilities not outgrowing assets; cash ratio ≥ 0.2; operating cash flow up.

## 5. Growth

Four series — **revenue**, **eps** (diluted EPS, split-adjusted with `ohlcv.splits`), **fcf** (annual operating cash flow + capex), **book_value** (stockholders' equity) — each get:

- **`<x>_cagr_1y / _3y / _5y`** — compound annual growth (%) from the annual value ~N years back to the latest. NULL if either end is ≤ 0 or no point that far back.
- **`<x>_growth_trend`** — annualized growth (%) from a log-linear fit over the last up to 5 annual points (all must be > 0).
- **`<x>_growth_vol`** — residual volatility around a linear fit, % of the mean (lower = steadier).
- **`<x>_growth_r2`** — R² of that linear fit (1 = perfectly steady trend).
- **`<x>_growth_cv`** — coefficient of variation, % (std / |mean|).
- **`<x>_yoy_q`** — latest quarter vs the same quarter a year earlier (%).

Plus:

- **`revenue_accel`**, **`eps_accel`** — `yoy_q` − `cagr_3y` (pp); > 0 = growing faster than its own 3-year pace.
- **`share_count_chg_1y`** — 1-year % change in diluted average shares (negative = buybacks).

## 6. Dividends (from `ohlcv.dividends` history)

- **`div_rate_ttm`** — dividends per share with ex-date in the last 365 days. **`div_yield_ttm`** = that / price (%). Non-payers get 0 for both and NULL for the rest.
- Trend metrics use each **complete calendar year's highest single payment** (not the yearly sum, which jumps with how many payments fall in a year):
  - **`div_cagr_1y / _3y / _5y`** (%), **`div_growth_trend`**, **`div_growth_vol`**, **`div_growth_r2`**, **`div_growth_cv`** — as in Growth.
  - **`div_consecutive_years`** — trailing streak of years the payment did not fall (a "not cut" streak, not years paid).
  - **`div_consistency`** — % of year-to-year steps that did not fall.
- **`div_payout_ratio`** = TTM dividends paid / TTM net income (%). **`div_coverage`** = TTM FCF / TTM dividends paid (x).

## 7. Technical (from the last ~2 years of `adj_close`)

- **`ma_50`, `ma_150`, `ma_200`** — simple moving averages; **`price_vs_ma_N`** = % above/below.
- **`rsi_14`** — Wilder RSI.
- **`macd_line`**, **`macd_signal`**, **`macd_hist`** (12/26/9 EMAs); **`macd_crossover`** (`bullish`/`bearish`/`none` — histogram crossed zero in the last 5 days); **`macd_hist_trend`** (`growing`/`shrinking`/`flat`).
- **`bb_upper`, `bb_middle`, `bb_lower`, `bb_width`** — 20-day, 2σ Bollinger bands. **`bb_pct`** — position in the band (0 = lower, 1 = upper). **`bb_position`** — `above_upper` / `near_upper` / `middle` / `near_lower` / `below_lower`. **`bb_squeeze`** — 1 when band width is in its bottom 20% of the last ~6 months.
- **`vol_20d_avg`**, **`vol_ratio`** (last day / 20-day average), **`vol_trend`** (`increasing`/`decreasing`/`flat`, ±10%). NULL for zero-volume series (mutual funds).
- **`atr_pct`** — 14-day ATR as % of price. NULL for flat-NAV funds.
- **`pct_from_52w_high`** (≤ 0), **`pct_from_52w_low`** (≥ 0) — over 252 trading days.
- **`trend`** — swing-point classification over the last year: `strong_uptrend` (higher highs and higher lows), `weak_uptrend`, `sideways`, `weak_downtrend`, `strong_downtrend`. A trend counts as broken (`sideways`) once price crosses its last swing point.
- **`history_years`** — years of stored price history.

## 8. Intrinsic value (per share, in price currency)

- **`intrinsic_value_graham`** = √(22.5 × EPS × book value per share).
- **`intrinsic_value_lynch`** = EPS × min(EPS trend growth %, 25).
- **`intrinsic_value_dcf`** — 10-year FCF DCF: TTM FCF grown at a rate starting at `fcf_growth_trend` (capped 35%) and fading linearly to 2.5% terminal growth, Gordon terminal value, discounted at `wacc` (or CAPM cost of equity if no WACC; at least 2 points above terminal growth), minus net debt, ÷ shares.
- **`intrinsic_value_ddm`** — same fading-growth structure applied to `div_rate_ttm` with `div_growth_trend`.
- **`fair_value`** — **median** of the positive values among the models suited to the type: standard → all four (regulated utilities: no DCF); bank/insurance → Graham, Lynch, DDM; REIT → DDM only; funds → none.
- **`margin_of_safety`** = (fair value − price) / fair value × 100. Positive = price below fair value; large negatives are common for growth stocks.
- **Scenarios** — **`intrinsic_value_{lynch,dcf,ddm}_{bear,bull}`**, **`fair_value_bear/_bull`**, **`margin_of_safety_bear/_bull`**: the same models rerun with growth = trend ∓ that series' `growth_vol` (bear floored at −10%). Fair values use only models valid in base, bear and bull alike, so bear ≤ base ≤ bull. NULL = no range possible.
- **`valuation_guardrail_flag`** — 1 when the discount rate had to be floored, or `roic_vs_wacc` > 20 pp (terminal value assumes an implausible lasting edge).
- **Red flags** (0/1, shown, not applied to values): **`bear_flag_cash_conversion`** (OCF/NI < 100%), **`bear_flag_moat_narrowing`** (ROIC − WACC < 3 pp), **`bear_flag_interest_coverage`** (< 2x), **`bear_flag_earnings_quality`** (Beneish > −1.78); **`bear_flag_count`** (0-4).

## 9. Analyst, earnings and ownership (from `signals.db`)

- **`forward_eps_growth`**, **`forward_rev_growth`** — next fiscal year (`+1y`) consensus growth (%).
- **`forward_peg`** = `forward_pe` / `forward_eps_growth`.
- **`eps_revision_1m`**, **`eps_revision_3m`** — % change in the current-year consensus EPS vs 30 / 90 days ago.
- **`eps_revision_breadth`** — analysts raising minus cutting in the last 30 days.
- **`analyst_count`** — analysts behind the current-year EPS estimate.
- **`earnings_surprise_avg`**, **`earnings_surprise_last`** — average and latest surprise (%) over the last 4 reported quarters. **`earnings_beat_rate`** — % of those 4 that beat.
- **`days_to_next_earnings`** — calendar days from the price date to the next earnings date (negative = stale date).
- **`insider_net_buy_pct`** — net insider shares bought over 6 months as % of insider holdings.
- **`institutions_count`**.

## 10. Peer comparison

- **`<metric>_vs_sector`**, **`<metric>_vs_industry`** = (value − group median) / |median| × 100. Positive = higher than the typical peer (more expensive for a multiple, more profitable for a margin).
- Metrics: `pe`, `forward_pe`, `peg`, `ps`, `pb`, `p_fcf`, `ev_ebitda`, `ev_revenue`, `gross_margin`, `operating_margin`, `net_margin`, `roe`, `roa`, `revenue_cagr_3y`, `debt_to_equity`.
- NULL when the group has fewer than 3 values. Non-positive values of P/E, P/B, P/S, P/FCF, EV/EBITDA, PEG, debt-to-equity are excluded from the median and get NULL.
- The median is over all rows in the table with that sector/industry tag (including ADRs).

## 11. Rankings and scores (0-100, higher = better)

- **`rs_rank`** (0-99) — IBD-style relative strength. `rs_raw` = 0.4·r(Q1) + 0.2·r(Q2) + 0.2·r(Q3) + 0.2·r(Q4), where r = price ratio over each of the last four 63-trading-day quarters (newest first). Ranked as a percentile **within the same `security_type`** (types with < 30 members ranked across all). Needs 252+ days of history.
- **`<metric>_goodness`** — each metric turned into a 0-100 "how good is this value" score by a per-metric rule:
  - **peer** rules: percentile within industry (≥ 5 members), else sector, else whole table — e.g. P/E (lower better), margins, ROE (higher better).
  - **universe** rules: percentile across the whole table — e.g. CAGRs, growth steadiness.
  - **absolute** rules: 50 at a fixed line (e.g. PEG 1.0, margin of safety 0, Altman 3.0, Beneish −1.78, revisions 0), or 100 inside a sweet-spot band (current ratio 1.5-3, quick ratio 1-2, cash ratio 0.2-0.75, RSI 40-70, `bb_pct` 0.3-0.7, dividend yield 2-6% (REIT 4-10%), payout 30-60% (REIT 80-95%)), fading to 0 toward the data's outlier fence.
  - Columns ending in `_score` (`beneish_m_score`, `quick_health_score`) have no goodness twin.
- **Category scores** — weighted average of the available goodness values in the category (missing metrics drop out, weights renormalize; NULL if none):
  - **`value_score`** — ev_ebitda, p_fcf, margin_of_safety (1.0), pe (0.75), peg, pb, ps (0.5), ev_revenue, forward_pe (0.25).
  - **`quality_score`** — roic, gross_margin (1.0), roe, altman_z, fcf_margin (0.75), net_margin, debt_to_equity, current_ratio, interest_coverage (0.5), operating_margin, roa, debt_to_ebitda (0.25).
  - **`growth_score`** — eps_cagr_3y, revenue_cagr_3y (1.0), eps_yoy_q, revenue_yoy_q (0.75), fcf_cagr_3y, eps_growth_r2, eps_cagr_5y, revenue_cagr_5y, forward_eps_growth, eps_revision_1m, revenue_accel, earnings_surprise_avg (0.5).
  - **`momentum_score`** — rs_rank (1.0), pct_from_52w_high (0.75), price_vs_ma_50, price_vs_ma_200 (0.5), price_vs_ma_150, insider_net_buy_pct (0.25).
  - **`income_score`** — div_yield_ttm (1.0), div_cagr_3y, div_coverage, div_consistency (0.75), div_payout_ratio, div_consecutive_years (0.5), div_cagr_5y (0.375).
- **`overall_score`** — quality 25%, value 22%, momentum 20%, growth 18%, income 15%, renormalized over the categories present (funds usually only have momentum and income).
- **`orphan_score`** — `growth_score`, kept only for standard/REIT stocks with below-median (or no) analyst coverage and current ratio ≥ 1.1 ("neglected firm" candidates); else NULL.
- **`<score>_vs_type`** — each category score and `overall_score` as % above/below the median of the same `screen_type`.

## Known limitations

- **Percentiles depend on the universe**: goodness, scores and `rs_rank` are relative to whatever is in the table on that run. They are not comparable across runs.
- Scoring rules and weights can be changed locally; scores reflect the rules in force at build time.
- Funds dominate the row count; filter by `security_type` / `screen_type` before computing statistics.
- Fundamental metrics inherit `financials.db` gaps: a missing quarter makes every TTM metric NULL. Many stocks lack TTM P/E for this reason.
- Intrinsic values are model outputs with strong assumptions (fixed 5% equity risk premium, 2.5% terminal growth). DCF is very sensitive to growth.
- `sector`/`industry`/`country` are Yahoo tags; ADRs and foreign companies carry their home company's tags.
