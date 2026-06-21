"""
Central configuration for all NON-SENSITIVE settings.

API keys never live here — they live in `.env` (see `.env.template`).
This file holds the committed DEFAULTS for everything the Settings page in the
Streamlit UI is allowed to edit: file paths, batch sizes, rate limits, scoring
weights, indicator parameters, etc. Keep it a plain module of module-level
constants.

The UI no longer rewrites this file. It saves only the CHANGED keys to a
gitignored, machine-local override (`settings.local.json`, see
`config/settings_overrides.py`); the bottom of this module lays those overrides
on top of the defaults at import time. Hand-edit a default here (committed) or the
override file (local-only). Updated 2026-06-12 — replaced the previous in-place
AST rewrite of this file (config/settings_io.py).

Roadmap references: Topic 3.4 (config split + override model), Topic 4.4 (scoring
weights), Topic 4.2 (indicator params), Topic 9 (logging / fetch locks).
"""

from __future__ import annotations

from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
# Project root = parent of the config/ package.
BASE_DIR: Path = Path(__file__).resolve().parent.parent

# Single knob for where all SQLite databases live, plus one path per database.
DB_DIR: Path = BASE_DIR / "databases"
SYMBOLS_DB: Path = DB_DIR / "symbols.db"
QUOTES_DB: Path = DB_DIR / "quotes.db"
OHLCV_DB: Path = DB_DIR / "ohlcv.db"
FINANCIALS_DB: Path = DB_DIR / "financials.db"
ANALYSIS_DB: Path = DB_DIR / "analysis.db"
MACRO_DB: Path = DB_DIR / "macro.db"
INDICES_DB: Path = DB_DIR / "indices.db"  # sector / sub-industry index level series

# Rotating backups of every .db file, taken before each fetch run.
BACKUP_DIR: Path = BASE_DIR / "backups"
BACKUP_VERSIONS: int = 5  # keep _1 (newest) .. _5 (oldest); _5 is dropped each run

# Logs (terminal + file). The log is rolled once per fetch run: the prior run is
# archived into BACKUP_DIR as a rotating versioned backup (famarket_1.log ..
# famarket_5.log, same scheme as the databases) — see roll_log(). Empty logs aren't
# backed up.
LOG_DIR: Path = BASE_DIR / "logs"
LOG_FILE: Path = LOG_DIR / "famarket.log"   # the run log (fetch/analysis) — rolled per run
# The Streamlit app logs HERE, never to the run log. The fetch runs as its own
# detached process and owns famarket.log (it rolls it at the start of each run);
# if the app also held famarket.log open, that roll's unlink() would fail on Windows
# (WinError 32 — file in use). Keeping the app on its own file removes that conflict.
APP_LOG_FILE: Path = LOG_DIR / "app.log"
LOG_LEVEL: str = "INFO"

# Detached-fetch lifecycle state (the fetch runs as its own OS process so it
# survives closing the app — see data_layer/run_state.py + data_layer/launcher.py).
# STATE_DIR holds tiny machine-local control files (gitignored):
#   - fetch_run.json : run lifecycle + summary, the single source of truth for
#     "is a fetch running" across processes/app restarts.
#   - fetch_stop.flag: a cross-process Stop request (cancel.py writes/reads it).
# FETCH_CONSOLE_LOG captures the detached process's stdout/stderr for the rare
# crash that happens before logging is set up (the run itself logs to LOG_FILE).
STATE_DIR: Path = BASE_DIR / "state"
FETCH_RUN_STATE_FILE: Path = STATE_DIR / "fetch_run.json"
FETCH_STOP_FILE: Path = STATE_DIR / "fetch_stop.flag"
FETCH_CONSOLE_LOG: Path = LOG_DIR / "fetch_console.log"
# Summary-level format; timestamp prefix on every entry (Topic 9.3).
LOG_FORMAT: str = "%(asctime)s [%(name)s] %(levelname)s %(message)s"
LOG_DATEFMT: str = "%Y-%m-%d %H:%M:%S"

# User-managed file collections (paths configurable from the Settings page).
FILTERS_DIR: Path = BASE_DIR / "filters"        # .filt files (saved filter sets)
COLUMN_SETS_DIR: Path = BASE_DIR / "column_sets"  # .prms files (output column sets)

