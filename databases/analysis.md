# analysis.db — computed screening metrics, one row per security

The main analysis output: for every analysable security, ~290 columns of valuation, profitability, health, growth, dividend, technical, intrinsic-value, analyst and ranking metrics.
All values are computed from the other databases. Rebuilt from scratch on each full run, so it is a snapshot "as of" one price date.

Join key: `symbol` (see `symbols.md` for shared conventions). Snapshot when written (2026-11-09): ~38.6k rows, prices as of 2026-11-06.

## Table `analysis_meta` — one row

- **`analyzed_at`** (UTC build time), **`prices_as_of`** (the price date every metric uses), **`n_symbols`**.
- **`rank_universe_<pool>`** (since 2026-11-15) — size of each ranking pool's reference set (see 11): `rank_universe_company`, `rank_universe_fund`, `rank_universe_preferred`, …

## Table `analysis` — one row per symbol

**Who is in it**: symbols with `is_active = 1` and `is_validated = 1` in `symbols.db`, excluding indices and symbols whose price history has stopped (`is_delisted = 1`, since 2026-11-12). That includes funds, ETFs, warrants etc. — most fundamental columns are simply NULL for them. Around 60% of rows are mutual funds.

## Global conventions

- **Price** = `adj_close` of the last completed trading session (`ohlcv.db`). Never an intraday price.
- **Price window cleanup** (since 2026-11-12): before `price`, the technical columns and `rs_raw` are computed, each symbol's ~2-year price window gets its `ohlcv.price_flags` applied — `bad_print` and `stale` bars are dropped, and a `bad_adj` symbol uses `close` for `adj_close` (price return, not total return). `level_shift` is flagged only. See `price_issues`.
- **TTM** = sum of the last 4 **consecutive** quarters (no gap over ~120 days), else NULL. Balance-sheet items = latest reported value (quarterly, falling back to annual).
- **Percent metrics are stored as percent numbers** (12.5 = 12.5%). Multiples (P/E, ratios marked "x") are plain numbers.
- **Market cap** = price × the company's shares across **all classes** (since 2026-11-14): Yahoo `marketCap` ÷ Yahoo's quote price (B-share equivalents for BRK-B, ADR units for an ADR), else `impliedSharesOutstanding`, else `sharesOutstanding`, else the latest `ordinary_shares_number` / `share_issued`. The same count gives `eps_ttm` and the per-share fair values.
- **Second lines get no market cap** (since 2026-11-15): a line with no Yahoo market cap of its own while another listing of the same company (`company_id`) has one — preferreds, units, warrants and foreign duplicates typed as stocks — gets NULL market cap, price ratios, `eps_ttm` and fair values. Its other columns are the company's. ADRs are exempt (an ADR is the US listing even when Yahoo omits its cap).
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
  - **Industry overrides** (`config/industry_overrides.py`, since 2026-11-12) replace Yahoo's industry for a short list before `screen_type`, peers and the sector index: 19 bitcoin / crypto miners → "Crypto Mining" (Yahoo's "Capital Markets" routed them to `bank`), Sun Life → "Insurance - Life".
