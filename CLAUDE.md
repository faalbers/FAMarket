# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

FAMarket is a local, Python-based stock-screening system: fetch US-market data
from free APIs → compute fundamental/technical metrics → screen via a Streamlit
UI. The complete design is already brainstormed — **read `ROADMAP.md` before any
non-trivial change**; it records every architectural decision and the build
sequencing. `ROADMAP.md` is a **living document**: when a design decision is
changed or refined in agreement with the user, update the affected entries
in-place (mark with the date and what it replaced; shipped items get ✅).
Never rewrite it wholesale, and never change a decision the user hasn't agreed
to. `Stock_Screening_Analysis.md` is the original blueprint — that one stays
read-only.

Build status: the **data layer** (`core/`, `config/`, symbol discovery, and the
yfinance/EDGAR/FRED fetchers) is functional. The **analysis layer** is now
complete — `_periods`, `metrics`, `technical`, `intrinsic_value`, `_stats`,
`peers`, `scoring` (category scores + Overall, percentile-rank) and universe-wide
`rs_rank` all work; `pipeline.run_analysis()` assembles and writes `analysis.db`
(126 cols — includes a sector/industry-derived `screen_type` column via
`analysis_layer/screen_type.py`, and a persisted `rs_raw` input column for
subset-run re-ranking) and is wired into the orchestrator as Group 3
after each fetch. On **full runs only** it also builds daily base-100 **sector &
sub-industry index series** (`analysis_layer/sector_index.py`, SPDR Select Sector
formula — float-MC weights, current capping, quarterly rebalance) and writes them to a
dedicated `indices.db` (long/tidy `sector_industry_index` table). Index history is **data-driven**
(starts where `financials.db` share coverage broadens, `INDEX_START_MIN_REPORTERS`),
read via a dedicated memory-efficient deep `adj_close` read of the liquid constituents
only — decoupled from `ANALYSIS_OHLCV_LOOKBACK_DAYS`. Each run logs its peak RAM via
`core/meminfo.py` (Win32 ctypes, no psutil). The **UI** is being built page by page: Fetch Control, Settings,
Filter, Output and Calibration are functional. Calibration
(`ui/pages/calibration.py`) is the peak-detection tuning tool — sliders for
`PEAK_PROMINENCE`/`PEAK_DISTANCE`, a price chart with detected swing highs/lows
overlaid (via the shared `analysis_layer/technical.trend_signals`),
price-behavior-picked sample stocks, and Save to `settings.local.json`. Shared
ECharts dark theme/palette now live in `ui/chart_theme.py`. The Output Action menu
charts (`ui/pages/charts.py`, routed by `?view=`) now include the normalized price
chart and a **Fundamentals bar chart** (`view=fundamentals_bar`): one symbol × one
parameter over its reported periods (annual/quarterly), read per-symbol from
`financials.db`. Its ratios reuse the **same formula functions as the analysis
snapshot** — `metrics.py` exposes them as named funcs + `RATIO_PERIOD_METRICS` /
`RAW_PERIOD_FIELDS` registries so a ratio is defined once, never duplicated in the UI.
The price view also has a **sector/industry relative-strength** selector (the first
`indices.db` consumer): a single-select tree of the charted symbols' sectors/industries
that, when one is picked, replaces the chart with each in-group symbol plotted as
`symbol_norm − index_norm + 100` against that group's index — with a 3-way toggle
(Relative / Symbols / Index) that swaps content at the same window + base-100.
The
Filter page (Topic 5) is backed by `ui/filter_registry.py` (per-`screen_type` metric
applicability) + `ui/filter_engine.py` (block model + `.filt` JSON). Each Run Filter
persists a run file (`ui/output_runs.py`: parquet+json in `results/`, newest
`OUTPUT_RUNS_KEEP` kept) and auto-opens `/output?run=<id>` in its own browser tab;
the sidebar Output page is a recent-runs launcher (.prms column sets, multi-sort,
row-select + Action menu still to come). Build order is
strictly **Data → Analysis → UI**, each layer completed fully before starting the
next (no thin end-to-end slices).