# Per-parameter scoring RULES (heatmap coloring / future scoring). A dedicated JSON
# file (like the .filt filter sets) holding the user's edits on top of the committed
# defaults in analysis_layer/scoring_rules.py. Delete it to reset rules to defaults.
SCORING_RULES_PATH: Path = BASE_DIR / "scoring_rules.json"

# Saved filter-run results (Topic 6): each Run Filter writes one parquet (rows) +
# json (metadata) pair here, so every run opens in its own browser tab at
# /output?run=<id> and survives app restarts. Newest N kept, pruned on each save.
OUTPUT_RUNS_DIR: Path = BASE_DIR / "results"
OUTPUT_RUNS_KEEP: int = 20

# Filter page — categorical multi-pick. A filter column with few distinct values is
# offered as a searchable multi-pick list ("is any of" / "is none of") instead of a
# free-text/number box. Two caps so continuous numerics stay range filters:
#   - text/classification columns (sector, industry, fund_family, trend, …)
#   - numeric columns — small, so 0-100 scores / 1-99 ranks keep >, <, between and
#     only tiny numeric enums (e.g. bb_squeeze 0/1, 1-10 risk scores) become lists.
FILTER_CATEGORICAL_MAX_UNIQUE: int = 100
FILTER_CATEGORICAL_MAX_UNIQUE_NUMERIC: int = 12

# UI-saved setting overrides (gitignored, machine-local). Holds ONLY the keys the
# Settings page changed from the defaults above; applied on top at the bottom of
# this module. Lives at the project root for now — the "Standalone executable"
# Future Idea (ROADMAP) will relocate writable paths like this to a user-data dir.
SETTINGS_OVERRIDES_PATH: Path = BASE_DIR / "settings.local.json"

# --------------------------------------------------------------------------- #
# Fetch behaviour
# --------------------------------------------------------------------------- #
# A successful (symbol, fetcher) fetch is locked for this many days; the weekly
# Friday cadence naturally falls outside the window so normal runs refetch.
FETCH_LOCK_DAYS: int = 4

# --------------------------------------------------------------------------- #
# Fetch viability / abandonment (data_layer/fetch_status.py + staleness.py)
# --------------------------------------------------------------------------- #
# Policy: a (symbol, fetcher) pair is ABANDONED — skipped on normal runs — when it
# stops producing fresh data. Two probes implement that one idea:
#   * strike probe    — for symbols that return NOTHING (no date to age).
#   * staleness probe — for symbols that return only OLD data (a frontier to age).
# This switch (NOT the 5-day lock) governs the whole viability policy — abandonment,
# staleness, and the financials due-date gate. Turn it off to make one run touch
# every symbol regardless of viability; data returning resets the probes. The 5-day
# lock is separate (respect_lock) and gates only the fetch cadence.
FETCH_ABANDONMENT_ENABLED: bool = True

# Strike probe: after this many actual fetches that return no data, the pair is
# abandoned. The counter resets the moment data is returned (a relisted symbol
# recovers).
MAX_NO_DATA_FETCHES: int = 4

# Staleness probe: skip a symbol whose newest STORED value is older than the
# window. Self-perpetuating — the stored date can't advance while skipped.
OHLCV_STALE_WEEKS: int = 4                     # newest OHLCV date older than this
FINANCIALS_QUARTERLY_STALE_QUARTERS: int = 2   # newest quarterly period_end older than this
FINANCIALS_YEARLY_STALE_QUARTERS: int = 6      # newest annual period_end older than this

# Financials due-date gate: defer a symbol while its NEXT statement cannot exist
# yet — newest quarterly period_end + ~91 days (annual-only: + 365 days) + this
# lag. The lag covers the SEC filing window (a 10-Q is due 40-45 days after the
# quarter ends). Deferral, not abandonment: the symbol comes due again on its
# own. A viability gate — governed by FETCH_ABANDONMENT_ENABLED, not the 5-day lock.
FINANCIALS_REPORT_LAG_DAYS: int = 45

# Minimum history pulled on an initial OHLCV load.
OHLCV_INITIAL_YEARS: int = 10