- **`price`** — the canonical price (above).
- **`rs_raw`** — weighted trailing return, the input to `rs_rank` (see 9).
- **`peer_universe`** (0/1, since 2026-11-15) — 1 for an exchange-listed stock / ADR with a market cap of its own (Yahoo's exchange name not "OTC Markets …", not a company second line). The set the peer medians are taken over (see 10).
- **`company_type`** (since 2026-11-15) — the health-check type from the Yahoo industry: `ordinary`, `utility` (Utilities - Regulated Electric / Gas / Water, Diversified), `telecom` (Telecom Services), `bank` (Banks - …), `insurer` (Insurance - …), `reit` (REIT - …), `mortgage_reit` (REIT - Mortgage), `broker` (Capital Markets, Financial Conglomerates, Mortgage Finance), `shell` (Shell Companies). NULL for non-companies.
- **`price_issues`** — the `ohlcv.price_flags` checks that touched the price window, comma-joined (e.g. `bad_print, stale`); NULL = clean prices. Screenable ("is null" keeps clean symbols). `level_shift` is not corrected — a lasting 10x move can be an unrecorded reverse split or a real collapse.

## 2. Valuation (TTM)

- **`market_cap`**.
- **`pe`** = market cap / TTM net income. **`ps`** = / TTM revenue. **`pb`** = / latest stockholders' equity. **`p_fcf`** = / TTM free cash flow.
- **`ev_ebitda`**, **`ev_revenue`** — enterprise value = market cap + total debt − cash + minority interest + preferred equity (the last two as 0 when not reported). EBITDA = Yahoo's `normalized_ebitda` (one-off charges removed) where available, else reported — also for `debt_to_ebitda` (since 2026-11-14).
- **`eps_ttm`** = TTM net income / shares.
- **`forward_pe`** = price / Yahoo `forwardEps`.
- **`peg`** = `pe` / `eps_growth_trend` (only when growth > 0).
- **A multiple over a zero or negative denominator is NULL** (since 2026-11-15; it used to be stored negative): `pe`, `forward_pe`, `p_fcf` on a loss, `pb` on negative equity, `ev_ebitda` on negative EBITDA, and `peg` through `pe`. Yahoo's own multiples (foreign filers) are NULL when negative. The sign stays visible in `eps_ttm`, `net_margin`, `ebitda_negative`. `ev_ebitda` can still be negative when EV is negative (cash above market cap + debt).

## 3. Profitability and returns (TTM, percent)

- **`gross_margin`**, **`operating_margin`**, **`net_margin`**, **`fcf_margin`** — over TTM revenue.
- **`roe`** = TTM net income / equity. **`roa`** = / total assets.
- **`asset_turnover`** (revenue / assets, x), **`equity_multiplier`** (assets / equity, x), **`roe_roa_gap`** (percentage points of ROE coming from leverage).
- `roe`, `equity_multiplier` and `roe_roa_gap` are NULL when equity ≤ 0 (since 2026-11-15): a loss over negative equity gave a large positive ROE.
- **`roic`** = NOPAT / invested capital; NOPAT = TTM operating income × (1 − effective tax rate, clipped 0-100%). NULL when invested capital ≤ 0 or |ROIC| > 500%.
- **`wacc`** (standard only, percent) — CAPM: cost of equity = 10y Treasury + beta × 5%; cost of debt = TTM interest expense / total debt × (1 − tax). NULL when beta is missing or |beta| > 10, or cost of debt > 50%.
- **`roic_vs_wacc`** — ROIC − WACC (pp); > 0 = creating value. **`roic_vs_wacc_5y`** — median annual ROIC of the last 5 years − today's WACC. **`roic_trend_3y`** — 3-year change in annual ROIC (pp). All standard only.
- **`ocf_to_ni_3y`**, **`ocf_to_ni_5y`** (standard only) — average operating cash flow / net income over the last 3 / 5 fiscal years, in percent. Loss years and years above 500% are excluded; needs 2+ years. ~100%+ = earnings backed by cash.
- **`gross_margin_trend_3y`**, **`operating_margin_trend_3y`** — change in annual margin over ~3 years (pp).
- **`op_margin_std`** (points) — standard deviation of the yearly operating margin over the last 5-10 fiscal years (needs 5). Lower = steadier.
- **`margin_vs_normal`** (points) — latest yearly operating margin minus the median of up to 10 earlier years (pre-tax margin when operating income has < 5 earlier years). Ordinary companies, utilities, telecoms.
- **`margin_peak`** (0/1) — cyclical peak: margin − normal > 15 points, margin > 1.5 × normal, and margin > 15 points above the Theil-Sen trend line through the earlier years. 0 when the normal is ≤ 0 (a turnaround, not a peak). The review's test: ~29% of the gap was given back within 2 years.

## 4. Financial health

- **`debt_to_equity`** (x), **`debt_to_ebitda`** (x), **`interest_coverage`** = TTM operating income / |TTM interest expense| (x). `debt_to_equity` is NULL when equity ≤ 0, `debt_to_ebitda` when EBITDA ≤ 0 (since 2026-11-15).
- **`ebitda_negative`** (0/1) — 1 when TTM EBITDA (as above) ≤ 0; NULL when unknown. Lets a filter with a "debt/EBITDA … or is null" branch keep these out.
- **`current_ratio`**, **`quick_ratio`** (cash + short-term investments + receivables, or current assets − inventory, over current liabilities), **`cash_ratio`**.
- **`altman_z`** — Altman Z-score, manufacturing form: 1.2·WC/TA + 1.4·RE/TA + 3.3·EBIT/TA + 0.6·MarketCap/Liabilities + 1.0·Sales/TA. Above ~3 safe, below ~1.8 distressed. NULL for large profitable firms whose retained earnings are deeply negative from buybacks.
- **`beneish_m_score`** — Beneish M-score from 8 year-over-year ratios of the last two fiscal years. Above −1.78 = possible earnings manipulation. NULL when |score| > 10.
- **`efficiency_ratio`** (banks only, %) — non-interest expense / revenue. TTM, else the newest fiscal year with both figures. Lower = better (~55-65% typical).
- **`quick_health_score`** (standard only, 0-7) — count of passed checks, last fiscal year vs the one before: revenue up; cost of revenue not outgrowing revenue; gross profit up; assets > liabilities; liabilities not outgrowing assets; cash ratio ≥ 0.2; operating cash flow up. Our own checklist, not the Piotroski score.
- **`piotroski_f`** (standard only, 0-9, since 2026-11-15) — Piotroski (2000), latest fiscal year (a) vs the one before (b), 300-430 days apart; one point each: ROA_a > 0; operating cash flow_a > 0; ROA_a > ROA_b; cash flow_a > net income_a; long-term debt / assets not up (missing debt = 0); current ratio up; `ordinary_shares_number` not up; gross margin up; revenue / assets up. ROA and turnover use the year's opening assets and leverage the average, as in the paper, when a third year is on file; else year-end assets. NULL unless all nine can be computed (total assets, current liabilities, revenue > 0).
- **`health_check`** (`ok` / `warn` / `fail`, since 2026-11-15) + **`health_reason`** (the failed tests, e.g. `debt/EBITDA 4.5x > 4x`) — a danger gate on the latest fiscal year, by `company_type`. A missing figure means that test can't be judged, never a fail; NULL when nothing can be judged.
  - **EBITDA** here = normalized, else reported; operating income + depreciation when Yahoo's EBITDA is below operating income (it starts from pre-tax income + interest, so a non-operating loss can drag it down: 210 listed companies, Carvana).
  - **Ordinary / utility / telecom:** fail on an operating loss or operating income / interest < 1.5x; warn when debt / EBITDA is above, or interest cover below, the line: industries sorted into thirds by the median `op_margin_std` of their listed ordinary companies (≥ 5) — steady 5.0x / 2.0x, normal 3.5x / 3.0x, swinging 2.5x / 4.0x; utilities 6.5x / 2.0x; telecoms 4.0x / 2.5x. EBITDA ≤ 0 with debt = warn. (The review failed on the line itself; changed 2026-11-15, it failed 31% of companies ≥ $2B.)
  - **REIT:** fail when EBITDA ≤ 0 or EBITDA / interest < 2x; warn when debt / EBITDA > 6.5x or EBITDA / interest < 3x.
  - **Insurer:** fail when equity ≤ 0, a net loss in 2 of the last 3 years, or (pre-tax income + interest) / interest < 2x; warn when debt > 35% of debt + equity, that cover < 4x, or the latest year is a loss.
  - **Bank** (warn only — loan quality and capital ratios are not in the data): a net loss, ROA < 0.5%, or tangible book < 4% of (total assets − goodwill and intangibles).
  - **Broker:** fail when equity ≤ 0 or a net loss in 2 of the last 3 years; warn when the latest year is a loss.
  - **Mortgage REIT:** fail when `economic_return_3y` < −20%; warn when it is < 0% or dividends paid exceed net income over the period.
- **`economic_return_3y`** (%, mortgage REITs) — (change in book value per share + dividends per share paid in between) / starting book value per share, over up to 3 fiscal years. Book value per share = `common_stock_equity` / year-end `ordinary_shares_number`, each year's count put on today's split basis; dividends from `ohlcv` (Annaly 2022-2024: 20.79 / 19.44 / 19.15, as reported).
- **Peer Strength measures** (since 2026-11-16, `analysis_layer/strength.py`; only for the types that use them):
  - **`roa_3y`**, **`rotce_3y`** (net income / tangible book), **`pretax_margin_3y`**, **`ebitda_margin_3y`** (%) — medians of the newest 3 fiscal years (≥ 2).
  - **`tangible_equity_ratio`** (%) — tangible book / (total assets − goodwill and intangibles). **`debt_to_capital`** (%) — debt / (debt + equity).
  - **`pretax_interest_cover`** (x) — (pre-tax income + interest) / interest; **`ebitda_interest_cover`** (x) — EBITDA / interest, 0 when EBITDA ≤ 0. Both 999 = no interest to pay (the insurer one only when profitable).
  - **`roe_std`** (points) — standard deviation of the yearly ROE over 5-10 years.
  - **`tbv_ps_growth`**, **`bvps_growth`**, **`ocf_ps_growth`** (%/yr) — robust growth (§5) of tangible book / book value / operating cash flow per diluted share over ~6 years; share counts put on the newest year's split basis when a split falls in the window (NULL if even the best basis is > 2.5x off). Since 2026-11-21 an event within 10% of 1:1 is not a split here: Yahoo records spin-off adjustments that way (Realty Income's Orion spin-off, "1.032"), which had given heavy share issuers NULL.

