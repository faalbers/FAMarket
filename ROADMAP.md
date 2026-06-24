# Stock Screening System — Brainstorm Roadmap

## Progress Overview
- Started: 2026-06-04
- Current position: BRAINSTORM COMPLETE — ready to start coding in Claude Code
- Last session ended: 2026-06-06
- Convention: You will update this roadmap file at end of each topic with all the newly discussed results

## Project Context
- Frank has already built a working version of this system before
- This brainstorm is a from-scratch redesign with Claude — goal is to catch gaps and improve on the original
- Build approach: build each layer fully before moving to the next (Data → Analysis → UI), not thin end-to-end slices

## Key Decisions Made
- Topic 7 (Directory Structure & Module Design) intentionally skipped — folder layout and module naming will be decided during Claude Code implementation, not in brainstorm
- Fetch pipeline is symbol-centric: symbol list is fixed before fetch run starts; all fetchers work from the same list
- Type resolution happens first (Polygon ticker list provides type → normalized via type_map; yfinance quoteType as fallback for unknowns) → types stored in symbols.db before data fetch begins
- Each fetch function inside a fetcher filters the symbol list internally by applicable security types — caller passes full list, function picks the right subset
- is_active flag is an output of the OHLCV fetch (updated post-fetch), not a prerequisite gate
- Fetch runs manually, Friday evening after market close
- Full US-investable universe (all security types — stocks, ETFs, REITs, mutual funds, ADRs, preferred, closed-end funds, SPACs, warrants)
- `security_type` + `sub_type` fields on every symbol (sourced from yfinance `quoteType`)
- Type names normalized across all APIs via lookup table in config/
- Multi-style flexible screening (value, growth, GARP — no single style locked in)
- Output: Interactive Streamlit web UI (local, runs via `streamlit run app.py`)
- `pandas_market_calendars` used to determine last completed trading session
- Completeness definition for financial periods → deferred to coding phase
- SQLite wrapper uses explicit, opinionated methods per operation: `db.append()`, `db.replace()`, `db.upsert(key=)` — no generic read/write (improvement over previous build)
- Separate SQLite databases by data type — cross-database merging done in pandas
- All API parameters stored as individual columns; schema grows dynamically via ALTER TABLE ADD COLUMN
- Batch fetching with `ratelimit` + `tenacity` (rate limiting + auto retry)
- Python `logging` module — writes to terminal + rotating log file
- Multiprocessing across APIs → Phase 2 optimization (single-threaded first)
- Write queue pattern for concurrent DB writes → revisit when building multiprocessing
- Filter UI: single unified interface, dynamically adaptive per security type
- Short parameter names in UI + hover tooltips from editable config/param_hints.py
- Filter operators: >, <, <=, >=, = on raw numeric values stored in analysis.db
- Store raw numeric values in analysis.db — let filter do comparisons, not pre-computed booleans
- Fetch ALL data first → analysis runs automatically after, full recalculate every run (clean slate)
- All prices use closing price of last completed trading session — never intraday
- Trend detection: peak detection only (scipy.signal.find_peaks), no MA-based trend
- Growth metrics: CAGR + polyfit residuals volatility % + R² + CV for all growth categories (1y/3y/5y + YoY quarterly)
- Hover hints: structured 3-section format (What it is / How to use it / Compare with peers), shown on hover after ~0.5-1s delay, bullet points within sections where needed
- Peak detection calibration tool built into Streamlit UI (not a separate script)
- Filter rows: [ ⏸ ] [ + ] [ - ] [ Parameter ] [ Operator ] [ V/P ] [ Value ] — toggle/add OR/delete/param/op/value-or-param
- Filter page layout: Security Type checklist (collapsible) + Filters section (collapsible) with Load/Add/Save/Clear buttons, scrollable drag-to-reorder block list, + Add Filter, Run Filter
- Filter sets saved as .filt files; column selections saved as .prms files; paths set in Settings
- Output: each filter run opens in its OWN browser tab (updated 2026-06-11; was: Run Filter navigates to the in-app Output page). Run Filter persists the result as a run file (results/ folder, parquet rows + json metadata, newest OUTPUT_RUNS_KEEP kept, survives restarts) and auto-opens it at /output?run=<id> (fallback link in a caption if the browser blocks the popup); empty results show a message on the Filter page instead — no tab, no run file. The sidebar Output page is a recent-runs launcher
- Output results table: Symbol (AAPL (stock)), Company Name, Sector/Industry, then selected parameter columns
- Action menu opens new browser tab per action; actions grouped as: Normalized Charts / Fundamentals / Dividends / Analyze on external site
- Dividend yield calculated from yfinance history() Dividends column — no separate API needed
- Dividend growth line charts use annual and quarterly periods only (TTM excluded)
- External site URLs: Finviz → screener?v=111&t=SYM1,SYM2; Yahoo Finance → /quotes/SYM1,SYM2/; TradingView → one tab per symbol; Koyfin → coding phase
- All charts use color-blind safe palette (no red/green); blue-to-orange for heatmaps
- Database file paths configured in config/settings.py — single DB_DIR variable + individual path vars per database (SYMBOLS_DB, OHLCV_DB, QUOTES_DB, FINANCIALS_DB, ANALYSIS_DB, MACRO_DB)
- Backup system: rotating 5-version backup of all .db files before each fetch run — BACKUP_DIR in config/settings.py; versions shift up by 1 on each run, version 5 dropped, new backup becomes version 1; format: {db_name}_1.db (most recent) through {db_name}_5.db (oldest)
- .env stores all API keys in UPPER_CASE — never committed to git (.env in .gitignore); .env.template committed with key names but empty values

---

## Topics

✅ Topic 1 — Project Overview & Goals
   - ✅ Subtopic 1.1 — Purpose and scope of the system
   - ✅ Subtopic 1.2 — Stock universe size (all US-investable securities, all types)
   - ✅ Subtopic 1.3 — Investing style approach (multi-style, flexible)
   - ✅ Subtopic 1.4 — Output format choice (Interactive Streamlit web UI)

✅ Topic 2 — Fetch Strategy
   - ✅ Subtopic 2.1 — Fetch cadence
     - Manual run, Friday evening after market close
     - ✅ IMPLEMENTED — market-closed gate: while the regular NYSE session is open,
       everything EXCEPT symbol discovery is skipped (no intraday prices). Honors
       weekends, US holidays, and early closes via `core/market_calendar.is_market_open`
       (pandas-market-calendars, XNYS). On by default; `run_full_fetch(block_when_market_open=…)`,
       a Fetch Control toggle, and `scripts.run_fetch --allow-market-open` turn it off
       for testing. This is shipped — do NOT re-assess as future work.
   - ✅ Subtopic 2.2 — Incremental fetch by data type
     - OHLCV → append by date; initial fetch = 10 years minimum
     - End date always capped to last completed trading session (pandas_market_calendars)
     - yfinance history() call returns Dividends column alongside OHLCV — dividend history fetched for free in the same call, no separate API needed
     - Financials (annual/quarterly) → append by date; re-check incomplete periods each run
     - TTM → calculated from stored quarters, not fetched
     - Completeness of financial periods → deferred to coding phase
     - yfinance date indices used to detect new vs existing periods
   - ✅ Subtopic 2.3 — fetch_status table & infrastructure
     - Tracks last fetch date per symbol per data type
     - fetch_errors counter — high-error symbols skipped, reported in end-of-run log file
     - Makes fetch runs resumable
     - Separate SQLite databases: symbols.db, ohlcv.db, financials.db, quotes.db, analysis.db
     - Cross-database merging handled in pandas
     - Batch fetching with configurable batch size
     - ratelimit library for rate limiting per API
     - tenacity library for automatic retries on failure
     - Python logging module → writes to terminal + rotating log file
     - Rate limit testing utility — standalone script that sends requests at increasing speeds per API, logs where errors appear, establishes safe ceiling to configure in settings
     - Active symbol checking — during each weekly fetch, flag symbols with no new OHLCV for X consecutive weeks as inactive in symbols.db
     - symbols.db gets is_active flag + last_active_date per symbol
     - Inactive symbols skipped in future fetches and excluded from screening by default, but kept in DB for historical reference
     - Multiprocessing across APIs (Phase 2) — write queue pattern to handle concurrent DB writes
   - ✅ Subtopic 2.4 — Initial load vs weekly update modes
     - Auto-detect: empty databases = initial load, otherwise weekly update