# OHLCV recency window for validation: a symbol whose newest OHLCV bar is older
# than this fails the "recent data" check in reassess_state (is_validated=False)
# and so drops out of the analysis universe. (Distinct from the staleness probe
# above, which stops *fetching* a stale symbol.)
OHLCV_INACTIVE_AFTER_WEEKS: int = 8

# Default batch size for batched API fetches (per-API overrides below).
DEFAULT_BATCH_SIZE: int = 100

# Per-API rate limits: (max_calls, period_seconds). Tune with the rate-limit
# testing utility (Topic 2.3) and store the safe ceiling here.
RATE_LIMITS: dict[str, tuple[int, int]] = {
    "yfinance": (100, 60),  # tested safe by Frank
    "polygon": (5, 60),    # free tier: 5 req/min
    "fmp": (250, 86400),   # free tier: ~250 req/day
    "fred": (120, 60),
    "etrade": (10, 1),
    "edgar": (9, 1),       # SEC fair-access policy: max ~10 req/sec
}

# Retry policy for tenacity (auto-retry on transient API failure).
RETRY_MAX_ATTEMPTS: int = 3
RETRY_WAIT_SECONDS: float = 2.0

# --------------------------------------------------------------------------- #
# Analysis — indicator parameters (Topic 4.2)
# --------------------------------------------------------------------------- #
MOVING_AVERAGES: tuple[int, ...] = (50, 150, 200)
RSI_PERIOD: int = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
MACD_CROSSOVER_PERSIST_DAYS: int = 5
BOLLINGER_PERIOD: int = 20
BOLLINGER_STD: float = 2.0
ATR_PERIOD: int = 14
VOLUME_AVG_PERIOD: int = 20
RS_RANK_MIN_HISTORY_DAYS: int = 252  # NULL rs_rank below this

# Daily price history loaded by run_analysis(), in CALENDAR days back from the
# newest stored bar. Indicators need at most ~253 trading days (rs_rank is the
# deepest), so ~2 years is generous. Dividends and splits are side-read in full
# regardless, so deep-history metrics (div_growth_5y, EPS split-adjust) are
# unaffected. The pipeline clamps low values so rs_rank never loses its window.
# RAISE THIS if a metric is ever added that needs deeper *price* history (e.g.
# 5y price CAGR, historical P/E bands). RAM scales ~linearly with it: +365 days
# ≈ +8.5M loaded rows at a 50k-symbol universe.
ANALYSIS_OHLCV_LOOKBACK_DAYS: int = 730

# Peak detection (scipy.signal.find_peaks) — calibrated via the in-UI tool.
PEAK_PROMINENCE: float = 0.05  # placeholder; tune in calibration tool
PEAK_DISTANCE: int = 20        # min trading days between detected peaks

# Growth metric windows (years); gated until enough history exists.
GROWTH_WINDOWS_YEARS: tuple[int, ...] = (1, 3, 5)
# Annual points used for the polyfit trend stats (residual vol %, R², CV); the
# series is truncated to its last N years so the consistency measure reflects
# recent behaviour rather than deep EDGAR history.
GROWTH_TREND_YEARS: int = 5

# Sector / sub-industry index (analysis_layer/sector_index.py) — daily base-100
# level series per Yahoo sector and 'sector | industry', SPDR Select Sector formula.
# Built on full analysis runs from the panels already loaded; written to INDICES_DB.
INDEX_FIELD: str = "adj_close"            # 'adj_close' total return | 'close' price return
# Liquidity floor: drop names whose average daily dollar volume (adj_close*volume)
# over the trailing window is below this, so illiquid/penny names can't distort a
# group's index (mutual-fund flat-NAV series are removed for free). Set 0 to disable.
INDEX_MIN_AVG_DOLLAR_VOLUME: float = 1_000_000.0
INDEX_LIQUIDITY_WINDOW_DAYS: int = 63     # trailing bars for the dollar-volume average
INDEX_MIN_INDUSTRY_MEMBERS: int = 3       # skip 'sector | industry' groups smaller than this
# Index history is read back to the earliest date by which this many constituents
# have a share report in financials.db (data-driven start; a breadth guard so one
# deep-history outlier can't drag the price read back decades). Prices for the index
# come from a dedicated narrow deep read, NOT the bounded ANALYSIS_OHLCV_LOOKBACK_DAYS.
INDEX_START_MIN_REPORTERS: int = 25