## 5. Growth

Four series — **revenue**, **eps** (diluted EPS on today's split basis: each row's basis is tested against its implied share count, net income ÷ EPS, since stored rows mix bases; a year with no reported EPS is filled with net income ÷ diluted average shares on the same basis test, since 2026-11-15), **fcf** (annual operating cash flow + capex), **book_value** (stockholders' equity) — each get:

- **`<x>_cagr_1y / _3y / _5y`** — compound annual growth (%) from the annual value ~N years back to the latest. NULL if either end is ≤ 0 or no point that far back.
- **`<x>_growth_trend`** — robust annual growth (%): Theil-Sen slope (median of pairwise slopes) of ln(value) over the last up to 6 annual points on their real dates; ≥ 4 points, all > 0. A first year that is an outlier (> 4 × the robust spread) is dropped once (the FY2020 trough). Since 2026-11-14; was an ordinary least-squares fit over 5 points.
- **`<x>_growth_vol`** — steadiness: 1.4826 × the median absolute residual around that line, % (lower = steadier). Series with a loss year are fitted on value ÷ median |value|.
- **`<x>_growth_tau`** — direction consistency, Kendall's tau of value vs time, −1 to 1 (+1 = up every year). Replaced `_growth_r2` and `_growth_cv` on 2026-11-14.
- Known limit: a two-year trough is not dropped — airlines keep a ~30%/yr post-COVID recovery "trend" (with a ~30% steadiness spread) until the window moves past 2021.
- **`<x>_yoy_q`** — latest quarter vs the same quarter a year earlier (%).

Plus:

- **`revenue_accel`**, **`eps_accel`** — `yoy_q` − `cagr_3y` (pp); > 0 = growing faster than its own 3-year pace.
- **`share_count_chg_1y`** — 1-year % change in diluted average shares (negative = buybacks).
- **`revenue_ps_growth`** (%/yr, since 2026-11-21) — robust growth of revenue per diluted share, the same per-share method as `ocf_ps_growth` (§4). Companies only.
- **`stock_category`** (text, since 2026-11-21, `analysis_layer/labels.py`) — growth / income / both / early / neither. Companies only; NULL for funds and shells. Lines in Settings (`CATEGORY_*`).
  - **growth:** `category_growth` ≥ 12%/yr. **`category_growth`** = mean of `revenue_ps_growth` and `forward_rev_growth` (either alone when the other is missing; Yahoo has no 2-year revenue forecast). A forecast above the track record counts only up to max(track record, `revenue_cagr_1y`): acquisitions (Kimberly-Clark +70%, Kenvue) and bank revenue-definition jumps are not yet in the reported year. Real accelerators keep the label (Lumentum, Bloom Energy); a rebound not yet reported loses it (Lattice).
  - **income:** `div_yield` (regular) ≥ 2.5% and `div_years_paid` ≥ 5.
  - **both:** growth and income.
  - **early:** a loss in the latest fiscal year and in more than half of the last up to 3, latest annual revenue < $1B (none on file = 0). Replaces growth / neither; an income label stays. Revenue is in the reporting currency (P/S can't convert it: TSM's is on Taiwan dollars), so foreign filers lean toward not early.
  - **neither:** otherwise.
- **Growth phase** (text, since 2026-11-21, `labels.py`; companies only; lines in Settings, `GROWTH_PHASE_*` / `GROWTH_AHEAD_*`):
  - **`growth_so_far`** — the revenue-per-share track record. **`revenue_ps_growth_long`** (%/yr) = robust growth over up to 12 fiscal years; **`revenue_years`** = the years it rests on. In order:
    - **recently listed:** `history_years` < 6 (young companies, spin-offs, bankruptcy relistings, late-listing REITs — the data can't tell them apart);
    - **short record:** < 8 years of revenue (mostly foreign filers: Yahoo's ~4 years, no SEC history);
    - **long-running:** long-run growth ≥ 10%/yr and Mann-Kendall tau ≥ 0.6;
    - **picked up:** `revenue_ps_growth` (~6y) ≥ the long-run rate + 5 points;
    - **moderate:** otherwise.
  - **`growth_ahead`** — `forward_rev_growth` against `revenue_ps_growth`: starting (past < 5%, forecast ≥ 12%), steady (change < 5 points or < 25% of the past rate), speeding up, slowing, slowing sharply (below half a positive past rate). The raw forecast, unlike the category: an acquisition reads as speeding up / starting (Kimberly-Clark). NULL without a forecast or past rate.
- **Per-share growth and listings** (all per-share columns, since 2026-11-21): when the share count jumps > 1.5x across the listing (first price bar + 1 year), the pre-listing years are dropped. Pre-IPO counts leave out the preferred that converts at listing (Palantir 577M → 1.9B), so revenue per share looked flat. Spin-offs file their old years on the distributed shares and keep them (GE Vernova).

## 6. Dividends (from `ohlcv.dividends` history)

Since 2026-11-15 every metric except the two `_ttm` ones is on **regular** payments (`analysis_layer/dividends.py`):

- **Pay rate** of a payment = amount × payments per year (1, 2, 3, 4, 6, 12, 26 or 52, from the spacing). A switch to monthly payments changes nothing.
- **Special** = a pay rate > 2.5 × the median of the lower half of the rates within ±400 days, unless the level persists (no lower payment follows within 400 days). A special on the regular schedule (regular + special in one payment) is kept at the regular level. Not tested for semi-annual / annual payers (interim and final differ by design). ~2% of payments; 786 of 2,531 stock / ADR payers had one.
- **Yearly rate** = median pay rate of a complete calendar year's regular payments (semi-annual / annual payers: the trailing-year sum).
- **`div_rate_ttm`** / **`div_yield_ttm`** — every dividend with ex-date in the last 365 days, specials included / price (%).
- **`div_rate`** — current regular annual rate: the latest regular payment's pay rate; 0 when the next payment is overdue (> 2 × spacing + 30 days: suspended). **`div_yield`** = that / price (%). Feeds `income_score` and the DDM.
- Non-payers get 0 for the four above and `div_years_paid`, NULL for the rest.
- **`div_cagr_1y / _3y / _5y`** (%), **`div_growth_trend`**, **`div_growth_vol`**, **`div_growth_tau`** — as in Growth, on the yearly rates.
- **`div_consecutive_years`** — trailing run of consecutive calendar years whose rate did not fall by more than 2% (rounding), or 10% for companies reporting in another currency (dividends arrive converted to dollars). 0 when `div_recent_cut` = 1. A "not cut" streak, not years paid; capped by the ~30-year price history.
- **`div_years_paid`** — unbroken calendar years with any dividend, counted back from the last full year.
- **`div_recent_cut`** — 1 when the current rate is below 92% of the median rate 1-2 years ago **and** the last 12 months' regular total is below 95% of the 12 months before; NULL with fewer than 5 regular payments in 3 years.
- **`div_outgrowing_earnings`** (0/1, since 2026-11-15) — a rising regular dividend (`div_growth_trend` > 0) growing > 5 points a year faster than net income (robust trend, §5), with `div_payout_ratio` ≥ 50% or a latest-year loss (with a loss and no income growth rate: dividend growth > 5 points is enough). NULL for non-payers. Narrowed from the review's "payout ≥ 50% or shrinking earnings": low-payout companies with falling earnings (Thermo Fisher, 9%) were 262 of its 427 flags.
- **`div_consistency`** — % of year-to-year steps that did not fall (same tolerance).
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
- **`intrinsic_value_lynch`** = EPS × min(EPS trend growth %, 25). The EPS trend includes the years filled from net income ÷ shares (§5).
- **`intrinsic_value_dcf`** — 10-year FCF DCF: TTM FCF grown at a rate starting at `fcf_growth_trend` (capped 35%; `revenue_growth_trend` when the FCF history has a negative year, since 2026-11-15) and fading linearly to 2.5% terminal growth, Gordon terminal value, discounted at `wacc` (or CAPM cost of equity if no WACC; at least 2 points above terminal growth), minus net debt, ÷ shares.
- **`intrinsic_value_ddm`** — same fading-growth structure applied to `div_rate` (regular, since 2026-11-15) with `div_growth_trend`.
- **`fair_value`** — **median** of the positive values among the models suited to the type: standard → all four (regulated utilities: no DCF); bank/insurance → Graham, Lynch, DDM; REIT → DDM only; funds → none.
- **`margin_of_safety`** = (fair value − price) / fair value × 100. Positive = price below fair value; large negatives are common for growth stocks.
- **`fair_value_tail`** — 1 when `fair_value` > 3 × price: a model tail (distressed price, book far above market value, a past cash flow that won't recur), not a bargain. ~10% of stocks / ADRs with a fair value.
- **Scenarios** — **`intrinsic_value_{lynch,dcf,ddm}_{bear,bull}`**, **`fair_value_bear/_bull`**, **`margin_of_safety_bear/_bull`**: the same models rerun with growth = the low / high end of that series' trend-growth 80% confidence range (the Theil-Sen slope interval; bear floored at −10%; since 2026-11-15, was trend ∓ `growth_vol`). Fair values use only models valid in base, bear and bull alike, so bear ≤ base ≤ bull. NULL = no range possible.
- **`valuation_guardrail_flag`** — 1 when the discount rate had to be floored, or `roic_vs_wacc` > 20 pp (terminal value assumes an implausible lasting edge).
- **Red flags** (0/1, shown, not applied to values): **`bear_flag_cash_conversion`** (OCF/NI < 100%), **`bear_flag_moat_narrowing`** (ROIC − WACC < 3 pp), **`bear_flag_interest_coverage`** (< 2x), **`bear_flag_earnings_quality`** (Beneish > −1.78); **`bear_flag_count`** (0-4).

## 9. Analyst, earnings and ownership (from `signals.db`)

- **`forward_eps_growth`**, **`forward_rev_growth`** — next fiscal year (`+1y`) consensus growth (%). `forward_rev_growth` is NULL when the `+1y` row's year-ago revenue is more than 1.5x off the `0y` row's estimate (a scale break: MUFG, SMFG, Mizuho and NextSource Materials; since 2026-11-12). EPS is not checked that way — the two rows often use different EPS definitions.
- **`forward_peg`** = `forward_pe` / `forward_eps_growth`.
- **`eps_revision_1m`**, **`eps_revision_3m`** — % change in the current-year consensus EPS vs 30 / 90 days ago.
- **`eps_revision_breadth`** — analysts raising minus cutting in the last 30 days.
- **`analyst_count`** — analysts behind the current-year EPS estimate.
- **`earnings_surprise_avg`**, **`earnings_surprise_last`** — average and latest surprise (%) over the last 4 reported quarters, leaving out quarters whose estimate was within $0.05 of zero. Large values remain where a real miss met a small estimate (ABTC −2,442%). **`earnings_beat_rate`** — % of those 4 with actual EPS above the estimate (`eps_difference` > 0).
- **`days_to_next_earnings`** — calendar days from the price date to the next earnings date; NULL when the stored date has already passed (Yahoo updates late).
- **`insider_net_buy_pct`** — net insider shares bought over 6 months as % of insider holdings.
- **`institutions_count`**.

## 10. Peer comparison

- **`<metric>_vs_sector`**, **`<metric>_vs_industry`** = (value − group median) / |median| × 100. Positive = higher than the typical peer (more expensive for a multiple, more profitable for a margin).
- Metrics: `pe`, `forward_pe`, `peg`, `ps`, `pb`, `p_fcf`, `ev_ebitda`, `ev_revenue`, `gross_margin`, `operating_margin`, `net_margin`, `roe`, `roa`, `revenue_cagr_3y`, `debt_to_equity`, `debt_to_ebitda`, `interest_coverage` (the last two since 2026-11-15).
- **The median is taken over the peer universe** (`peer_universe` = 1: listed stocks / ADRs with their own market cap; since 2026-11-15). Every row with a value and a tag, OTC included, is measured against it. Before, OTC lines, preferreds, units and closed-end funds with a sector tag were in the median (Auto Manufacturers' P/E median 7.9 with them, 18.3 without).
- NULL when the group has fewer than 5 peer-universe values (3 before 2026-11-15). A small industry stays NULL; it does not fall back to the sector. Non-positive values of P/E, P/B, P/S, P/FCF, EV/EBITDA, PEG, debt-to-equity, debt/EBITDA are excluded from the median and get NULL.

## 11. Rankings and scores (0-100, higher = better)

- **Fixed ranking universe** (since 2026-11-15; before, every row in the table):
  - **`rank_pool`** — `company` (stock, ADR), `fund` (ETF, mutual fund, closed-end fund), else the security type (`preferred`, `unit`, …). Rows are only ranked within their pool.
  - **`rank_universe`** (0/1) — the pool's reference rows, the distribution every percentile is taken against: companies with `peer_universe` = 1 (listed, own market cap), one share class per mutual fund (SEC series; the class with a return history first, then alphabetical), every row of the other pools. Rows outside it (OTC stocks, extra fund classes) are placed in that distribution.
  - The absolute rules' fade-out reach is also measured on the reference rows.
- **`rs_rank`** (0-99) — IBD-style relative strength. `rs_raw` = 0.4·r(Q1) + 0.2·r(Q2) + 0.2·r(Q3) + 0.2·r(Q4), where r = price ratio over each of the last four 63-trading-day quarters (newest first). Percentile **within the ranking pool**, against its reference rows (pools with < 30 reference values ranked across all). Needs 252+ days of history.
- **`<metric>_goodness`** — each metric turned into a 0-100 "how good is this value" score by a per-metric rule:
  - **peer** rules: percentile within industry (≥ 5 reference members), else sector, else the pool — e.g. P/E (lower better), margins, ROE (higher better).
  - **universe** rules: percentile across the pool — e.g. growth trends, CAGRs, growth steadiness.
  - **absolute** rules: 50 at a fixed line (e.g. PEG 1.0, margin of safety 0, Altman 3.0, Beneish −1.78, revisions 0), or 100 inside a sweet-spot band (current ratio 1.5-3, quick ratio 1-2, cash ratio 0.2-0.75, RSI 40-70, `bb_pct` 0.3-0.7, dividend yield 2-6% (REIT 4-10%), payout 30-60% (REIT 80-95%)), fading to 0 toward the data's outlier fence.
  - Columns ending in `_score` (`beneish_m_score`, `quick_health_score`) have no goodness twin.
  - **`margin_of_safety_goodness`** is NULL when `fair_value_tail` = 1 (a model tail is not a bargain).
- **Category scores** — weighted average of the available goodness values in the category (missing metrics drop out, weights renormalize). **NULL when less than half of the category's weight is present** (since 2026-11-15; before, any one input gave a score):
  - **`<category>_inputs`** (%) — the share of the weight present, stored next to each score.
  - **`value_score`** — ev_ebitda, p_fcf, margin_of_safety (1.0), pe (0.75), peg, pb, ps (0.5), ev_revenue (0.25). `forward_pe` left out since 2026-11-15.
  - **`quality_score`** — roic, gross_margin (1.0), roe, altman_z, fcf_margin (0.75), net_margin, debt_to_equity, current_ratio, interest_coverage (0.5), operating_margin, roa, debt_to_ebitda (0.25).
  - **`growth_score`** — eps_growth_checked, revenue_growth_checked (1.0), eps_yoy_q, revenue_yoy_q (0.75), fcf_growth_trend, eps_growth_tau, eps_revision_1m, revenue_accel, earnings_surprise_avg (0.5). Since 2026-11-15; before, the 3- and 5-year CAGRs and `forward_eps_growth`.
  - **`revenue_growth_checked`**, **`eps_growth_checked`** (%) — the trend growth, pulled halfway toward the analysts' next-year forecast (`forward_rev_growth`, `forward_eps_growth`) when the forecast is lower; a higher forecast adds nothing. Forecasts for fast growers run high and get cut later (La Porta 1996; Bordalo, Gennaioli, La Porta & Shleifer 2019).
  - **`momentum_score`** — rs_rank (1.0), pct_from_52w_high (0.75), price_vs_ma_50, price_vs_ma_200 (0.5), price_vs_ma_150, insider_net_buy_pct (0.25).
  - **`income_score`** — div_yield (1.0), div_cagr_3y, div_coverage, div_consistency (0.75), div_payout_ratio, div_consecutive_years (0.5), div_cagr_5y (0.375), div_years_paid (0.25).
- **`overall_score`** — quality 25%, value 22%, momentum 20%, growth 18%, income 15%, renormalized over the categories present; NULL below half of that weight (`overall_inputs`). Funds have only momentum and income (35%), so they have no overall score since 2026-11-15.
- **Non-payers** have no `income_score` (only the yield and years-paid zeros are inputs, ~26% of the weight); their overall score is over the other four categories.
- **`orphan_score`** — `growth_score`, kept only for standard/REIT stocks with below-median (or no) analyst coverage and current ratio ≥ 1.1 ("neglected firm" candidates); else NULL.
- **`peer_strength`** (0-100, since 2026-11-16) — the company's strength against its peers on its type's measures: each measure's percentile (flipped where lower is better), averaged.
  - **Measures:** bank — `roa_3y`, `rotce_3y`, `pretax_margin_3y`, `tangible_equity_ratio`, `tbv_ps_growth`; insurer — `pretax_margin_3y`, `debt_to_capital` (lower), `pretax_interest_cover`, `roe_std` (lower), `bvps_growth`; REIT — `ebitda_margin_3y`, `debt_to_ebitda` (lower; EBITDA ≤ 0 = worst), `ebitda_interest_cover`, `ocf_ps_growth`; broker — `rotce_3y`, `pretax_margin_3y`, `roe_std` (lower), `tbv_ps_growth`; ordinary / utility / telecom — `roic`, `ocf_to_ni_3y`, `op_margin_std` (lower), `debt_to_ebitda` (lower; EBITDA ≤ 0 = worst), `interest_coverage`, `piotroski_f`.
  - **Peers:** the industry when it holds ≥ 8 reference companies of the type family, else the sector (ordinary family) or the whole type. **Reference** = listed (`peer_universe`) companies ≥ $2B; smaller / OTC companies are scored against them.
  - NULL with fewer than 2/3 of the type's measures (ordinary: 4 of 6), for mortgage REITs (gated by `health_check` instead) and shells. **`peer_strength_measures`** = how many it rests on.
  - The review's test: the weaker score went to the riskier stock (later peer-adjusted volatility) in ~70% of pairs.
- **`<score>_vs_type`** — each category score and `overall_score` as % above/below the median of the same `screen_type`.

## Known limitations

- **Percentiles are relative to the run's reference set**: since 2026-11-15 that set is fixed by rule (listed companies, one class per fund) and its size is stored in `analysis_meta`, but the companies in it still change between runs, so scores are comparable across runs only roughly.
- **Score accuracy is not validated yet**: whether higher scores lead to better later returns is unknown. `score_history.db` keeps one snapshot per full run (since 2026-11-15) so it can be tested once enough runs exist.
- Scoring rules and weights can be changed locally; scores reflect the rules in force at build time.
- Funds dominate the row count; filter by `security_type` / `screen_type` before computing statistics.
- Fundamental metrics inherit `financials.db` gaps: a missing quarter makes every TTM metric NULL. Many stocks lack TTM P/E for this reason.
- Intrinsic values are model outputs with strong assumptions (fixed 5% equity risk premium, 2.5% terminal growth). DCF is very sensitive to growth.
- **Most fair values far above the price are not growth problems**: price far below book (Graham), a large past cash flow next to a distressed price (DCF), or a high dividend about to be cut (DDM). `fair_value_tail` marks them.
- **Dividends of irregular payers**: variable dividends (Cal-Maine pays a share of profit, Argentine banks pay in instalments) can read as cuts or a suspension. Specials are not detected for semi-annual / annual payers.
- `sector`/`industry`/`country` are Yahoo tags; ADRs and foreign companies carry their home company's tags.
