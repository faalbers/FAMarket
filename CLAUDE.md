# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

FAMarket is a local, Python-based stock-screening system: fetch US-market data
from free APIs → compute fundamental/technical metrics → screen via a Streamlit
UI. The complete design is already brainstormed — **read `ROADMAP.md` before any
non-trivial change**; it records every architectural decision and the build
sequencing. `Stock_Screening_Analysis.md` is the original blueprint. Treat both
as read-only design docs (the user maintains them manually).

Build status: the **data layer** (`core/`, `config/`, symbol discovery, and the
yfinance/EDGAR/FRED fetchers) is functional. The **analysis layer** is now
complete — `_periods`, `metrics`, `technical`, `intrinsic_value`, `_stats`,
`peers`, `scoring` (category scores + Overall, percentile-rank) and universe-wide
`rs_rank` all work; `pipeline.run_analysis()` assembles and writes `analysis.db`
(125 cols — now includes a sector/industry-derived `screen_type` column via
`analysis_layer/screen_type.py`) and is wired into the orchestrator as Group 3
after each fetch. The **UI** is being built page by page: Fetch Control, Settings,
and Filter are functional; Output and Calibration remain `st.info` skeletons. The
Filter page (Topic 5) is backed by `ui/filter_registry.py` (per-`screen_type` metric
applicability) + `ui/filter_engine.py` (block model + `.filt` JSON). Build order is
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
  every run** (clean slate via `Database.replace`, no delta tracking). Modules:
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
  `financials`, `analysis`, `macro` — paths in `config/settings.py`). There are
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
  limits, scoring weights, indicator params) lives in `config/settings.py`, which
  the Settings UI page edits in place — so keep it a plain module of
  round-trippable constants.
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
  recently-fetched pairs.
- **Logging is summary-level only** (`core/logging_config.py`): batch progress
  and failures, no per-symbol/per-value noise; sanitize fixes are silent.
- **Prices**: all price-based calculations use `adj_close` of the **last
  completed trading session** (via `pandas-market-calendars`) — never intraday.
  OHLCV writes are gated the same way: `sanitize_ohlcv` drops any bar past the last
  fully-settled session (close + 15 min, via `last_completed_session()`), so the
  store never holds an intraday / non-final close.
- **Analysis ratios are compute-and-reconcile**: fundamental ratios are computed
  from `financials.db` on one convention (canonical `adj_close`, TTM = last 4
  quarters), cross-checked against yfinance's equivalent in `quotes.db` with a
  summary WARNING on divergence, and fall back to the yfinance value (tagged
  `valuation_basis="yfinance"`) only when inputs are missing or the reporting
  currency mismatches. Percentage-valued metrics are **stored as percent numbers**
  (`12.5`, not `0.125`), and every `config/param_hints.py` entry declares a `unit`.
- **Charts** use the color-blind-safe palette in `settings.CHART_COLORWAY` (no
  red/green; blue-to-orange for heatmaps).
- **Backups**: `core/backup.py` keeps a rotating 5-version copy of every `.db`,
  taken before each fetch run.

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