# Compute-and-reconcile (Analysis): every fundamental ratio is computed from
# financials.db; where yfinance (quotes.db) has the same ratio we cross-check and
# log a summary WARNING when they diverge by more than this fraction.
RECONCILE_TOLERANCE_PCT: float = 0.10

# --------------------------------------------------------------------------- #
# Intrinsic value (Topic 4.1)
# --------------------------------------------------------------------------- #
GRAHAM_MULTIPLIER: float = 22.5        # Graham's 15 (P/E) × 1.5 (P/B)
LYNCH_GROWTH_CAP: float = 25.0         # cap on growth% used as Lynch fair P/E
DCF_PROJECTION_YEARS: int = 10         # explicit FCF projection horizon
DCF_TERMINAL_GROWTH: float = 0.025     # perpetual growth after the horizon
DCF_EQUITY_RISK_PREMIUM: float = 0.05  # added to risk-free via beta (CAPM)
DCF_DEFAULT_BETA: float = 1.0          # used when a symbol has no beta
DCF_GROWTH_CAP: float = 0.15           # cap on historical FCF growth in projection
DCF_MIN_DISCOUNT_SPREAD: float = 0.02  # floor on (discount − terminal growth)

# --------------------------------------------------------------------------- #
# Peer comparison (Topic 4.3)
# --------------------------------------------------------------------------- #
# Metrics that get _vs_sector / _vs_industry columns (% above/below peer median).
PEER_COMPARABLE_METRICS: tuple[str, ...] = (
    "pe", "forward_pe", "peg", "gross_margin", "operating_margin", "net_margin",
    "roe", "roa", "ev_ebitda", "revenue_cagr_3y", "debt_to_equity",
)
MIN_PEERS_FOR_MEDIAN: int = 3       # below this a sector/industry median is too noisy
MIN_PEERS_FOR_PERCENTILE: int = 5   # below this fall back to the universe for scoring
RS_RANK_MIN_PER_TYPE: int = 30      # min members for a security_type to rank RS within itself
                                    # (else fall back to universe — funds vs stocks don't distort)

# --------------------------------------------------------------------------- #
# Scoring & ranking (Topic 4.4) — all adjustable from the Settings page
# --------------------------------------------------------------------------- #
# Weights of each category score within the Overall Score (must sum to 1.0).
# Mix re-derived 2026-06-20 (was Q25/G25/M20/V20/I10): the five category scores are
# near-uncorrelated on the live universe (all |r|≈0, value↔momentum −0.23), so this is
# a pure emphasis call. Growth down (weak standalone factor, overlaps quality); value &
# income up (robust / independent + one of only two categories funds even have).
OVERALL_SCORE_WEIGHTS: dict[str, float] = {
    "quality": 0.25,
    "value": 0.22,
    "momentum": 0.20,
    "growth": 0.18,
    "income": 0.15,
}

