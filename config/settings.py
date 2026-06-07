"""
Central configuration for all NON-SENSITIVE settings.

API keys never live here — they live in `.env` (see `.env.template`).
This file holds everything the Settings page in the Streamlit UI is allowed to
edit: file paths, batch sizes, rate limits, scoring weights, indicator
parameters, etc. Both the UI and hand-edits write to this same file, so keep it
a plain module of module-level constants (no logic that's hard to round-trip).

Roadmap references: Topic 3.4 (config split), Topic 4.4 (scoring weights),
Topic 4.2 (indicator params), Topic 9 (logging / fetch locks).
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

# Rotating backups of every .db file, taken before each fetch run.
BACKUP_DIR: Path = BASE_DIR / "backups"
BACKUP_VERSIONS: int = 5  # keep _1 (newest) .. _5 (oldest); _5 is dropped each run

# Logs (terminal + file). The log is rolled once per fetch run: the prior run is
# kept as famarket.prev.log (one copy, overwritten each run) — see roll_log().
LOG_DIR: Path = BASE_DIR / "logs"
LOG_FILE: Path = LOG_DIR / "famarket.log"
LOG_LEVEL: str = "INFO"
# Summary-level format; timestamp prefix on every entry (Topic 9.3).
LOG_FORMAT: str = "%(asctime)s [%(name)s] %(levelname)s %(message)s"
LOG_DATEFMT: str = "%Y-%m-%d %H:%M:%S"

# User-managed file collections (paths configurable from the Settings page).
FILTERS_DIR: Path = BASE_DIR / "filters"        # .filt files (saved filter sets)
COLUMN_SETS_DIR: Path = BASE_DIR / "column_sets"  # .prms files (output column sets)

# --------------------------------------------------------------------------- #
# Fetch behaviour
# --------------------------------------------------------------------------- #
# A successful (symbol, fetcher) fetch is locked for this many days; the weekly
# Friday cadence naturally falls outside the window so normal runs refetch.
FETCH_LOCK_DAYS: int = 5

# Minimum history pulled on an initial OHLCV load.
OHLCV_INITIAL_YEARS: int = 10

# Symbols with no new OHLCV for this many consecutive weeks are flagged inactive.
INACTIVE_AFTER_WEEKS: int = 8

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

# Peak detection (scipy.signal.find_peaks) — calibrated via the in-UI tool.
PEAK_PROMINENCE: float = 0.05  # placeholder; tune in calibration tool
PEAK_DISTANCE: int = 20        # min trading days between detected peaks

# Growth metric windows (years); gated until enough history exists.
GROWTH_WINDOWS_YEARS: tuple[int, ...] = (1, 3, 5)

# --------------------------------------------------------------------------- #
# Scoring & ranking (Topic 4.4) — all adjustable from the Settings page
# --------------------------------------------------------------------------- #
# Weights of each category score within the Overall Score (must sum to 1.0).
OVERALL_SCORE_WEIGHTS: dict[str, float] = {
    "quality": 0.25,
    "growth": 0.25,
    "momentum": 0.20,
    "value": 0.20,
    "income": 0.10,
}

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
    for path in (DB_DIR, BACKUP_DIR, LOG_DIR, FILTERS_DIR, COLUMN_SETS_DIR):
        path.mkdir(parents=True, exist_ok=True)