✅ Topic 3 — Data Layer Design
   - ✅ Subtopic 3.1 — API sources
     - yfinance — primary source for quotes, financials, OHLCV
     - Polygon (free account, key in .env) — symbol universe, historical OHLCV, reference data (splits, dividends)
     - FMP / Financial Modeling Prep (free account, key in .env) — financial statements & ratios supplement; free tier ~250 req/day
     - FMP usage strategy: Key Metrics endpoint, prioritize high market cap symbols, target yfinance gaps — deferred to coding phase
     - SEC EDGAR — free, no rate limit, fallback for financial filings + secondary symbol source (`https://www.sec.gov/files/company_tickers.json`)
       - EDGAR symbols added to symbols.db with `source=edgar` and `is_validated=False`
       - Validation happens as a byproduct of the normal yfinance fetch — no extra API calls:
         1. yfinance info returns real data → quote data written to quotes.db simultaneously
         2. OHLCV has data within the last few weeks → confirms actively trading
         3. Financials have at least some recent periods → confirms not a dead/shell company
       - All three must pass for `is_validated=True` — partial passes stay False and get flagged
       - Analysis layer only processes symbols where `is_active=True` and `is_validated=True`

   - SYMBOL STATE MANAGEMENT:
     - New symbols always enter symbols.db with `is_active=True` by default
     - Fetch runs only process `is_active=True` symbols — inactive symbols skipped entirely
     - Full symbol state reassessment runs at end of every fetch run (never assumed to carry forward)
     - Symbols no longer present in latest Polygon fetch → flagged with `in_polygon=False` (not deleted)
     - Newly listed symbols added by Polygon mid-week → picked up on next Polygon sync as `is_active=True`
     - End-of-run validation flips `is_active=False` only when absence of data is confirmed NOT an API error:
       - No data / symbol not found → legitimate dead/invalid symbol → flip `is_active=False`
       - Rate limit / timeout / connection error → API problem, not symbol problem → keep `is_active=True`, log error, retry next run
     - fetch_errors counter in fetch_status table distinguishes API errors from true missing data
     - First fetch run: straight insert (empty DB auto-detected = initial load)
     - Subsequent runs: upsert — update existing, add new, preserve flags
     - E*Trade (OAuth1, key in .env) — secondary quote data source
       - Auth uses webbrowser module + user enters code via input()
       - Auth runs once per fetch session
       - Token revocation via class destructor (__del__) automatically
       - Revoke URL: https://api.etrade.com/oauth/revoke_access_token
       - On API error: revoke immediately, log error, retry affected symbols next session
       - Phase 2: auth in main process before workers start, token passed to workers
     - FRED (free account, key in .env) — macro data (interest rates, inflation, GDP)
     - Future potential: Alpha Vantage (25 req/day), OpenFIGI (free) — add after coding phase reveals gaps
   - ✅ Subtopic 3.2 — Security types & classification
     - All types: common stock, ETF, REIT, mutual fund, ADR, preferred, closed-end fund, SPAC, warrant
     - security_type + sub_type columns on every symbol
     - yfinance quoteType as primary classification source
     - Type names normalized across APIs via lookup table in config/ (e.g. yfinance "EQUITY" → "stock/common")
     - Symbol format normalization required after Polygon fetch — other APIs (yfinance, E*Trade) use different formats:
       - Indices (`market=I:`): replace leading `I:` with `^` (e.g. `I:SPX` → `^SPX`)
       - Class tickers (last 2 chars `.C`): replace all `.` with `-` (e.g. `BRK.B` → `BRK-B`)
       - Warrants (`type=WARRANT`): replace trailing `.WS.A` or `.WS` with `-WT`
       - Units (`type=UNIT`): replace trailing `.WS.A` or `.U` with `-UN`
       - Polygon `type` and `market` fields used to detect which transformation to apply
       - Normalized symbol stored as separate column (e.g. `symbol_normalized`) alongside original Polygon symbol
     - Scope tiers:
       - Common Stock, REIT, ADR → full: quotes, financials, OHLCV, metrics, dividend history
       - ETF, Closed-end Fund → full ETF: quotes, holdings, OHLCV, ETF metrics, dividend history
       - Preferred Stock → quotes + yield + dividend history
       - Mutual Fund → limited: quotes + basic info only
       - SPAC, Warrant → minimal: quotes only
   - ✅ Subtopic 3.3 — What data to fetch per security type
     - All types: basic info, quote data
     - Stocks/REITs/ADRs: financials (annual+quarterly), OHLCV, dividends, key ratios
     - ETFs/Closed-end: holdings, expense ratio, NAV, tracking error, OHLCV, dividends
     - Income data for all applicable types: yield, dividend rate, payout ratio, ex-div date, dividend history
     - Calculated in Analysis layer: dividend growth rate, consecutive growth years, dividend consistency, coverage ratio
     - Analyst estimates fetched via yfinance: earnings_estimate, revenue_estimate, growth_estimates
       - Multiple time horizons (current/next quarter, current/next year)
       - Stored in financials.db; used in Analysis layer for forward-looking metrics
       - Coverage sparse for small caps — NULL where unavailable
       - ✅ IMPLEMENTED — FETCH side (2026-06-21; expanded + renamed 2026-06-22):
         `YFinanceSignals` (was `YFinanceEstimates`; data_layer/fetchers/yfinance_fetcher.py)
         pulls earnings_estimate + revenue_estimate + growth_estimates AND eps_trend +
         eps_revisions (the revision-momentum signal — yfinance gives 7/30/60/90d-ago in one
         snapshot). Stored in **signals.db** (renamed from estimates.db; NOT financials.db —
         different key shape: the `estimates` table is tidy/long keyed (symbol, horizon) with
         horizon 0q/+1q/0y/+1y/LTG), upsert-replaced each run. applies_to stock/reit/adr;
         standard weekly lock; no-coverage names self-abandon. Wired into the orchestrator
         Group 2 (after financials, before EDGAR) + backup/restore.
         **2026-06-22 — same fetcher now also reads, off the SAME Ticker (one throttle pass,
         ~5 req/symbol under the Financials envelope), two NEW raw tables in signals.db:**
         `earnings_surprise` (tidy per-quarter from earnings_history: eps est/actual/diff +
         surprise%) and `ownership` (one row/symbol from calendar + the Holders request:
         next earnings/ex-div dates, net insider buy/sell activity, institutions_count).
         Stored RAW. Multi-table write via a `_write` split (no base.py change). Ownership %s NOT
         duplicated (already in quotes.db `info`).
       - ✅ IMPLEMENTED — ANALYSIS side (2026-06-23): `analysis_layer/signals.py` derives 6 metrics
         from those tables → `earnings_surprise_avg/_last`, `earnings_beat_rate`,
         `days_to_next_earnings`, `insider_net_buy_pct`, `institutions_count` (new **Earnings** +
         **Ownership** filter categories, applies COMPANY; param_hints + filter_registry). Scoring
         rules (scoring_rules.py): surprise/insider `absolute@0`, beat-rate `universe` → goodness +
         Score variant + heatmap; days/institutions filter-only. Category weighting:
         `earnings_surprise_avg`→growth 0.5, `insider_net_buy_pct`→momentum 0.25. analysis.db
         216→226 cols. Verified end-to-end (AAPL surprise +6.1/beat 100%; REIT O insider net-buy +40.7%).
       - ✅ IMPLEMENTED — ANALYSIS side (2026-06-22): `analysis_layer/estimates.py` derives seven
         forward filter metrics from the `estimates` table in signals.db, wired into `pipeline.run_analysis()` (per-symbol,
         pre-grouped O(1) like the other panels) and surfaced as a new **Estimates** filter category
         (param_hints.py + filter_registry.py): `forward_eps_growth`, `forward_rev_growth`,
         `forward_peg`, `eps_revision_1m`, `eps_revision_3m`, `eps_revision_breadth`, `analyst_count`.
         REVISED from the original plan: a true **LTG-based PEG/Lynch is NOT derivable** — yfinance's
         LTG horizon carries only the *index* long-term constant (~12% for every symbol), never a
         per-stock stockTrend, so the forward PEG uses the **+1y (next fiscal year)** consensus EPS
         growth instead. Revision momentum/breadth read the FY0 (current-year) consensus.
       - ✅ SCORING (2026-06-22): all the new metrics (these + the cheap-win batch) got scoring
         RULES in `scoring_rules.py` (heatmap coloring + Rules-page tunable; new **Estimates** rule
         category) — except `market_cap`/`analyst_count`, left ruleless as size/coverage gates. And
         three forward signals — `forward_eps_growth`, `eps_revision_1m`, `revenue_accel` — now feed
         `growth_score` at 0.5 weight each (`CATEGORY_METRIC_WEIGHTS`); sparse, so they drop out and
         renormalize for no-coverage names. Values populate on a full analysis run.
     - Data completeness checked before appending — is_complete flag on all records
     - yfinance values cross-checked against calculated values; fallback to calculated if info returns None/NaN
     - data_source field per metric: "info", "calculated", or "fallback_calculated"
   - ✅ Subtopic 3.4 — SQLite database structure
     - Five separate databases: symbols.db, quotes.db, ohlcv.db, financials.db, analysis.db
     - All API parameters stored as individual columns (no JSON blob)
     - Schema grows dynamically — new parameters added via ALTER TABLE ADD COLUMN
     - NULL for symbols where a parameter doesn't apply
     - All keys in .env, .env in .gitignore
     - Non-sensitive settings (batch sizes, rate limits, file paths, scoring weights, peak detection params, etc.) in config/settings.py
     - Settings page in Streamlit UI provides interface to edit config/settings.py
     - ✅ 2026-06-12: defaults + local-override model (replaces "both routes update the same file"). config/settings.py holds the committed DEFAULTS and is never written by the UI. Saving writes ONLY the changed keys to a gitignored, machine-local `settings.local.json` (flat `{dotted.path: value}`, e.g. `RATE_LIMITS.yfinance`); the bottom of settings.py lays it on top at import (config/settings_overrides.py — type-coerced to each default, unknown/bad keys ignored). Reasons: no git churn on settings.py, simpler/safer than the previous in-place AST rewrite, and exe-ready (override can move to a user-data dir when frozen). Retired the old config/settings_io.py + its settings.py backups. Hand-edit a default (committed) or the override (local). Delete the file to reset all to defaults.
     - Settings page uses collapsible sections grouped by category — grouping to be designed during coding phase

