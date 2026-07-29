# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

FAMarket is a local, Python-based stock-screening system: fetch US-market data
from free APIs → compute fundamental/technical metrics → screen via a React UI
on a FastAPI backend. The complete design is already brainstormed — **read `ROADMAP.md` before any
non-trivial change**; it records every architectural decision and the build
sequencing. `ROADMAP.md` is a **living document**: when a design decision is
changed or refined in agreement with the user, update the affected entries
in-place (mark with the date and what it replaced; shipped items get ✅).
Never rewrite it wholesale, and never change a decision the user hasn't agreed
to.

Build status: the **data layer** (`core/`, `config/`, symbol discovery, and the
yfinance/EDGAR/FRED fetchers) is functional. The **analysis layer** is now
complete — `_periods`, `metrics`, `technical`, `intrinsic_value`, `estimates`
(forward analyst metrics from the `estimates` table in `signals.db`), `_stats`,
`peers`, `scoring` (category scores + Overall, percentile-rank) and universe-wide
`rs_rank` all work; `pipeline.run_analysis()` assembles and writes `analysis.db`
(229 cols — includes a sector/industry-derived `screen_type` column via
`analysis_layer/screen_type.py`, and a persisted `rs_raw` input column for
subset-run re-ranking) and is wired into the orchestrator as Group 3
after each fetch. On **full runs only** it also builds daily base-100 **sector &
sub-industry index series** (`analysis_layer/sector_index.py`, SPDR Select Sector
formula — float-MC weights, current capping, quarterly rebalance) and writes them to a
dedicated `indices.db` (long/tidy `sector_industry_index` table). Index history is **data-driven**
(starts where `financials.db` share coverage broadens, `INDEX_START_MIN_REPORTERS`),
read via a dedicated memory-efficient deep `adj_close` read of the liquid constituents
only — decoupled from `ANALYSIS_OHLCV_LOOKBACK_DAYS`. Each run logs its peak RAM via
`core/meminfo.py` (Win32 ctypes, no psutil).

The **UI is React + FastAPI** (migrated from Streamlit 2026-07-28, which is now
removed). All nine screens are ported: Fetch Control, Filter, Output, Charts,
Sector Indices, Scoring Rules, Parameters, Utilities and Settings. Notable
behaviour, since it differs from the Streamlit original:

* **Fetch Control streams live progress** over SSE (`GET /api/fetch/stream`) —
  run-state transitions plus a tail of `logs/famarket.log`. The Streamlit page
  had none; it read the state file once per rerun. Runs remain detached OS
  processes, so neither the server restarting nor every tab closing interrupts
  one.
* **Output** ships a whole run frame in one columnar response, so sorting,
  column show/hide and selection are instant and local. Multi-level sort is
  shift-click on the headers (up to 4 levels) — the old separate sort panel
  existed only because `st.dataframe` headers can't be clicked.
* **Charts** keep the `?view=` contract (`price`, `fundamentals_bar`,
  `fundamentals_line`, `radar`, `dividend_line`, `heatmap`, `scores_heatmap`,
  `news`, `filter_fail`). The price view keeps the sector/industry
  relative-strength tree and its 3-way Relative / Symbols / Index toggle.
* **Settings** builds its form from a schema the API serves
  (`services/settings_schema.py`), and embeds the peak-detection calibration
  tuner as a section — sliders for `PEAK_PROMINENCE`/`PEAK_DISTANCE` over
  behaviour-picked sample stocks, using the same
  `analysis_layer/technical.trend_signals` a real run uses.
* **News** (`data_layer/news.py`) is unchanged and still on-demand only — never
  part of a fetch run, not a `BaseFetcher`, no DB. Both report actions remain:
  a headlines PDF, and the AI news markdown that `/make_news_reports` turns into
  per-stock summary PDFs.

Filters are still backed by `services/filter_registry.py` (per-`screen_type`
metric applicability) + `services/filter_engine.py` (block model + `.filt`
JSON), and each Run Filter persists a run file (`services/output_runs.py`:
parquet+json in `results/`, newest `OUTPUT_RUNS_KEEP` kept) then opens
`/output?run=<id>` in a new tab. `.filt` files carry both a free-text markdown
`comment` and read-only `ai_instructions`; selections persist via
`services/selection_io.py` (`.syms`/`.prms` in `SELECTIONS_DIR`).