## Commands

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1   # create + activate venv
pip install -r requirements.txt                        # install deps
copy .env.template .env                                # then fill in API keys
streamlit run app.py                                   # launch the UI
python -m scripts.discover_symbols --edgar             # symbol discovery (EDGAR, no key)
python -m scripts.discover_symbols --show              # summarize symbols.db
```

There is **no test framework** — this is a deliberate decision (ROADMAP Topic 9.1).
Verification is manual: ad-hoc scripts written as needed, plus the VSCode SQLite
viewer to inspect the `.db` files. Do not add pytest or a CI test suite unless the
user explicitly asks. To reset state, delete the `.db` files in `databases/` — the
system auto-detects empty databases as an initial-load run.

## Architecture

Three intentionally decoupled layers plus shared infrastructure:

- **`data_layer/`** — symbol discovery (Polygon primary, SEC EDGAR
  operating-company + mutual-fund gap-fill; gap-fill sources are additive-only —
  never overwrite an existing symbol), symbol normalization and state flags, and
  per-API fetchers in
  `data_layer/fetchers/` (yfinance, polygon, fmp, etrade, fred, edgar). Writes to
  the per-type databases.
- **`analysis_layer/`** — reads the data DBs and **fully rebuilds `analysis.db`
  every full run** (clean slate via `Database.replace`, no delta tracking).
  **Subset runs merge instead of wipe**: only the subset is loaded/recomputed,
  its rows are spliced into the existing table (`pipeline._merge_existing`), and
  peers/scoring/rs_rank re-run over the merged frame so ranks stay
  universe-wide (`rs_raw` is persisted in `analysis.db` for this). Modules:
  `metrics`, `technical`, `peers`, `intrinsic_value`, `scoring`, orchestrated by
  `pipeline.run_analysis()`. Only processes symbols with `is_active=True` AND
  `is_validated=True`.
- **`ui/`** + **`app.py`** — Streamlit multipage app (registered via
  `st.navigation` in `app.py`, pages under `ui/pages/`): Fetch Control, Filter,
  Output, Calibration, Settings.
- **`core/`** — `database.py` (the SQLite wrapper), `logging_config.py`,
  `backup.py`. Used by every layer.
- **`config/`** — `settings.py` (all non-sensitive, UI-editable settings),
  `type_map.py` (security-type normalization), `param_hints.py` (UI hover hints).

### Conventions that cut across the codebase

- **Separate SQLite DBs per data type** (`symbols`, `quotes`, `ohlcv`,
  `financials`, `analysis`, `macro`, `indices` — paths in `config/settings.py`). There are
  **no cross-database SQL joins**; merge in pandas instead.
- **The SQLite wrapper is opinionated by design** (`core/database.py`): never a
  generic write. Use the verb that names the intent — `append` (add rows, e.g.
  new OHLCV dates), `replace` (drop + rewrite, e.g. the analysis rebuild), or
  `upsert(key=...)` (insert-or-update). pandas DataFrames are the interchange
  format.
- **Schema grows dynamically**: every API field is its own column (no JSON
  blobs); unknown columns are added via `ALTER TABLE ADD COLUMN` automatically on
  write. `NULL` means "not applicable" for that symbol.
- **Row-level provenance**: when a table draws from more than one source, every
  row carries a `source` column naming its origin (e.g. `financials`: `yfinance`
  primary, `edgar` deep-history backfill). Single-source tables (`quotes`,
  `ohlcv`, `macro`) omit it. Add `source` to a table only when a second source
  starts writing to it.
- **Secrets vs settings**: API keys live only in `.env` (git-ignored; see
  `.env.template`), loaded via `config/secrets.py` (use `secrets.require("KEY")`
  for a clear error when missing). Everything else (paths, batch sizes, rate
  limits, scoring weights, indicator params) lives in `config/settings.py` as the
  committed **defaults** — keep it a plain module of constants. The Settings UI
  **never writes settings.py**: on Save it persists only the changed keys to a
  gitignored, machine-local `settings.local.json` (flat `{dotted.path: value}`),
  which `config/settings_overrides.py` lays on top of the defaults at the bottom of
  `settings.py` (type-coerced to each default; unknown/malformed keys ignored).
  Delete that file to reset all settings to defaults. (Replaced the old in-place
  AST rewrite `config/settings_io.py`, 2026-06-12.)
- **TLS / certificates**: this machine sits behind TLS interception (a proxy/AV
  re-signs HTTPS with a private CA in the Windows trust store). `certifi` alone
  fails with CERTIFICATE_VERIFY_FAILED, so `core/net.configure_tls()` MUST run
  before any network call — it patches stdlib/`requests` via `truststore` and
  points `CURL_CA_BUNDLE` (for yfinance's `curl_cffi`) at a merged
  certifi+OS-store bundle. `app.py` and `symbols.run_discovery()` already call it;
  any new entry point (scripts, fetch orchestrator) must too.
- **Canonical symbol key**: the normalized form (`symbol`, yfinance/E*Trade
  convention) is the join key across every database; the raw Polygon ticker is
  kept in `polygon_symbol`. `symbols.normalize_symbol()` does the conversion.
- **Fetchers** receive the *full* symbol list and filter internally to the
  security types they handle. Each owns a paired sanitize function; flow is
  `fetch raw → sanitize → conditional enrichment → sanitize → write`. Rate
  limiting via `ratelimit`, retries via `tenacity`, both keyed off
  `settings.RATE_LIMITS` / `RETRY_*`.
- **Resumability**: `fetch_status` (composite PK `symbol, fetcher_name`) tracks
  the last successful fetch and an error counter; a 5-day per-fetcher lock skips
  recently-fetched pairs. Financials additionally defer symbols whose next
  statement can't exist yet (filing cycle + `FINANCIALS_REPORT_LAG_DAYS`,
  `staleness.financials_not_due`) — a deferral, not abandonment; forced runs
  bypass it.
- **Logging is summary-level only** (`core/logging_config.py`): batch progress
  and failures, no per-symbol/per-value noise; sanitize fixes are silent.
- **Prices**: all price-based calculations use `adj_close` of the **last
  completed trading session** (via `pandas-market-calendars`) — never intraday.
  OHLCV writes are gated the same way: `sanitize_ohlcv` drops any bar past the last
  fully-settled session (close + 15 min, via `last_completed_session()`), so the
  store never holds an intraday / non-final close. The fetch pipeline itself runs
  at any time (there is no market-closed gate) — this OHLCV session gate is the
  sole intraday guard, so while the market is open today's in-progress bar is
  simply omitted from the write.
- **Analysis ratios are compute-and-reconcile**: fundamental ratios are computed
  from `financials.db` on one convention (canonical `adj_close`, TTM = last 4
  quarters), cross-checked against yfinance's equivalent in `quotes.db` with a
  summary WARNING on divergence, and fall back to the yfinance value (tagged
  `valuation_basis="yfinance"`) only when inputs are missing or the reporting
  currency mismatches. Percentage-valued metrics are **stored as percent numbers**
  (`12.5`, not `0.125`), and every `config/param_hints.py` entry declares a `unit`.
- **`config/param_hints.py` is the ONE canonical hint registry** — `name`, `category`,
  `unit`, `what_it_is`, `how_to_use`, `vs_peers` per key. **System requirement: every
  parameter exposed for filtering (every `ui/filter_registry.py` base metric) MUST have a
  `param_hints.py` entry** — add the hint in the same change that adds the metric. The UI
  reads hints ONLY from here (Filter picker ▸ info, Output column headers, the radar's
  category info row, etc.) — never hardcode a description in a page. Category scores
  (`*_score`) are registry keys too (`category: "Score"`). To verify nothing slipped:
  cross-check `R.BASE_BY_KEY` against `PARAM_HINTS` (currently 69/69, 0 missing).
- **Charts** are rendered with **Apache ECharts** (`streamlit-echarts`, pinned
  `==0.4.0` — 0.7.0 uses `st.components.v2` and breaks on the current Streamlit).
  Use a color-blind-safe palette (no red/green; blue-to-orange for heatmaps); the
  price chart's dark theme + bright palette + gap-break helper now live in
  `ui/chart_theme.py` (`COLORWAY`/`DARK_*`/`echarts_points`), shared by the price
  chart and the Calibration chart. `settings.CHART_COLORWAY` is the default palette
  for any other chart.
- **Streamlit live updates** use `st.fragment(run_every=…)`, never a
  `time.sleep() + st.rerun()` poll loop — the blocking sleep freezes the script
  between ticks and silently drops widget input (e.g. a Stop click). A button that
  must register during an auto-refreshing view uses an `on_click` callback, not its
  return value. `st.expander` cannot be nested (use `st.popover` inside an expander).
  **Keyed widgets ignore their `value=`/`index=` after the first render** (they read
  their own session_state key thereafter), and a widget's own key **cannot be assigned
  after that widget is instantiated in the same run**. So to repopulate widgets from a
  Load/preset, drive them purely by key, stash the desired values in a NON-widget key,
  and apply them at the TOP of the next run before the widgets render — never set the
  widget keys inside the handler below them (see `ui/pages/filter.py` Security-Type load).
- **Streamlit CSS** all lives in the single `app.py` `<style>` block — never per-page
  (it's injected after `set_page_config` and `app.py` reruns on every navigation, so it
  styles all pages). Two gotchas: selectbox/multiselect **dropdown menus render in a
  popover portaled OUTSIDE `[data-testid="stMain"]`**, so style them with global
  `li[role="option"]` selectors; to target one specific widget, wrap it in
  `st.container(key=…)` and select `[class*="st-key-…"]` (sizing a button down needs
  `!important` to beat Streamlit's own button rules). **When targeting via a
  `st-key-…` hook, use a DESCENDANT selector (`[class*="st-key-…"] button`), never a
  direct child (`… > button`)** — Streamlit (1.58) puts the `st-key-<key>` class on an
  OUTER container with the real widget nested below it, so `>` silently matches nothing
  (this looks like a caching bug but isn't). Direct-child `>` is still fine off a
  widget's OWN class (`.stButton > button`). Verify a selector by grepping the frontend
  JS (`.venv/.../streamlit/static/static/js/*.js`) across ALL chunks before trusting it.
- **Backups**: `core/backup.py` keeps the newest 5 dated copies of every `.db`
  (`{stem}_{YYYY-MM-DD_HH-MM-SS}{suffix}`), taken before each fetch run; one run stamps
  all DBs with a single timestamp, so each stamp is one consistent snapshot.
  Count-capped, not day-based. Same scheme backs up the run log and `config/settings.py`.
  `core/restore.py` reverts the live DBs to a chosen snapshot (Fetch Control danger
  zone), copying the current DBs to `backups/pre_restore/` first as a one-level undo.
- **Per-symbol analysis loops index once**: never filter the full OHLCV/financials
  frame inside the per-symbol loop — a boolean mask per symbol re-scans millions of
  rows. Pre-group by symbol before the loop (`groupby` → dict of pre-sorted slices)
  so each lookup is O(1). See `analysis_layer/pipeline.run_analysis()`.
- **Analysis loads a bounded OHLCV window**: the full ~70M-row table balloons far
  past physical RAM in pandas (measured 57 GB commit on 32 GB → heavy paging), so
  `run_analysis()` reads only the trailing `settings.ANALYSIS_OHLCV_LOOKBACK_DAYS`
  (~2 years; indicators need ≤253 trading days) and only the bar columns it uses.
  Dividends and splits — the sole deep-history consumers (div_growth_5y/streaks,
  EPS split-adjust) — are side-read in FULL as sparse event series and passed to
  `metrics.compute()` separately. **If you add a metric that needs deeper *price*
  history (e.g. 5y price CAGR, historical P/E bands), raise that constant** and
  budget RAM (~8.5M extra rows per +365 days at a 50k universe).

### Things deliberately deferred (don't "fix" them)

- Multiprocessing across fetchers is a **Phase 2 optimization** — Phase 1 is
  single-threaded on purpose.
- Chunked `yf.download()` for OHLCV is a **Phase 2 optimization**. Phase 1 keeps a
  per-symbol loop so OHLCV shares the one rate-limit/retry/`fetch_status` model;
  `download()` is a threaded loop over `history()` (no batch endpoint) whose
  internal threads bypass our `@limits` throttle and risk 429/IP-block. See the
  note in `data_layer/fetchers/yfinance_fetcher.py`.
- Directory/module layout was left to the implementation (ROADMAP Topic 7) — the
  structure here is the realization of that, so extend it rather than restructure
  without checking the roadmap.

## Platform

Windows; default shell is PowerShell (use `;` to chain, `.\.venv\Scripts\Activate.ps1`
to activate). Python 3.14.