# Metric weights WITHIN each category score (Topic 4.4) — all adjustable from the
# Settings page. Each category's score is the weight-averaged 0-100 rule GOODNESS
# of whichever of these metrics the symbol has (NaN metrics drop out, so funds/ETFs
# gate themselves naturally). Weights are positive magnitudes; metric DIRECTION and
# the peer/universe/absolute anchor live per-metric in analysis_layer/scoring_rules.py.
#
# Re-derived 2026-06-20 from the live 37,753-row analysis.db (intra-category goodness
# correlation + coverage + discriminating spread) plus factor-investing robustness.
# PRINCIPLE: weight by UNIQUE INFORMATION + robustness, not by count — a correlated
# cluster shares one "slot"; independent / sustainability metrics keep weight even when
# sparse (gating handles absence). Notable collapses: momentum's MA trio (corr up to
# 0.96), value's ps↔ev_revenue (0.82), quality's roa/operating/net margin blob (0.82-0.84).
CATEGORY_METRIC_WEIGHTS: dict[str, dict[str, float]] = {
    "value": {  # favor EV/EBITDA, FCF, DCF gap; down-weight redundant multiples
        "ev_ebitda": 1.0, "p_fcf": 1.0, "margin_of_safety": 1.0, "pe": 0.75,
        "peg": 0.5, "pb": 0.5, "ps": 0.5, "ev_revenue": 0.25, "forward_pe": 0.25,
    },
    "quality": {  # gross_margin + roic + safety carry; margin blob shares one slot
        "roic": 1.0, "gross_margin": 1.0, "roe": 0.75, "altman_z": 0.75,
        "fcf_margin": 0.75, "net_margin": 0.5, "debt_to_equity": 0.5,
        "current_ratio": 0.5, "interest_coverage": 0.5, "operating_margin": 0.25,
        "roa": 0.25, "debt_to_ebitda": 0.25,
    },
    "growth": {  # already near-independent; trim 3y/5y window overlap (~0.55)
        "eps_cagr_3y": 1.0, "revenue_cagr_3y": 1.0, "eps_yoy_q": 0.75,
        "revenue_yoy_q": 0.75, "fcf_cagr_3y": 0.5, "eps_growth_r2": 0.5,
        "eps_cagr_5y": 0.5, "revenue_cagr_5y": 0.5,
    },
    "momentum": {  # rs_rank is the canonical factor; collapse the MA trio
        "rs_rank": 1.0, "pct_from_52w_high": 0.75, "price_vs_ma_50": 0.5,
        "price_vs_ma_200": 0.5, "price_vs_ma_150": 0.25,
    },
    "income": {  # near-independent already; keep sustainability weighted
        "div_yield_ttm": 1.0, "div_growth_5y": 0.75, "div_coverage": 0.75,
        "div_consistency": 0.75, "div_payout_ratio": 0.5, "div_consecutive_years": 0.5,
    },
}

# rs_rank (Topic 4.2): IBD-style weighted percentile vs the whole universe.
# Weighted return over four ~3-month windows (most recent weighted heaviest);
# NULL below RS_RANK_MIN_HISTORY_DAYS of price history.
RS_RANK_QUARTER_DAYS: int = 63                          # ~3 trading months
RS_RANK_WEIGHTS: tuple[float, ...] = (0.4, 0.2, 0.2, 0.2)  # newest -> oldest quarter

# --------------------------------------------------------------------------- #
# Output / charts
# --------------------------------------------------------------------------- #
# Color-blind safe palette (no red/green); blue-to-orange for heatmaps.
CHART_COLORWAY: tuple[str, ...] = (
    "#0072B2", "#E69F00", "#56B4E9", "#009E73",
    "#F0E442", "#D55E00", "#CC79A7", "#999999",
)
HEATMAP_COLORSCALE: str = "RdBu_r"  # blue-to-orange/red, perceptually safe

# External analysis sites (Topic 6.2). Symbols joined per each site's format.
EXTERNAL_SITES: dict[str, str] = {
    "finviz": "https://finviz.com/screener.ashx?v=111&t={symbols}",   # comma-joined
    "yahoo": "https://finance.yahoo.com/quotes/{symbols}/",            # comma-joined
    "tradingview": "https://www.tradingview.com/symbols/{symbol}/",    # one tab per symbol
    # "koyfin": URL format confirmed during coding phase
}

# FRED macro series pulled into macro.db (Topic 8 — FRED DATA).
FRED_SERIES: dict[str, str] = {
    "treasury_10y": "DGS10",
    "fed_funds_rate": "FEDFUNDS",
    "cpi": "CPIAUCSL",
    "gdp_growth": "A191RL1Q225SBEA",
}


def ensure_runtime_dirs() -> None:
    """Create the directories the app writes to. Safe to call on every startup."""
    for path in (DB_DIR, BACKUP_DIR, LOG_DIR, FILTERS_DIR, COLUMN_SETS_DIR, OUTPUT_RUNS_DIR,
                 STATE_DIR):
        path.mkdir(parents=True, exist_ok=True)


# Lay the gitignored local overrides on top of the defaults above. Done at the very
# bottom so every default is already defined; `apply` reads the path from this
# module's own globals (no re-import mid-load) and quietly ignores a missing or
# malformed file, so defaults always stand on their own.
from config import settings_overrides as _settings_overrides  # noqa: E402

_settings_overrides.apply(globals())