## Commands

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1   # create + activate venv
pip install -r requirements.txt                        # install Python deps
copy .env.template .env                                # then fill in API keys
cd frontend; npm install; npm run build; cd ..         # front end (once, then after UI edits)
python scripts/serve_ui.py                             # launch the UI (serves dist + API)
python -m scripts.discover_symbols --edgar             # symbol discovery (EDGAR, no key)
python -m scripts.discover_symbols --show              # summarize symbols.db
```

`frontend/dist` is gitignored, so a fresh clone must `npm run build` once before
`serve_ui.py` will start (it refuses with a clear message otherwise). For
front-end work use two terminals instead: `python -m uvicorn api.main:app
--reload --port 8765` and `npm run dev` (Vite proxies `/api`, open :5173).
The API port is **8765**, not 8000 — 8000 is taken by a local MCP server on this
machine; `serve_ui.py --port` and `frontend/vite.config.ts`'s proxy target must
stay in sync.

Type-check with `npx pyright api services` and, in `frontend/`, `npx tsc -b`.

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
  `metrics`, `technical`, `peers`, `intrinsic_value`, `estimates` (forward
  analyst metrics — forward EPS/rev growth, forward PEG, EPS-revision
  momentum/breadth, analyst count — read from the `estimates` table in `signals.db`),
  `signals` (earnings-surprise + ownership metrics — avg/last/beat-rate surprise,
  days-to-next-earnings, insider net buying, institutions count — from the
  `earnings_surprise`/`ownership` tables in `signals.db`),
  `scoring`, orchestrated by `pipeline.run_analysis()`. Only processes symbols with
  `is_active=True` AND `is_validated=True`.
- **`api/`** — FastAPI: app factory, presence-WebSocket lifecycle, native file
  dialogs, and one router per UI area under `api/routers/`. Deliberately thin —
  an endpoint wraps existing functions and holds no business logic. If a
  computation is needed it belongs in `services/`.
- **`services/`** — UI-agnostic view logic; imports neither FastAPI nor any UI
  framework, so it stays runnable from a plain script. Holds the chart /
  fundamentals / scores / indices series builders, the filter engine + registry,
  run and selection persistence, the settings schema, calibration, the fetch
  watcher and the email bodies.
- **`frontend/`** — the Vite + React + TypeScript app. Pages and chart libraries
  are lazily chunked, so a tab downloads only what it opens.
- **`core/`** — `database.py` (the SQLite wrapper), `logging_config.py`,
  `backup.py`. Used by every layer.
- **`config/`** — `settings.py` (all non-sensitive, UI-editable settings),
  `type_map.py` (security-type normalization), `param_hints.py` (UI hover hints).

### Conventions that cut across the codebase

- **Separate SQLite DBs per data type** (`symbols`, `quotes`, `ohlcv`,
  `financials`, `estimates`, `analysis`, `macro`, `indices` — paths in `config/settings.py`). There are
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
- **Scoring rules** (per-parameter strong/weak model, `analysis_layer/scoring_rules.py`)
  follow the SAME defaults-in-code + machine-local-override pattern: committed
  `DEFAULT_RULES` in code, the Scoring Rules UI saves only deviations to a gitignored
  `scoring_rules.json` (delete to reset). A rule = shape (`higher_better`/`lower_better`/
  `sweet_spot`) + anchor (`peer`/`universe`/`absolute`) + sparse per-`screen_type` overrides;
  `goodness()` turns a column into 0-100. **Both the Scoring Rules page and the rule-colored
  heatmap consume `scoring_rules.goodness()` — never reimplement strong/weak coloring
  elsewhere.** Category `*_score`s are **results, not rules** (excluded from the rules page;
  `scoring.py` DERIVES them from rule goodness). `scoring.py` also **stores a
  `<metric>_goodness` (0-100) column per scorable metric** in `analysis.db` (computed
  ONCE via `metric_goodness`, then reused as the input to the category scores —
  parameter goodness → category → overall). These power the Filter **"Score" variant**
  (alongside Value / vs Sector / vs Industry; `resolve_column`→`_goodness`,
  `filter_registry.score_column`) and are viewable/sortable in Output (label "· Score").
  Editing a rule + Save calls `scoring.refresh_scores()` — a fast (~4s) recompute of
  the goodness + category/overall columns on the stored `analysis.db` (no fetch /
  per-symbol pass), so the stored/filterable scores match the heatmap immediately.
- **Filterable/sortable derived values are computed in the analysis layer and
  stored** as `analysis.db` columns (peer `_vs_*`, category `*_score`, per-metric
  `*_goodness`) — never computed live in the UI at filter time, so
  Filter/Output/heatmap all read one consistent number. When a config/rule changes
  them, provide a fast refresh that recomputes just those columns on the stored
  `analysis.db` (e.g. `scoring.refresh_scores()`) instead of forcing a full
  re-analysis. (Reserve live UI compute for purely presentational things — chart
  overlays — that aren't filtered or sorted.)
- **TLS / certificates**: this machine sits behind TLS interception (a proxy/AV
  re-signs HTTPS with a private CA in the Windows trust store). `certifi` alone
  fails with CERTIFICATE_VERIFY_FAILED, so `core/net.configure_tls()` MUST run
  before any network call — it patches stdlib/`requests` via `truststore` and
  points `CURL_CA_BUNDLE` (for yfinance's `curl_cffi`) at a merged
  certifi+OS-store bundle. `api/main.py` and `symbols.run_discovery()` already
  call it; any new entry point (scripts, fetch orchestrator) must too.
- **Canonical symbol key**: the normalized form (`symbol`, yfinance/E*Trade
  convention) is the join key across every database; the raw Polygon ticker is
  kept in `polygon_symbol`. `symbols.normalize_symbol()` does the conversion.
- **Fetchers** receive the *full* symbol list and filter internally to the
  security types they handle. Each owns a paired sanitize function; flow is
  `fetch raw → sanitize → conditional enrichment → sanitize → write`. Rate
  limiting via `ratelimit`, retries via `tenacity`, both keyed off
  `settings.RATE_LIMITS` / `RETRY_*`. **For yfinance fetchers**, group the Ticker
  properties you read by their shared `quoteSummary` request (one cached request =
  one real Yahoo hit; `@limits` counts `fetch_one` not requests, so a multi-request
  fetcher needs a lower rate) — the full property→request map is in
  `dev_docs/yfinance_request_groups.md`.
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
  parameter exposed for filtering (every `services/filter_registry.py` base metric) MUST have a
  `param_hints.py` entry** — add the hint in the same change that adds the metric. The UI
  reads hints ONLY from here (Filter picker ▸ info, Output column headers, the radar's
  category info row, etc.) — never hardcode a description in a page. Category scores
  (`*_score`) are registry keys too (`category: "Score"`). To verify nothing slipped:
  cross-check `R.BASE_BY_KEY` against `PARAM_HINTS` (currently 98/98, 0 missing).
- **No explainer text in the UI** — don't add caption/blurb text describing what a
  control does or how to use it (it clutters the UI, 2026-06-21). The sanctioned
  hint mechanism is `config/param_hints.py` hint boxes plus `title=` tooltips.
  Keep ONLY status/error/empty messages and captions that show **data** (counts,
  names, plotted-data subtitles, sort state). The Parameters page
  (`frontend/src/pages/ParametersPage.tsx`) is intentional documentation — exempt.
- **Charts** use TWO libraries by shape, and a tab downloads only the one it
  opens: **Lightweight Charts** for dense time series (price, sector indices,
  calibration) and **ECharts** for bar / radar / heat map, which Lightweight
  Charts cannot draw. Wrappers: `frontend/src/components/PriceChart.tsx` and
  `EChart.tsx`; shared palette and layout in `frontend/src/components/chartTheme.ts`.
  **The chart is created ONCE and mutated after** — `setData`, `setMarkers`,
  add/remove series. Never recreate it on re-render, and call `fitContent()`
  only when the SUBJECT changes (symbol set, window, mode), tracked with a key.
  Acceptance test for any chart change: zoom into a region, then toggle a series
  or drag a slider — the zoom must not reset. Tree-shaken ECharts needs every
  chart type and component registered via `echarts.use([...])` or `setOption`
  fails at runtime, silently for some components; it also doesn't watch its
  container, so resize via `ResizeObserver` and `dispose()` on unmount.
- **Colour is never the only cue.** No red/green anywhere: blue is up, amber is
  down, and errors/warnings use amber. The series palette is Okabe-Ito
  (`SERIES_COLORS`), reordered for red-weak vision. Pair colour with a second
  cue chosen to fit the chart's DENSITY — on dense series that means a direct
  label at the line's right edge plus a named readout strip, NOT a dash pattern
  (at ~750 points a dash reads as noise, or as gaps that aren't there);
  `SERIES_DASH` exists but is documented sparse-charts-only. Heat-map cells
  print their value as well as colouring it. A dashed line is still fine for a
  REFERENCE level (the flat 100 baseline) — that marks "not data", a different
  job from identity.
- **A chart host cannot be allowed to collapse**: give it a `relative` parent
  with a `min-h-*` fallback and mount the chart in an `absolute inset-0` child.
  Sized only by `flex-1`/`h-full` inside an `overflow-auto` ancestor, it
  collapses to a sliver.
- **Long jobs stream over SSE**, never a polling loop: the server holds the
  state (on disk, for fetches), sends a full snapshot on connect, then pushes
  only on change. `EventSource` reconnects for free, which a WebSocket would
  need hand-written retry for. Whatever the UI shows must be part of the
  stream's change key — see `services/fetch_watch.version()`, which watches the
  stop flag as well as the run state, because requesting a stop changes neither
  the state file nor the log.
- **The virtualised table owns its own scroll element** (`frontend/src/components/DataTable.tsx`):
  never wrap it in another `overflow-auto` container or scrolling stops working.
  Its row component is memoised — without that, every scroll tick re-renders
  every visible row's cells. `tableLayout: fixed` with explicit widths, or the
  browser re-flows columns as you scroll.
- **Search params are validated in `frontend/src/lib/search.ts`**, a separate
  module on purpose: `validateSearch` runs at router setup and is statically
  imported, so putting it in a page file drags that page into the main chunk and
  kills its lazy import.
- **Backups**: `core/backup.py` keeps the newest 5 dated copies of every `.db`
  (`{stem}_{YYYY-MM-DD_HH-MM-SS}{suffix}`), taken before each fetch run; one run stamps
  all DBs with a single timestamp, so each stamp is one consistent snapshot.
  Count-capped, not day-based. Same scheme backs up the run log and `config/settings.py`.
  `core/restore.py` reverts the live DBs to a chosen snapshot (Fetch Control danger
  zone), copying the current DBs to `backups/pre_restore/` first as a one-level undo.
- **File Open/Save dialogs go through `api/dialogs.py`** — the app's ONE file
  chooser. It pops a **native OS dialog** (tkinter) in a **child process**, and
  the endpoint awaits it with `asyncio.create_subprocess_exec` so the event loop
  keeps serving other requests (SSE, polling) while the dialog sits open;
  blocking `subprocess.run` would freeze every endpoint. Concurrent dialogs are
  serialised with a lock (409 if one is already open). Chosen deliberately over
  the browser's file picker because the app is local and single-user, so a
  native dialog can start in / save straight to a folder (e.g. `FILTERS_DIR`)
  and offers plain Save/Load buttons — a browser chooser can do neither. **This
  ties file dialogs to running on the user's own desktop**; a cloud move would
  have to switch to the browser chooser. The utility only picks the path — each
  file type owns its (de)serialisation (e.g. `filter_engine.save_filterset_to`).
  A `fake_path` field skips tkinter for headless checks.
- **Selections (chosen items + per-item info) persist via `services/selection_io.py`** — the
  ONE place for "save/load a SET of items". Two kinds, one JSON shape (a dict keyed by
  item; insertion order = saved order), both in the single `settings.SELECTIONS_DIR`
  folder, suffix telling them apart: **`.syms`** symbol sets (per-symbol info =
  Company/Sector/Industry from analysis.db) and **`.prms`** parameter/column sets
  (per-param info = the param's `param_hints` entry). The module only reads and
  writes — CHOOSING the path is the caller's job (`api/dialogs.py`), which keeps
  it importable from a script with no UI. The typed filename IS the name (no
  pre-naming field). Item KEYS drive behaviour on load; the info is descriptive
  snapshot metadata.
  Wired into Output (Custom Symbols box, results-selection Action menu, parameter-columns
  Swap/Add) and Fetch Control (dev subset). Don't add a parallel selection store — extend
  this (add a kind to its registry).
- **Per-symbol analysis loops index once**: never filter the full OHLCV/financials
  frame inside the per-symbol loop — a boolean mask per symbol re-scans millions of
  rows. Pre-group by symbol before the loop (`groupby` → dict of pre-sorted slices)
  so each lookup is O(1). See `analysis_layer/pipeline.run_analysis()`.
- **Analysis loads a bounded OHLCV window**: the full ~70M-row table balloons far
  past physical RAM in pandas (measured 57 GB commit on 32 GB → heavy paging), so
  `run_analysis()` reads only the trailing `settings.ANALYSIS_OHLCV_LOOKBACK_DAYS`
  (~2 years; indicators need ≤253 trading days) and only the bar columns it uses.
  Dividends and splits — the sole deep-history consumers (div_cagr_1y/3y/5y/streaks,
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