✅ Topic 4 — Analysis Layer
   - ✅ Subtopic 4.1 — Metric categories defined
     - Size: Market cap (price × shares outstanding)
     - Valuation: P/E, Forward P/E, PEG, P/B, P/S, P/FCF, EV/EBITDA, EV/Revenue
     - Profitability: ROE, ROA, ROIC, gross/operating/net margins, FCF margin, EPS, 3y gross/operating margin trend
     - Growth: Revenue, EPS, FCF, book value growth (1y, 3y, 5y CAGR); revenue/EPS acceleration (latest YoY − 3y CAGR); 1y diluted share-count change (buybacks)
     - Income: dividend yield (TTM, annual, quarterly), growth rate, payout ratio, consecutive growth years, consistency, coverage ratio
     - Dividend yield calculation:
       - Source: Dividends column from yfinance history() — included in OHLCV fetch, no separate API call needed
       - TTM yield = sum of all dividends in last 365 days ÷ current closing price × 100
       - Annual yield = sum of dividends in calendar year ÷ closing price at year end × 100
       - Quarterly yield = sum of dividends in quarter ÷ closing price at quarter end × 100
     - Financial Health: Debt/Equity, current ratio, quick ratio, interest coverage, Debt/EBITDA, cash ratio, Altman Z-Score
     - Technical: MAs (50/150/200-day), price vs MA %, RS rank, RSI, MACD, Bollinger Bands, volume metrics, 52-week high/low %, ATR
     - Intrinsic Value — go deep when we get here:
       - Graham Number (EPS + book value)
       - Peter Lynch Fair Value (EPS + growth rate)
       - Simple DCF (FCF history + growth estimates + FRED risk-free rate + beta)
       - Results: intrinsic_value_graham, intrinsic_value_lynch, intrinsic_value_dcf, margin_of_safety
     - ✅ IMPLEMENTED amendment (2026-06-21): added six filter-only "cheap win" metrics
       derived from data already loaded — market_cap (new Size category),
       gross_margin_trend_3y / operating_margin_trend_3y (pct-point change vs ~3y ago),
       revenue_accel / eps_accel (latest YoY − 3y CAGR), share_count_chg_1y (1y diluted
       share-count change; negative = buybacks). Filter metrics ONLY — deliberately NOT
       wired into category scores or peer-relative (_vs_sector/_vs_industry) variants.
   - ✅ Subtopic 4.2 — Technical indicators
     FETCH / ANALYSIS PHASE DESIGN:
     - Fetch ALL data first (all APIs, all symbols) → analysis runs automatically after fetch completes
     - Full recalculate of analysis.db every run (clean slate — fast, no complexity of tracking deltas)
     - ✅ IMPLEMENTED amendment (2026-06-10): dev-subset runs no longer wipe the rest of analysis.db.
       A subset run recomputes ONLY the subset rows, merges them into the existing table
       (other symbols' rows kept as-is), then re-runs peers/scoring/rs_rank over the merged
       frame so ranks and medians stay universe-wide (rs_raw is now a stored column for this).
       No subset → full clean-slate rebuild, unchanged.
     - If an API fails after retries → log clear failure message, continue other fetchers, analysis still runs on available data
     - UI shows: "Analysis calculated: [timestamp]" + "Prices as of: [last completed trading session date]"
     - All price-based calculations use closing price of last completed trading session only (never intraday)

     GROWTH METRICS (added to all growth categories — Revenue, EPS, FCF, Book Value):
     - CAGR (1y, 3y, 5y) — compound annual growth rate
     - Polyfit residuals volatility % — detrended noise measure (Frank's method)
     - R² — how well trend line explains the data (0=chaotic, 1=perfectly consistent)
     - CV — coefficient of variation (simple baseline for comparison)
     - YoY quarterly growth (same quarter vs prior year, removes seasonality) + same volatility metrics
     - NULL stored where insufficient history exists; gated: 1y immediate, 3y after 3 years, 5y after 5 years

     TREND:
     - Peak detection only — no MA-based trend stored
     - Uses scipy.signal.find_peaks on closing prices (swing highs) and inverted prices (swing lows)
     - Classifies: "strong_uptrend" (HH+HL confirmed), "weak_uptrend", "sideways", "weak_downtrend", "strong_downtrend" (LL+LH confirmed)
     - ✅ IMPLEMENTED amendment (2026-06-13): SWING-BREAK gate added to the
       classifier (analysis_layer/technical._classify). The peak structure alone
       ignored all price action after the last peak, so a stock that made HH+HL but
       has since collapsed below its last swing low still read "strong_uptrend". Now
       a trend reverts to "sideways" once price closes below the last swing low (up)
       or above the last swing high (down) — Dow-theory "trend intact only while the
       last swing holds". A broken trend is NOT relabelled to the opposite direction
       (that needs a new confirmed swing). Stays inside the peak-detection paradigm —
       no MA/regression input (the MA-confirm alternative was considered and rejected
       to keep the label self-contained; price_vs_ma_50 remains a separate column).
       Measured ~13% of liquid-universe labels flip, all trend→sideways.
     - ✅ IMPLEMENTED (2026-06-13): Calibration tool built into Streamlit UI, not a
       separate script. MOVED 2026-06-18 from its own sidebar page into a **section of the
       Settings page** (`ui/calibration.py` exposes `render()`, called inside a self-managed
       collapsible section — NOT an st.expander, which would snap shut on the calibrator's
       reruns and mount its ECharts iframe at 0-width while collapsed; `ui/pages/calibration.py`
       removed) — it tunes two settings.py knobs, so it belongs with Settings:
       - Auto-selects representative stocks by PRICE behavior, not the stored
         fundamental-growth R² (amended from "highest/lowest R², most/least volatile":
         the stored R² measures revenue/EPS trend quality, the wrong axis for tuning
         PRICE peaks). Picks from a liquid equity pool (top vol_20d_avg, atr_pct ≤ 25%
         to drop reverse-split artifacts), computes a linear-fit R² on each one's price
         window on the fly, then spans clear-trend / choppy / volatile / calm.
       - Shows detected swing highs/lows overlaid on the price chart, with Prev/Next
         click-through at Frank's pace + a manual symbol box. Uses the SAME
         technical.trend_signals(close, prominence, distance) the pipeline calls, so the
         peaks shown are exactly what a run computes.
       - Two sliders (PEAK_PROMINENCE, PEAK_DISTANCE) → Save writes both to
         settings.local.json via settings_overrides.update_settings (same path the
         Settings page uses). New values apply on the NEXT analysis run; existing rows
         are not re-labelled.
       - Shared ECharts dark theme/palette lifted into ui/chart_theme.py (was local to
         charts.py) now that a 2nd chart view needs it.
     - `trend` column stored as text in analysis.db

     RSI:
     - 14-day only (standard, appropriate for weekly screening)
     - `rsi_14` stored as numeric value in analysis.db

     MACD (12/26/9 standard settings):
     - `macd_line` — 12-day EMA minus 26-day EMA (numeric)
     - `macd_signal` — 9-day EMA of MACD line (numeric)
     - `macd_hist` — MACD line minus signal line (numeric)
     - `macd_crossover` — text: "bullish" / "bearish" / "none" — persists 5 trading days after crossover event
     - `macd_hist_trend` — text: "growing" / "shrinking" / "flat"

     BOLLINGER BANDS (20-day SMA, 2 standard deviations):
     - `bb_upper`, `bb_middle`, `bb_lower` — the three band values (numeric)
     - `bb_width` — upper minus lower, measures band tightness (numeric)
     - `bb_pct` — where price sits within bands (0=lower band, 1=upper band, >1=above upper) (numeric)
     - `bb_position` — text: "above_upper" / "near_upper" / "middle" / "near_lower" / "below_lower"
     - `bb_squeeze` — boolean: bands unusually narrow, signals potential big move coming
     - All are current state, recalculated weekly (no persistence logic needed)

     VOLUME (in progress — combinations with other indicators discussed):
     - `vol_20d_avg` — 20-day average daily volume (baseline)
     - `vol_ratio` — last closing day volume ÷ 20-day average (is recent volume unusual?)
     - `vol_trend` — text: "increasing" / "decreasing" / "flat"
     - Volume combines with trend, MACD, BB, RSI in filter UI for conviction screening

     HOVER HINTS (applies to all parameters across all categories):
     - Shown as tooltip on hover, after ~0.5–1 second delay — no short description shown in dropdown
     - Three structured sections per hint:
       1. What it is — plain language definition
       2. How to use it — what good/bad values look like and what action they imply
       3. Compare with peers — yes/no and why (when to look at _vs_sector / _vs_industry)
     - Section titles bold, info text indented below each title
     - Bullet points used within a section when multiple points need explaining
     - Plain language throughout — explain what the value MEANS for the stock, not just the math
     - Stored in config/param_hints.py — editable without touching UI code

     ATR:
     - `atr_pct` only — ATR ÷ price × 100 (normalized, comparable across price levels)

     52-WEEK HIGH/LOW:
     - `pct_from_52w_high` — % below 52-week high
     - `pct_from_52w_low` — % above 52-week low

     RS RANK:
     - `rs_rank` — 0–99 weighted percentile vs entire universe (IBD-style)
     - Weighted: 40% last 3 months, 20% each prior 3-month period
     - Calculated last in analysis phase after all other metrics
     - NULL if less than 252 days of history

     ALL PRICE CALCULATIONS USE adj_close (adjusted for splits and dividends)

   - ✅ Subtopic 4.3 — Peer comparison logic (sector/industry medians)
     - Metrics with peer comparison: P/E, Forward P/E, PEG, Profit margins, ROE, ROA, EV/EBITDA, Revenue growth, Debt/Equity
     - Two columns per metric: `_vs_sector` and `_vs_industry` — stored as % above/below peer median
     - No median values stored — just the relative % difference
     - Two grouping levels: Sector (broad) and Industry (narrow)

   - ✅ Subtopic 4.4 — Scoring & ranking approach
     - Both raw metrics AND composite scores available
     - Scores range: 0–100
     - Category scores: Value, Quality, Growth, Momentum, Income
     - Overall Score: weighted combination of all category scores
     - Default Overall Score weights: Quality 25%, Growth 25%, Momentum 20%, Value 20%, Income 10%
     - Metric weights within each category score: set by Claude as sensible defaults
     - All weights adjustable via Settings page in Streamlit UI (sliders, saves to config automatically)
     - Settings page also covers category weights within Overall Score
     - ✅ PER-PARAMETER SCORING RULES added + IMPLEMENTED (2026-06-20, Option A) —
       a richer "is this value strong or weak?" model than scoring.py's two-state
       `_LOWER_IS_BETTER`. Each metric gets a RULE: shape (higher_better / lower_better /
       **sweet_spot** — middle is best, e.g. payout, current ratio) + anchor (`peer` /
       `universe` / `absolute` with a pivot value or a band) + sparse per-`screen_type`
       overrides (e.g. REIT payout 80-95 vs base 30-60). `analysis_layer/scoring_rules.py`
       holds committed `DEFAULT_RULES` + the `goodness()` 0-100 engine (pure; UI + a future
       scoring.py both import it); user edits save to gitignored `scoring_rules.json`.
       A new left-menu **Scoring Rules** page (`ui/pages/scoring_rules_page.py`) edits base
       rules with a live IQR-zoomed, rule-colored histogram preview; the **metrics heat map**
       (6.2) is the first consumer.
       - DECIDED: anchor follows the GOAL (income = absolute bands, not peers); PEG anchored
         absolute @1.0 (growth-adjusted, comparable on its own); preview x-axis + goodness
         falloff both use the **1.5× IQR fence** (P/E etc. are wildly right-skewed).
       - SCOPE (Option A): heatmap is the ONLY consumer for now — `scoring.py` category/overall
         scores are NOT rewired yet. Category `*_score`s are RESULTS, not rules (excluded from
         the rules page; the rewire must DERIVE them from rule goodness).
       - ✅ PER-PARAMETER SCORE as a filter/Output VARIANT (2026-06-22): `scoring.py` now stores
         a `<metric>_goodness` (0-100) column per scorable metric in analysis.db — computed once
         via `metric_goodness`, reused as the input to the category scores (parameter goodness →
         category → overall). Surfaced as a 4th Filter **compare** variant **"Score"** (beside
         Value / vs Sector / vs Industry; `filter_engine.resolve_column`→`_goodness`,
         data-driven `filter_registry.score_column`), and labelled "· Score" + sortable in
         Output. Editing a rule + Save runs `scoring.refresh_scores()` — a ~4s recompute of the
         goodness + category/overall columns on the stored analysis.db (no fetch/per-symbol pass),
         so the filterable scores track the heatmap. 76 goodness cols (excludes `*_score`/peer
         variants + `rs_rank`); analysis.db 140→226 cols.
       - ✅ RANK-WITHIN-SECURITY-TYPE for RS Rank (2026-06-24 confirmed; shipped in the
         c2619b3 scoring rewire): `scoring._rs_rank` ranks `rs_raw` WITHIN each
         `security_type` via `_stats.percentile_rank_tiered` (thin types < RS_RANK_MIN_PER_TYPE
         fall back to the universe percentile). Funds (~65% of the universe) no longer
         distort real stocks. PROBABLY-A-NON-ISSUE for the OTHER `universe`-anchored
         scoring-rule metrics (not RS Rank): they are all FUNDAMENTALS (growth CAGRs, margin
         trends) which mutual funds DON'T HAVE → funds are NaN there → they already drop out
         of the ranking, so the ~65%-funds flood that hurt RS Rank (a PRICE metric funds DO
         have) doesn't reach them. LEAVE PARKED — only revisit if a future metric is BOTH
         `universe`-anchored AND price-based (funds would then re-enter that pool).
       - PER-TYPE OVERRIDE EDITING UI — PARKED (decided 2026-06-24, with a trigger).
         Per-`screen_type` overrides already WORK (`scoring_rules.resolve()` /
         `metric_goodness()`); only 2 are seeded in code (REIT `div_yield_ttm` +
         `div_payout_ratio`). A UI to add/edit more fits the visual-tuning workflow but only
         pays off given a concrete per-type tuning need. REVISIT TRIGGER: a metric
         demonstrably mis-scored across types — standing candidate **P/B** (banks/REITs sit
         at very different "normal" P/B, so one universal rule mis-ranks them). If/when built:
         verify a base-rule edit doesn't drop the seeded `overrides` from `scoring_rules.json`,
         and make `_preview()` reflect the selected screen_type.
       - CATEGORICAL METRICS → NUMERIC ENCODING — NOT PLANNED (decided 2026-06-24).
         `trend`/`macd_crossover`/`bb_position`/`vol_trend`/`macd_hist_trend` are text,
         excluded from scoring (`goodness()` coerces to numeric → NaN). They're already
         filterable BY VALUE, which for an enum is clearer/more honest than a fuzzy 0-100
         score; the only gain (heatmap color on `trend`) doesn't justify per-metric ordinal
         encoding + an ordinal goodness path + a dual filter UI. Same "redundant, no new data"
         call as the dropped yield heat map.
       - (Yield heat map DROPPED 2026-06-24 — see 6.2.)

   - ✅ Subtopic 4.5 — Sector & sub-industry index series (added + IMPLEMENTED 2026-06-14)
     - WHAT: a daily base-100 level series for every Yahoo sector and every
       'sector | industry' group — a constructed index per group, so a sector's /
       industry's price trend can be charted and compared, not just per-stock metrics.
     - FORMULA (ported verbatim from a vetted standalone prototype the user supplied,
       since removed from the repo): SPDR Select Sector method. Float-MC weights (price ×
       daily-interpolated shares × IWF) recomputed each quarter (third-Friday rebalance),
       capped with the current/"new" (2024-09-23) Select Sector diversification rules,
       held fixed between rebalances, chained to base 100. The formula/capping/rebalance
       mechanics live in `analysis_layer/sector_index.py` — do not retune them.
     - WHERE/HOW STORED: a NEW dedicated `databases/indices.db` (one-DB-per-data-type
       convention), table `sector_industry_index` in LONG/tidy shape
       (`kind`,`label`,`date`,`level`) + an `index_meta` row. `replace`d clean-slate
       each full run, like analysis.db. Added to backup/restore scope.
     - WHEN: inside `pipeline.run_analysis()`, FULL runs only (universe-wide; subset
       runs lack the panel). Isolated in try/except AFTER the analysis.db write so an
       index failure can't discard the main result. Module: `analysis_layer/sector_index.py`.
     - DATE RANGE / EFFICIENCY (amended 2026-06-14, replaced the first cut): the index
       history is DECOUPLED from `ANALYSIS_OHLCV_LOOKBACK_DAYS` (that ~2yr bound exists
       only to cap the full-table read the per-symbol metrics need — wrong axis for the
       indices). Start is DATA-DRIVEN: the earliest date by which >=
       `INDEX_START_MIN_REPORTERS` (default 25) constituents have a share report in
       financials.db (breadth guard against a lone deep-history outlier dragging the read
       back decades). Prices come from a DEDICATED memory-efficient deep read
       (`sector_index._deep_price_panel`): only `adj_close`, only the ~4.5k liquid
       constituents (not the 38k universe), read in 500-symbol IN(...) chunks assembled
       into one column per symbol. Quotes + full financials + the recent liquidity window
       are still reused from what the pipeline already holds.
       - MEASURED (2026-06-14, stdlib ctypes probe, 32GB machine): the index build's own
         footprint is tiny — price panel ~38 MB, shares panel ~38 MB, ~8.5 MB per extra
         YEAR of history, so deepening history is essentially free on RAM. Data-driven
         start landed on 2021-12-31 (~4.5yr, ~144k rows). The big resident consumers are
         pipeline-wide and pre-existing (730-day ohlcv_by, ~930 MB financials), not the
         index. A permanent one-line peak-RAM log was added (`core/meminfo.py`, Win32 via
         ctypes, no psutil) — `run_analysis()` logs "peak RAM X GB / Y GB system" each run.
     - UNIVERSE / DISTORTION GUARD: the prototype warns illiquid/penny names can
       dominate a group's index. Filter = active+validated → sector-tagged → LIQUIDITY
       FLOOR (avg daily dollar volume ≥ `INDEX_MIN_AVG_DOLLAR_VOLUME`, default $1M, over
       `INDEX_LIQUIDITY_WINDOW_DAYS`=63 bars; flat-NAV funds drop for free). Tiny
       industries pruned via `INDEX_MIN_INDUSTRY_MEMBERS`. All in config/settings.py.
     - ✅ FIRST CONSUMER IMPLEMENTED (2026-06-14) — relative-strength view on the Charts
       page (`ui/pages/charts.py`, `view=price`). A full-width selector ABOVE the chart
       lists the charted symbols' sectors (collapsible via a ▸/▾ arrow) with a
       non-collapsible industry sublist; each entry has a checkbox, single-select across
       the whole tree (click the active one to clear). On select the chart REPLACES the
       normal normalized view with only that group's symbols, each plotted as
       `symbol_norm − index_norm + 100` over the period ∩ index window (both rebased to
       100 at the line's first shared date — every line starts at 100; a literal
       difference, so a big underperformer can dip below 0). The index series is read
       from indices.db. Period (1Y/3Y/5Y) stays live: changing it recomputes the overlap.
       A **3-way view toggle** (st.segmented_control above the chart) switches, at the
       same window + base-100, between: Relative (symbol−index+100), Symbols (the same
       names normalized, no subtraction), and Index (the group index alone). Identity:
       Relative = Symbols − Index + 100. The legend's per-line on/off selection is
       PRESERVED across the toggle/period reruns via a `legendselectchanged` event
       round-trip stored in session_state and re-applied as `legend.selected`.
     - ✅ SECOND CONSUMER IMPLEMENTED (2026-06-18) — standalone **Sector Indices** page
       (`ui/pages/sector_index.py`), its OWN sidebar entry (registered in app.py, just above
       Parameters), NOT a Charts `view=` / Output link — it's symbol-free, so it belongs in the
       main nav rather than a per-run Action menu. A **View** toggle (default **Sectors**):
       overlays all 11 sectors, each REBASED to 100 at the chosen period's start (1Y/3Y/5Y/Max)
       so growth is directly comparable; dataZoom picks sub-ranges. Uses a 12-color CB-safe
       palette (`_SECTOR_COLORWAY`, Tol bright+muted) since 11 sectors exceed the shared 7-color
       COLORWAY.
     - ✅ INDUSTRIES ADDED (2026-06-18) — the same page's **Industries** View mode: pick one
       sector → overlay that sector's industries (6–24 each; legend shows the industry part
       only) with a 2-way **Relative / Absolute** toggle. Relative (default) =
       `industry_norm − sector_norm + 100`, both rebased to 100 at their first shared date in
       the window (flat 100 line = the parent sector; above beats it) — same idea as the price
       chart's relative-strength view (`_relative_line` ports `charts.py::_group_symbol_lines`).
       Absolute = each industry rebased to 100 at the window start. Legend show/hide persists
       across period changes and resets when the view/sector/scale context changes. Big sectors
       (Industrials = 24) cycle the 12-color palette tail — accepted (legend + hover name each
       line). The chart render is factored into `_chart_options` + `_render_chart`, shared by
       both modes.
     - ⏭️ STILL OPEN (brainstorm before building, topic-by-topic): other consumption —
       ranking/sorting sectors by trend, industry presentation, derived metrics, etc.
       ([[indices-usage-needs-brainstorm]]).

✅ Topic 5 — Filter Interface
   - KEY DESIGN DECISION: Single unified interface, dynamically adaptive
     - Security type is the first filter
     - Available metrics show/hide automatically based on selected type
     - ETF selected → expense ratio, holdings, tracking error appear
     - Stock selected → P/E, EPS growth, revenue growth appear
     - Mixed types → only shared metrics shown
     - Allows mixing types in one screen (e.g. stocks + REITs for income screening)
     - One codebase, one interface to learn — no separate screens per type
   - KEY DESIGN DECISION: Short parameter names in UI + hover tooltips
     - Parameter names kept as short/abbreviated as possible
     - Hover hints show full name, category, description, usage
     - Hints stored in config/param_hints.py (or YAML) — easily editable without touching UI code
     - Streamlit native tooltip support via help= parameter on widgets
     - Editable by Frank manually or via Claude Code on request
   - ✅ Subtopic 5.1 — Filter logic
     BLOCK-BASED FILTER BUILDER:
     - Filters are blocks stacked top to bottom: [ Parameter ] [ Operator ] [ Value or Parameter ]
     - `between` operator adds fourth entry: [ Parameter ] [ between ] [ Value ] [ and ] [ Value ]
     - Every block has a toggle (on/off), active by default
     - Between is always inclusive

     AND/OR STRUCTURE:
     - All top-level blocks are ANDed together (stock must pass ALL to appear in results)
     - OR child blocks can be added under any AND block (one level deep only)
     - OR children cannot have their own children
     - Logic: check parent first → if true, skip children → if false, check OR children → if any child true, parent passes
     - OR children act as fallbacks when parent fails

     OPERATORS:
     - >, <, <=, >=, =, !=
     - between (inclusive)
     - is null, is not null
     - starts_with, contains (text only, case-insensitive)
     - ✅ IMPLEMENTED amendment (2026-06-18): membership operators `is any of` /
       `is none of` for low-cardinality columns. A filter column whose distinct-value
       count is within a cap is auto-detected (`filter_engine.categorical_values`) and
       its value box is replaced by a SEARCH-NARROWED MULTI-PICK list (reuses
       `ui/param_picker.render` in multi-select mode, categories/info off) — the user
       picks values instead of typing exact strings; `value` is stored as a JSON list.
       Two caps in settings: `FILTER_CATEGORICAL_MAX_UNIQUE` (100, text/classification)
       and `FILTER_CATEGORICAL_MAX_UNIQUE_NUMERIC` (12, so 0-100 scores / 1-99 ranks
       stay range filters; only tiny numeric enums like a 0/1 flag become lists). NULL
       still fails both membership ops (mirrors `!=`). Existing enum metrics (`trend`,
       `macd_crossover`, `bb_position`, `vol_trend`, `macd_hist_trend`) get this for free.
     - ✅ IMPLEMENTED amendment (2026-06-18): new "Classification" base metrics
       `sector`, `industry`, `fund_family` (filter on the company/fund's text labels).
       sector/industry were already in analysis.db; `fund_family` is now copied into
       analysis.db by `analysis_layer/pipeline.py` (funds only, NULL for stocks) — needs
       an analysis re-run to populate. Each has a `config/param_hints.py` entry.

     PARAMETER SELECTION (first and third entries):
     - Searchable dropdown — type to filter, type beginning of name to jump to first match
     - Parameters grouped by category with non-selectable section headers
     - Alphabetically sorted within each category
     - No short description in dropdown — full structured hover hint shown on hover (0.5–1s delay)
     - Third entry accepts fixed value OR another parameter name
     - between uses fixed values or parameter names for both bounds

     NULL HANDLING:
     - Any symbol with NULL for a filtered parameter automatically fails and is excluded
     - Exception: is null / is not null operators work correctly regardless

     SECURITY TYPE SELECTOR:
     - Dedicated selector at top of filter page (separate from filter blocks)
     - Controls which parameters appear in the filter block dropdown
     - Multiple types selectable at once — only shared parameters shown when multiple selected
     - Hover hints on every type and sub-type (2-4 sentences, plain language)
     - Common Stock sub-types: Standard, Bank/Financial, Insurance
     - ADR inherits same sub-types as Common Stock
     - Sub-type auto-detected from sector/industry data, manual override available in UI

   - ✅ Subtopic 5.2 — Saving & importing filter sets
     - Filter sets saved as .filt files (plain JSON, human-readable/editable) in filters/ folder
     - Save, Load, Add (append to current filters), Clear (wipe current screen)
     - Save/Load/Add → **native OS file dialog** (`ui/file_io.py`), starting in the
       filters/ folder; Save writes straight there. (2026-06-21: replaced the original
       in-app .filt dropdown picker — we evaluated the browser file chooser vs a
       tkinter dialog and chose tkinter because it can start in / save to the filters
       folder and gives plain Save/Load/Add buttons. Reusable for .prms later. Valid
       only because the app is local & single-user; a cloud/remote move would need the
       browser chooser instead. tkinter runs out-of-process to avoid Streamlit's
       worker-thread crash.)
     - Load → replaces current filters entirely; Add → appends saved set's blocks
     - Clear → wipes all current blocks (no file selector)
     - Filters folder path configured once in Settings

   - ✅ Subtopic 5.3 — Filter UI design in Streamlit
     FILTER ROW LAYOUT:
     - [ ⏸ ] [ + ] [ - ] [ Parameter ▾ ] [ Operator ▾ ] [ V/P ] [ Value ]
     - ⏸ = toggle on/off (front), + = add OR child, - = delete block (end of controls)
     - V button = value mode (click to switch to parameter mode → becomes P)
     - P button = parameter mode (click to switch back to value mode → becomes V)
     - V/P button disappears when text operators (starts_with, contains) are selected
     - For `between` operator: two value entries each with their own V/P button
     - OR child rows: same layout but no + button; only - button

     AND/OR VISUAL STRUCTURE:
     - OR children indented with a left border line (no collapsing — always visible)
     - Entire block list has no collapsible parts
     - AND blocks draggable to any position in main list (OR children move with parent)
     - OR children draggable within their parent's OR group only

     PAGE LAYOUT (top to bottom):
     - ▶ Security Type (collapsible, open by default)
       - Checklist of all security types (Common Stock checked by default)
       - Supports multiple selections — only shared parameters shown when multiple selected
     - ▶ Filters (collapsible, open by default)
       - [ Load ] [ Add ] [ Save ] [ Clear ] button row
       - Scrollable block list (drag-to-reorder)
       - [ + Add Filter ] (adds new AND block at bottom)
       - [ Run Filter ] (auto-opens the run in its own browser tab)

     RUN FILTER (updated 2026-06-11 — was: navigate to the in-app Output page):
     - Saves the result as a run file: parquet rows + json metadata in results/
       (ui/output_runs.py; newest OUTPUT_RUNS_KEEP kept; survives app restarts)
     - Auto-opens the run in its OWN browser tab at /output?run=<id>; several
       output tabs can be open at once. Fallback link in a caption in case the
       browser blocks the popup (allow pop-ups for the site once)
     - Empty results: message on the Filter page — no tab opened, no run file
     - Results displayed there (Topic 6)

✅ Topic 6 — Output Interface (Streamlit)
   - ✅ Subtopic 6.1 — Results table & sorting
     - (updated 2026-06-11; 2026-06-16) One browser tab per output: each tab shows one
       output at /output?run=<id>, independent column selections per tab. The
       sidebar Output page (no ?run param) is a "Recent outputs" launcher: saved outputs
       (filter runs AND custom symbol sets) listed with Name / Type / run time / Count /
       security types and an "Open ↗" link each (see 6.2 for Custom Symbols)
     - Table columns: Symbol (AAPL (stock)), Company Name, Sector/Industry, then filter parameters
     - Add/remove parameter columns via same searchable dropdown as filter
     - ✅ IMPLEMENTED (2026-06-21): Column selection saveable as .prms file; load with
       Swap or Add option. Built on the shared SELECTION system below (`ui/selection_io.py`),
       not a bespoke writer. Save/Load(Swap)/Load(Add) buttons sit in the Parameter-columns
       list; per-param info = the param's `param_hints` entry.
     - .prms folder path configured in Settings (now the shared `SELECTIONS_DIR`)
     - ✅ IMPLEMENTED (2026-06-21): SHARED SELECTION SYSTEM (`ui/selection_io.py`). One
       JSON shape — a dict keyed by item with per-item info, insertion order preserved —
       serves two kinds in one `SELECTIONS_DIR` folder (suffix distinguishes):
       **`.syms`** symbol sets (info = Company/Sector/Industry from analysis.db) and
       **`.prms`** parameter/column sets (info = each param's `param_hints`). Built on the
       native file dialog (`ui/file_io`); the typed filename is the name (no pre-naming).
       SYMBOL Save/Load wired into: Output Custom Symbols box (Load also sets the Output
       name to the file stem), the results-selection Action menu (Save selection), and the
       Fetch Control dev subset (enrich-if-available). Local-machine only (file_io limit).
       Replaced the originally separate `COLUMN_SETS_DIR` with the shared `SELECTIONS_DIR`.
     - Multi-column sort: click header = primary sort, Shift+click = secondary sort etc.
     - Sort direction indicator + priority number on each sorted column header
     - Standard table multi-select: click = select row, Shift+click = range, Ctrl/Cmd+click = add to selection
     - Click anywhere on row to select it

   - ✅ Subtopic 6.2 — Action menu
     - ✅ IMPLEMENTED (2026-06-12): results table is multi-row selectable; an
       Action popover above it (disabled until rows are selected) holds the
       Normalized price chart (opens ui/pages/charts.py in a new tab) plus the
       external-site links — Finviz + Yahoo (one multi-symbol tab each),
       TradingView (one tab per symbol). Each opens in a new browser tab via
       st.link_button; external URLs come from settings.EXTERNAL_SITES. Still to
       build: the Fundamentals + Dividends chart actions, and Koyfin (URL format
       still to be confirmed).
     - ✅ IMPLEMENTED (2026-06-16): the SAME Action menu is also available on a
       hand-typed symbol list. The menu body was extracted to `_render_actions(symbols)`,
       shared by the run-results selection popover and the launcher's typed-symbol box.
       SUPERSEDED 2026-06-16 by Custom Symbols (below): the one-off "Quick actions" popover
       was replaced — a typed symbol set is now a saved output you open into the full
       results screen, where the same row-selection Action menu already lives.
     - ✅ IMPLEMENTED (2026-06-16): CUSTOM SYMBOLS as saved outputs. An "output" is now
       either a FILTER run or a CUSTOM symbol set; both persist identically via
       `ui/output_runs.py` (parquet+json) with a `kind` field ("filter"/"custom"; runs
       without it read as "filter"). `save_run` and new `save_custom_run` share a private
       `_persist(df, meta_extra)`. The launcher (no-`?run=` mode) has a collapsible
       "Custom Symbols" box: an Output **name** + a symbols field (comma/space/newline) and
       a **Go** button (active only when BOTH are filled) that snapshots those symbols from
       analysis.db (`_read_analysis_rows`, ordered to the typed order; symbols absent from
       analysis.db are reported and dropped), saves a custom output, and opens it in a new
       tab — exactly like the Filter page's Run Filter. Custom outputs open into the SAME
       results screen with NO viewer rewrite (param_cols empty so the column set starts
       blank; screen_types = the distinct types found, so the column picker still works).
       The viewer header + summary branch on kind (a "Symbols in this output" list replaces
       the filter-block summary). Launcher table renamed "Recent outputs"; columns are now
       Open / **Name** / **Type** (Filter|Custom) / Run at / **Count** / Security types
       (Security types shown for filter runs only). Custom outputs share the
       OUTPUT_RUNS_KEEP retention pool.
     - ✅ IMPLEMENTED (2026-06-13): Fundamentals BAR chart (charts.py
       view=fundamentals_bar). Design REVISED from the original "grouped by metric,
       multi-symbol" note (see the action-menu structure below): the user wants to
       watch ONE symbol's parameter change over time, so v1 is one-symbol ×
       one-parameter × bars-over-periods, with an annual/quarterly toggle (symbol +
       parameter are single-select dropdowns; the multi-symbol comparison is a
       deliberate later step). Data comes from financials.db per period (read lazily
       per symbol, cached) — NOT analysis.db, which only stores the current snapshot.
       Ratios (margins/ROE/ROA/leverage) reuse the SAME formula functions as the
       analysis snapshot: metrics.py now exposes them as named funcs +
       RATIO_PERIOD_METRICS/RAW_PERIOD_FIELDS registries, so a ratio is defined ONCE
       (verified 80/80 parity vs the stored snapshot after the extraction). Price-based
       ratios (P/E, EV/EBITDA, yield) need a per-period price and are DEFERRED; growth
       metrics + category scores are multi-period summaries and don't belong on a
       per-period bar. Missing periods render as gaps.
     - ✅ IMPLEMENTED (2026-06-15): RADAR chart (charts.py view=radar) — the five 0-100
       category scores (Value/Quality/Growth/Momentum/Income) read straight from the
       analysis.db snapshot (no recompute), one polygon per selected symbol, overlaid
       for comparison. Dark ECharts theme + color-blind-safe palette; the scroll legend
       toggles symbols on/off (all on by default — same pattern as the price chart's
       legend, ROADMAP 6.3, rather than a separate checklist). A category with no score
       leaves that axis blank for the symbol; symbols with no analysis row are listed and
       skipped. Wired into the Output Action menu as "🎯 Category scores radar".
     - ✅ IMPLEMENTED (2026-06-15): Fundamentals GROWTH LINE chart (charts.py
       view=fundamentals_line) — the multi-symbol comparison the bar chart deliberately
       deferred. One parameter (shared param picker, same as the bar) across EVERY selected
       symbol, one line each over its reported periods, annual/quarterly toggle. Uses a TIME
       x-axis (not category) so symbols on different fiscal calendars align by date. A
       Scale toggle: "Indexed (100)" rebases each symbol to 100 at its first POSITIVE period
       (compare growth trajectories regardless of size; a ≤0 base can't be indexed → that
       symbol is listed + skipped, flip to Actual), "Actual" shows reported values with ONE
       shared B/M/K divisor across symbols. Missing periods break the line (period-aware gap,
       NOT the price chart's 7-day threshold). Values reuse the SAME deriver as the bar
       (metrics formulas for ratios, split-adjusted EPS) — extracted to a shared
       `_period_values()` so a value is computed once. Legend = left vertical scroll +
       All/Invert (same as price/radar). Wired into the Output Action menu as
       "📉 Fundamentals growth lines".
       - REFINED 2026-06-15: (1) the chart now TRIMS to the most recent unbroken run —
         per symbol it finds the first date after its last gap (a skipped period OR a NaN
         value, `_last_clean_run_start`), then cuts EVERY symbol at the LOWEST (earliest)
         such date so they share one window (Indexed rebases to 100 there); a gap-free
         param keeps full history. Caption shows "from <date> (after last gap)". (2) the
         Scale toggle (Indexed/Actual) now signals selection by BACKGROUND only (soft
         green active, transparent inactive) instead of the theme-red text/border —
         scoped CSS in app.py (segmented-control `st-key-fundline_mode`).
     - ✅ IMPLEMENTED (2026-06-15): Dividend yield chart (charts.py view=dividend_line) —
       each selected symbol's dividend yield over CALENDAR periods (annual = calendar year,
       quarterly = calendar quarter; TTM excluded from the growth view per the action-menu
       spec), one line each. Yield per period = summed `dividends` ÷ the period-end RAW
       `close` × 100 — divides the NOMINAL per-share cash by the contemporaneous (unadjusted)
       close, NOT adj_close, so historical yields aren't inflated by back-adjustment
       (`_load_div_prices` reads close+dividends from ohlcv.db full-history; `_period_yields`
       resamples YE/QE). A period with no payout is a real 0%. Reuses the shared
       `_growth_line_options` and the same last-gap trim. Wired into the Output Action menu
       under a "Dividends" group as "💰 Dividend yield".
       REFINED 2026-06-16: (1) the still-running current period is DROPPED — its
       dividends/price aren't final (its period-end label falls past the last bar), so only
       completed periods show. (2) Rendered on a CATEGORY x-axis labelled per period
       ("2025" / "2025-Q3") via `_period_label`, since a time axis pushes a Dec-31 period-end
       visually under the next year; quarterly thins labels to Q1-only (full label still in
       the tooltip) at a smaller font. (3) Gained the Actual/Normalized scale toggle (default
       Actual; Normalized rebases each symbol to 100 at its first positive period) — and the
       Fundamentals line's old "Indexed (100)" toggle was RENAMED to "Normalized" with the
       order reversed + Actual default to match. (4) Both growth-line charts now drop symbols
       whose actual values are all 0, and PERSIST the legend show/hide selection across the
       Actual/Normalized + period switches (legendselectchanged → session_state →
       legend.selected, same pattern as the price chart).
       ✅ FUNDAMENTALS HEAT MAP IMPLEMENTED (2026-06-20) — metrics heat map (charts.py
       view=heatmap, Output Action menu): symbols × metrics, each cell colored by a
       per-parameter SCORING RULE (orange=strong, blue=weak), ranked across the universe.
       Backed by a new per-parameter rules system — see the "Scoring rules system" addition
       under Topic 4.4. ✅ ENHANCED 2026-06-21: (a) the heat map columns now default to the
       Output table's SHOWN columns (passed via ?cols=; hidden ones excluded) and use the
       shared param_picker popover (▸ info hints); (b) CLICK a column header to sort the
       symbol rows by that metric's strength (single-column, toggles direction); (c) a NEW
       SCORES heat map (view=scores_heatmap, action "🏅 Scores heat map") — the 5 category
       scores + Overall + RS Rank for the selected symbols, same grid/sort via a shared
       _heatmap_core. Still pending in 6.2: the Koyfin link. (Dividend yield BAR dropped
       2026-06-16 — practically identical to the line, per the user. The YIELD heat map
       variant was DROPPED 2026-06-24 — current yield is already a column in the metrics
       heat map and yield-over-periods is the dividend line chart, so the rows×periods grid
       added no new data; the user reviewed and judged the existing views enough.)
     - [ Action ] button opens grouped dropdown, one action at a time, each opens a new browser tab
     - Action menu structure:
       - Normalized Charts
       - Fundamentals
           - Parameter bar charts — ORIGINAL note: "grouped by metric, per share/ratio,
             time period selector" (multi-symbol comparison on one period). REVISED
             2026-06-13 (✅ shipped, see 6.2 above) to one-symbol × one-parameter ×
             over-periods; the multi-symbol-comparison bar is a later step.
           - ✅ Parameter heat map charts (blue-to-orange scale, color-blind safe) — shipped
             2026-06-20 (view=heatmap), colored by the scoring-rules `goodness()` engine;
             enhanced 2026-06-21 (follows Output shown columns, click-to-sort rows) + a
             companion ✅ Scores heat map (view=scores_heatmap)
           - ✅ Radar chart (5 category scores: Value, Quality, Growth, Momentum, Income) — shipped 2026-06-15, see 6.2 above
           - ✅ Parameter growth line charts (annual/quarterly selector, all periods, gaps shown as breaks) — shipped 2026-06-15, see 6.2 above (multi-symbol, Actual/Normalized scale toggle)
       - Dividends
           - ~~Yield bar chart~~ — DROPPED 2026-06-16 (practically identical to the line)
           - ~~Yield heat map chart~~ — DROPPED 2026-06-24 (no new data over what already
             ships: current yield is a column in the metrics heat map and yield-over-periods
             is the dividend line chart; user reviewed and judged those enough)
           - ✅ Yield line chart (annual and quarterly only — TTM excluded) — shipped 2026-06-15, refined 2026-06-16, see 6.2 above (multi-symbol, calendar-period yield, Actual/Normalized, view=dividend_line)
       - Analyze on external site
           - Finviz → https://finviz.com/screener?v=111&t=SYM1,SYM2,...
           - Yahoo Finance → https://finance.yahoo.com/quotes/SYM1,SYM2,.../
           - TradingView → one tab per symbol (no multi-symbol URL support)
           - Koyfin → URL format to be confirmed during coding phase
     - All chart actions include: scrollable symbol checklist (all checked by default), color-blind safe palette (no red/green)
     - All Fundamentals/Dividends period selectors: TTM (default), latest quarter, latest annual, 3Y average, 5Y average
     - Dividend growth line charts: annual and quarterly periods only (TTM excluded)

   - ✅ Subtopic 6.3 — Normalized price chart details
     - ✅ IMPLEMENTED (2026-06-12): ui/pages/charts.py, view=price — rendered with
       Apache ECharts (streamlit-echarts==0.4.0; 0.7.0 breaks on the current Streamlit's
       components.v2). adj_close indexed to 100 at the window start; dark theme + bright
       color-blind-safe palette (Paul Tol "vibrant", local _COLORWAY); axis-trigger
       tooltip = unified hover (every symbol named + colored + valued at the cursor);
       clickable scroll legend toggles lines (replaces a separate symbol selector);
       gridlines; NaN gap breaks (connectNulls=False, >7-day threshold). Range: Period
       presets 1Y/3Y/5Y set the loaded window, ECharts dataZoom slider + wheel/drag pick
       sub-ranges (native arbitrary-range selector — no custom date inputs), toolbox
       restore = reset to full view. Chose ECharts after trialing Plotly (worked, less
       finance-native) and TradingView Lightweight Charts via streamlit-lightweight-
       charts-pro (rejected: the wrapper ignored theming and dropped legend names). The
       trial used disposable git branches off tag plotly-charts-baseline.
     - All symbols start at 100% (normalized), one line per symbol
     - Inline symbol labels at end of each line (no legend), offset to avoid overlap
     - Time period buttons at bottom: 1Y, 3Y, 5Y, custom date range (calendar input)
     - Plotly used: zoom/pan, crosshair tooltip with values per symbol per date
     - Block-select a date range on chart → auto-populates custom date range inputs
     - Line breaks for data gaps (no interpolation)

⏭ Topic 7 — Directory Structure & Module Design (SKIPPED — handled in Claude Code)
   - Directory layout, module naming, and config file structure will be decided during implementation
   - Key architectural decisions captured in Key Decisions Made above

## Future Ideas (post-MVP, no timeline)
- Cloud VM for daily fetch + analysis (added 2026-06-17, LOW priority — brainstormed, not scheduled) — move the weekly fetch+analysis to a cloud VM that maintains the DBs and runs **daily** outside market hours; user retrieves only the small analysis data locally. Discussion outcome, not yet a committed design:
  - **Feasible — it's just Python + SQLite, nothing machine-bound.** A clean VM also won't need the local TLS-interception hack (`core/net.configure_tls()` stays but is harmless there).
  - **Retrieval split by DB size:** analysis.db ~19 MB + indices.db ~10 MB + financials.db ~202 MB are cheap to sync daily; **ohlcv.db ~6.7 GB stays on the VM, used only to compute analysis.** Two shapes: (A) pull the small DBs, run the UI locally; (B) run everything incl. Streamlit on the VM and browse to it.
  - **Charts without the 6.7 GB file (user's preferred refinement):** in shape A, fetch the *few* charted symbols' OHLCV **on-demand from yfinance locally** (small home-IP load, safer than the datacenter IP), backed by a tiny local cache db. Contained change — swap the 3 ohlcv.db readers in `ui/pages/charts.py` (`_adj_close_history`, split-events reader, raw-close+dividends reader) to an on-demand source; an `auto_adjust=False` + actions pull supplies adj_close, raw close, dividends, splits.
  - **Rate limits on a datacenter IP:** no published yfinance limit — it's IP-reputation based and *degrades* (rising 429/`999`), not a clean cap. Expect a VM to give **≤ home's ~100/min**, twitchier. With ~37,756 active+validated symbols and an outside-market-hours weeknight window of ~16 h (≈4:15pm ET → ~8am ET), break-even is **~40/min sustained**; target holding ~60/min, lean on the ~63 h weekend window + **liquidity tiering** (liquid names weeknights, long tail on weekends) as the release valve. VPN-location rotation mostly treats the wrong symptom — commercial VPN exits are themselves flagged datacenter IPs.
  - **Find the "golden speed" empirically on the VM:** gentle ramp-up probe (step the rate, hold each step 20–30 min, watch the *sustained* error rate), take ~70–80% of the highest clean rate; ideally wire **AIMD adaptive backoff** into the fetcher so it rides the drifting limit instead of a hardcoded number. The IP has memory — stop and wait out any block before continuing a probe.
  - **RAM-reduce analysis to fit a small/cheap VM (user wants this):** current ~6.6 GB peak is the deliberate "load 2 yr OHLCV once, index into a per-symbol dict, go fast" trade. Since a VM has all night, trade time for RAM: **batch the per-symbol phase** (read a batch's OHLCV → compute snapshot rows → drop → next batch), then run the **universe-wide steps once** (`peers`/`scoring` percentile ranks/`rs_rank`) over the small accumulated 126-col snapshot frame — those need every symbol but only the *small* rows, never the OHLCV, so heavy memory and all-symbols need never overlap. Peak RAM → O(batch); batch size is the RAM dial; **float32 OHLCV** roughly halves the frame for ~free. The subset-merge path (`pipeline._merge_existing`) is partial existing machinery. Plausibly a **4 GB VM at ~2–4× runtime**. Real refactor of `run_analysis()` (not a config knob); universe-wide steps MUST run after all batches accumulate.
- News sentiment analysis — fetch news via Finviz and Polygon news APIs, derive sentiment scoring per symbol from article content. Never designed in detail — start fresh when the time comes.
  - ✅ NEWS FOUNDATION SHIPPED (the on-demand pieces this would build on, NOT sentiment itself):
    - On-demand **News action** (Charts `view=news`, `data_layer/news.py`): per-symbol
      headlines from yfinance + Polygon + finviz, deduped, code-only Company-vs-Context split.
      On-demand only — never in a fetch/analysis run, no DB.
    - **Report pipeline** (committed e5e4a6c, 2026-06-24, verified end-to-end): generic
      reportlab PDF engine (`core/pdf.py`) + `reporting/` package (`generate()` registry +
      `store`). Two Charts-news actions: **"Generate news PDF"** (headlines as clickable
      links → `reports/`, newest `REPORTS_KEEP` kept) and **"Generate AI news reports"** —
      scrapes the full article bodies behind the Company links (`news.fetch_article`,
      trafilatura, soft-fail on paywall/bot-block) into per-symbol
      `<symbol>_ai_news_report.md` in `AI_NEWS_REPORTS_DIR`, designed to be read by an AI.
    - WORKFLOW NOTE: turning a scraped `.md` into a SUMMARY (and a `_summary.pdf`) is
      currently a MANUAL Claude step — the shipped code stops at writing the `.md`.
      Automating summary→PDF (e.g. via the Claude API) is a possible next step, NOT built.
- Standalone executable (added 2026-06-12) — package the whole app so it launches by double-click, no manual `streamlit run`.
  - **Feasible, not blocked.** The app is a local web server, so the "exe" starts the Streamlit server, opens the browser at localhost, and quits on tab close (autoshutdown.py already does the quit).
  - **Recommended approach:** freeze a bundled Python env + a small launcher, OR a PyInstaller / Nuitka one-folder build. NOT stlite/WASM — it can't run the native deps (curl_cffi, pyarrow, SQLite writes, truststore).
  - **Main prerequisite refactor — writable paths when frozen.** Every writable dir hangs off `BASE_DIR = Path(__file__).../..` (settings.py): databases, backups, logs, results, filters. In a frozen build `__file__` is inside the read-only bundle. Detect `sys.frozen` and redirect those dirs to a user data folder (e.g. `%LOCALAPPDATA%\FAMarket`); read-only code/assets stay in the bundle. Contained change — all paths already funnel through `BASE_DIR`.
  - **Other care points (all solvable):** collect native binaries (curl_cffi/libcurl, pyarrow, numpy/pandas, lxml) via freezer hooks; Streamlit packaging needs `copy_metadata("streamlit")` + its static assets/hidden imports; resolve the TLS CA-bundle paths in core/net.py relative to the bundle (`sys._MEIPASS`); `.env`/API keys fine for personal single-machine use (user supplies own keys if ever distributed); confirm the freezer supports the Python version in use (3.14 is new — tooling may lag).
  - **Budget as two contained pieces:** (1) writable-paths-when-frozen, (2) freezer config with the right hooks. Nothing in the current design needs changing before then.

✅ Topic 8 — Build Phases
   - ✅ Subtopic 8.1 — What to build first (MVP scope)
     - Build each layer fully before moving to the next (Data → Analysis → UI)
     - No thin end-to-end slices — complete each phase fully
   - ✅ Subtopic 8.2 — Phase breakdown & sequencing
     - Phase 1: Data Layer
       - Symbol discovery: Polygon (primary) + SEC EDGAR (gap fill)
       - Symbol normalization (Polygon → yfinance/E*Trade format)
       - Symbol state management (is_active, is_validated flags)
       - SQLite wrapper with explicit methods (append, replace, upsert)
       - All API fetchers: yfinance, FMP, E*Trade, FRED, Polygon
       - Fetch control panel in Streamlit (Group 1: symbol APIs, Group 2: data APIs)
       - Rate limiting + retry logic (ratelimit + tenacity)
       - Logging (terminal + rotating log file)
       - config/settings.py for all non-sensitive settings
       - Single-threaded (multiprocessing deferred to Phase 2 optimization)
     - Phase 2: Analysis Layer
       - All metrics calculated from Phase 1 databases
       - Peer comparisons (sector/industry medians)
       - Scoring & ranking
       - Intrinsic value calculations (Graham, Lynch, DCF using FRED Treasury yield)
       - Full recalculate every run (clean slate; subset runs merge into the existing table — see 4.2)
       - Only processes is_active=True + is_validated=True symbols
     - Phase 3: UI Layer
       - Streamlit filter interface (block-based, AND/OR logic)
       - Output interface (results table, action menu, charts)
       - Settings page (edits config/settings.py)
       - Fetch control panel (integrated into UI)
   - ✅ Subtopic 8.3 — What each phase should produce (runnable milestone)
     - Phase 1 done when: all APIs fetched, data stored correctly in symbols.db, quotes.db, ohlcv.db, financials.db, macro.db — verified via VSCode SQLite extension + test scripts
     - Phase 2 done when: analysis.db fully populated with all metrics, scores, peer comparisons — verified via SQLite + test scripts
     - Phase 3 done when: full Streamlit UI running locally end-to-end
   - ✅ Subtopic 8.3b — Development testing strategy
     - Full symbol universe fetch is fast (Polygon) — no special handling needed
     - During fetcher development: manually select a small subset (~20-50 symbols across different security types) for testing
     - Delete .db files to reset to a clean slate and retest from scratch — system auto-detects empty databases as initial load
     - Full universe fetch (40,000+ symbols) runs once everything is working — Frank is comfortable waiting hours for the final run

   - ✅ Subtopic 8.4 — Phase 0 — Project Setup (runs before Phase 1)
     - Set up repo + git
     - Standard Python project folder structure (done properly, with explanations for learning)
     - Virtual environment setup
     - requirements.txt or pyproject.toml
     - .env template + .gitignore
     - SQLite wrapper skeleton (with append/replace/upsert methods)
     - config/settings.py skeleton
     - Topic 7 (directory structure) effectively handled here in practice
     - IDE: VSCode confirmed; Claude Code via terminal inside VSCode or VSCode extension — to be decided at start of Phase 0
   - ✅ Subtopic 8.5 — Phase 2 optimization (multiprocessing)
     - Wrap Group 2 API fetchers in multiprocessing (Group 1 stays sequential)
     - Write queue pattern for concurrent DB writes
     - Auth for E*Trade runs in main process before workers start, token passed to workers

   - FETCH CONTROL PANEL (Streamlit UI):
     - Group 1 — Symbol Discovery (always runs first, sequential):
       - ☑ Polygon
       - ☑ SEC EDGAR
     - Group 2 — Data Fetch (runs after Group 1, single-threaded Phase 1 / parallel Phase 2):
       - ☑ yfinance
       - ☑ FMP
       - ☑ E*Trade
       - ☑ FRED
     - Group 2 locked until Group 1 completes
     - ✅ 2026-06-12: indices excluded from Group 2 entirely (replaces the
       implicit "minimal scope-tier" handling for `index`). They are
       reference-only, carry no fetchable fundamentals, and are typed `index` at
       discovery time, so `orchestrator.load_fetch_universe()` drops
       `security_type == "index"` before any fetcher runs. Benchmark OHLCV, if
       ever needed, stays an analysis-layer concern.
     - Live log output shown in UI during fetch run
     - ✅ 2026-06-19: the fetch now runs as its OWN detached OS process
       (`data_layer/launcher.py` spawns `scripts/run_fetch.py` with Windows
       CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP — no popup console window, and
       Windows doesn't kill children when the parent exits), so it KEEPS RUNNING after the
       app is closed. Replaces the previous in-Streamlit background *thread* +
       stop-on-tab-close design (the auto-shutdown hook no longer kills the fetch —
       it just prints a notice; `core/autoshutdown.py` + `app.py`). Cross-process
       coordination: `data_layer/run_state.py` (a `state/fetch_run.json` lifecycle
       file — launching → running → done/error/cancelled, with the worker's own PID
       for liveness) is the single source of truth for "is a fetch running"; Stop is
       a cross-process flag file (`settings.FETCH_STOP_FILE`, written/polled via
       `data_layer/cancel.py`) that still unwinds at the next batch boundary. Because
       the fetch is out-of-process, the **live log view was removed from the UI** —
       the page is now a controller (run status banner + last-run summary, buttons
       gated on `run_state.is_active()` so a second fetch can never start); watch
       `logs/famarket.log` for progress. Removed `cancel.stop_for_shutdown` /
       in-thread worker tracking. Standalone `python -m scripts.run_fetch` (now with
       `--analysis-only`) writes the same state file.
     - ✅ 2026-06-19: hardened that detachment against the OS session ending.
       Two follow-up fixes: (1) `data_layer/launcher.py` adds
       `CREATE_BREAKAWAY_FROM_JOB` so the fetch escapes the parent (VS Code)
       Windows **Job Object** — whose default policy was killing the "detached"
       fetch the moment the editor closed (CREATE_NEW_PROCESS_GROUP only shields
       console *signals*, not job membership); falls back to spawning without the
       flag when a job forbids breakaway (OSError winerror 5). (2) `core/shutdown_guard.py`
       (NEW) — a daemon thread with a hidden Win32 window that answers
       WM_QUERYENDSESSION + calls ShutdownBlockReasonCreate, so a logoff / shutdown /
       restart while a fetch runs raises a "wait?" warning instead of silently
       killing it (started/stopped around the run in `scripts/run_fetch.py`).
       Tested: closing VS Code mid-fetch now survives; logoff + shutdown both warn.
       Forced "shut down anyway" is a deliberate HARD KILL (the run is resumable
       from its last committed batch, and Windows only grants a ~5s block window).
     - Each API can be run independently (e.g. re-run just yfinance after a failure)

   - FRED DATA:
     - Fetched via FRED API (free, fast, minimal overhead)
     - Data stored in macro.db (not per-symbol — macro context only)
     - Used in Analysis layer for DCF calculations (Treasury yield = ^TNX cross-check available via yfinance)
     - Macro series: 10-year Treasury yield, Federal Funds Rate, CPI, GDP growth rate

✅ Topic 9 — Testing Strategy, Error Handling & Data Quality Validation
   - ✅ Subtopic 9.1 — Testing approach
     - No pytest — manual verification only (Frank already knows this approach)
     - Ad-hoc testing scripts written as needed during build
     - VSCode SQLite viewer for visual database inspection
     - No pre-built verification scripts — Frank builds his own as he goes

   - ✅ Subtopic 9.2 — Error handling
     - API fails repeatedly → that fetcher stops, other fetchers continue unaffected
     - Failure logged clearly in the run log
     - Analysis still runs after all fetchers finish or stop — uses whatever data is available
     - fetch_status table tracks last successful fetch per (symbol, fetcher_name) — composite primary key
     - On restart → fetch_status determines which symbols are already done → skips them, resumes from where it left off
     - 5-day lock per (symbol, fetcher_name) after successful fetch — symbol not re-fetched until lock expires
     - Lock is per fetcher function (e.g. yfinance_quotes and etrade_quotes have independent locks)
     - Weekly cadence naturally falls outside 5-day window → normal weekly runs always fetch fresh data
     - ✅ 2026-06-12: the 5-day lock and the viability gates are now INDEPENDENT
       switches (replaces the earlier single `respect_lock` that bypassed everything
       at once — that bundling was an unintended miscommunication).
       - `respect_lock` (UI "Respect 5-day fetch lock" / CLI `--no-lock`) gates ONLY
         the cadence lock.
       - Abandonment (no-data cap), staleness, and the financials due-date gate are
         the "viability gates", governed solely by `FETCH_ABANDONMENT_ENABLED`.
       - A full "refetch everything" run = lock off AND `FETCH_ABANDONMENT_ENABLED`
         off. The split lives in `fetch_status.classify_skips` + `BaseFetcher.select_due`
         (one source of truth for both a real run and the Report Fetch dry run), and
         the report now shows a separate Locked vs Abandoned column.
     - fetch_status table columns: symbol, fetcher_name, last_fetched, fetch_errors
       - Composite primary key: (symbol, fetcher_name)
       - fetcher_name stored as plain string e.g. "yfinance_quotes", "etrade_quotes"

   - ✅ Subtopic 9.3 — Logging
     - Summary-level only — no symbol names, no per-value noise
     - Timestamp prefix on every log entry (via Python logging %(asctime)s formatter)
     - Log format per batch: 2026-06-06 19:42:11 [fetcher_name] Batch X/Y — Fetched: N | Success: N | Failed: N | Remaining: N
     - Sanitize fixes are silent — not logged individually

   - ✅ Subtopic 9.4 — Data quality / sanitize functions
     - Each fetcher has its own paired sanitize function (e.g. sanitize_yfinance_quotes())
     - Flow per fetcher: fetch raw data → sanitize → conditional enrichment → sanitize enrichment → write to database
     - Sanitize operates on raw API response before anything touches the database
     - Two levels of response:
       - Field-level fix: bad individual value → replace with NaN, keep the rest (e.g. inf → NaN, "N/A" → None)
       - Record-level reject: data fundamentally broken → return empty, skip entire record (e.g. price = 0 or None)
     - Conditional enrichment based on security type detected in base fetch data:
       - ✅ IMPLEMENTED — ETF + MUTUALFUND → run `ticker.funds_data.fund_overview`,
         flatten each key to a `fund_<key>` column on the same quote row
         (`fund_categoryName`, `fund_family`, `fund_legalType`), plus the `info`
         scalars `totalAssets/navPrice/fundFamily/category`. Best-effort: a
         funds_data failure never rejects the valid quote. See
         `data_layer/fetchers/yfinance_fetcher.py::_enrich_quote` / `_fund_overview`.
         This is shipped — do NOT re-assess it as future work.
       - (Future) ETF deep enrichment → holdings, expense ratio, tracking error
       - Each enrichment call also goes through its own sanitize pass (`_clean_value`)
     - Enrichment data written to same table as base fetch, as additional columns
     - NULL for security types where enrichment doesn't apply
     - Schema grows dynamically via ALTER TABLE ADD COLUMN (consistent with existing design)
