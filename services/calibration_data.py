"""
Peak-detection calibration: sample stocks and the swing highs/lows a given
prominence/distance pair produces.

Detection goes through `analysis_layer.technical.trend_signals` — the SAME
function the analysis pipeline calls — so what the tuner previews is exactly
what a real run would find.
"""

from __future__ import annotations

from typing import Any, cast

import numpy as np
import pandas as pd

from analysis_layer.technical import _TREND_WINDOW, trend_signals
from config import settings
from core.database import Database

POOL_LIQUID = 300  # most-liquid equities to consider (avoids gappy penny names)
POOL_FIT = 120  # of those, how many get a price-fit R² computed
SAMPLE_DAYS = 400  # calendar days per candidate (> ~252 sessions)
EQUITY_TYPES = ("stock", "adr")  # real price action; mutual funds are flat NAV
ATR_MAX = 25.0  # %; above this is a split/illiquidity artifact, not volatility

# The five verdicts trend_signals can return. Direction is spelled out in the
# text and carried by an arrow — never by colour alone.
TREND_LABELS = {
    "strong_uptrend": "▲▲ Strong uptrend — higher highs AND higher lows",
    "weak_uptrend": "▲ Weak uptrend — one of: higher high / higher low",
    "sideways": "— Sideways — no confirmed swing pattern",
    "weak_downtrend": "▼ Weak downtrend — one of: lower low / lower high",
    "strong_downtrend": "▼▼ Strong downtrend — lower lows AND lower highs",
}


def load_window(symbols: list[str]) -> pd.DataFrame:
    """Recent adj_close history for `symbols` -> (symbol, date, adj_close)."""
    if not symbols or not settings.OHLCV_DB.exists():
        return pd.DataFrame()
    cutoff = (pd.Timestamp.today() - pd.Timedelta(days=SAMPLE_DAYS)).strftime("%Y-%m-%d")
    with Database(settings.OHLCV_DB) as db:
        if not db.table_exists("ohlcv"):
            return pd.DataFrame()
        placeholders = ",".join("?" * len(symbols))
        frame = db.read(
            "ohlcv",
            where=f"symbol IN ({placeholders}) AND date >= ?",
            params=[*symbols, cutoff],
        )
    if frame.empty:
        return frame

    frame = frame[["symbol", "date", "adj_close"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["adj_close"] = pd.to_numeric(frame["adj_close"], errors="coerce")
    frame = frame[frame["date"].notna() & frame["adj_close"].notna()]
    return frame.sort_values(["symbol", "date"], kind="stable")


def _fit_r2(close: pd.Series) -> float:
    """R² of a straight-line fit over the trend window — how cleanly price trends."""
    values = close.tail(_TREND_WINDOW).to_numpy(dtype=float)
    if len(values) < 2:
        return float("nan")
    x = np.arange(len(values), dtype=float)
    fit = np.polyfit(x, values, 1)
    residual = values - np.polyval(fit, x)
    total = float(np.sum((values - values.mean()) ** 2))
    return 1.0 - float(np.sum(residual**2)) / total if total > 0 else float("nan")


def samples() -> list[dict[str, Any]]:
    """A spread of liquid equities chosen by price BEHAVIOUR, not by name.

    Picks the clearest trends, the choppiest, the most volatile and the calmest,
    so the two knobs get tuned against the range of shapes they must handle.
    """
    if not settings.ANALYSIS_DB.exists() or not settings.OHLCV_DB.exists():
        return []
    with Database(settings.ANALYSIS_DB) as db:
        if not db.table_exists("analysis"):
            return []
        analysis = db.read("analysis")

    keep = [
        c
        for c in ("symbol", "name", "security_type", "atr_pct", "vol_20d_avg")
        if c in analysis.columns
    ]
    analysis = analysis[keep].copy()
    analysis = analysis[analysis["security_type"].isin(EQUITY_TYPES)]
    for column in ("atr_pct", "vol_20d_avg"):
        analysis[column] = pd.to_numeric(analysis[column], errors="coerce")
    analysis = analysis[analysis["atr_pct"].notna() & analysis["vol_20d_avg"].notna()]
    analysis = analysis[
        (analysis["vol_20d_avg"] > 0) & (analysis["atr_pct"].between(0, ATR_MAX))
    ]
    if analysis.empty:
        return []

    # Liquidity gate first, then bound how many linear fits we pay for.
    pool = analysis.nlargest(POOL_LIQUID, "vol_20d_avg")
    if len(pool) > POOL_FIT:  # span the volatility range within the liquid pool
        picks = np.linspace(0, len(pool) - 1, POOL_FIT).round().astype(int)
        pool = pool.sort_values("atr_pct").iloc[np.unique(picks)]

    prices = load_window(pool["symbol"].tolist())
    if prices.empty:
        return []
    fits = {
        str(symbol): _fit_r2(group["adj_close"])
        for symbol, group in prices.groupby("symbol", sort=False)
    }
    pool = pool.assign(r2=pool["symbol"].map(fits))
    pool = pool[pool["r2"].notna()]
    if pool.empty:
        return []

    chosen: dict[str, str] = {}  # symbol -> tag; the first tag wins on overlap

    def take(frame: pd.DataFrame, tag: str, count: int = 2) -> None:
        for symbol in frame["symbol"].head(count):
            chosen.setdefault(str(symbol), tag)

    take(pool.sort_values("r2", ascending=False), "clear trend")
    take(pool.sort_values("r2", ascending=True), "choppy")
    take(pool.sort_values("atr_pct", ascending=False), "volatile")
    take(pool.sort_values("atr_pct", ascending=True), "calm")

    names = pool.set_index("symbol")["name"].to_dict()
    return [
        {"symbol": symbol, "name": names.get(symbol) or "", "tag": tag}
        for symbol, tag in chosen.items()
    ]


def signals(symbol: str, prominence: float, distance: int) -> dict[str, Any]:
    """One symbol's price window with the swing highs/lows those knobs detect."""
    frame = load_window([symbol.upper()])
    if frame.empty:
        return {"points": [], "highs": [], "lows": [], "message": f"No price history for {symbol}."}

    series = pd.Series(
        frame["adj_close"].to_numpy(), index=pd.DatetimeIndex(pd.to_datetime(frame["date"]))
    )
    high_idx, low_idx, label = trend_signals(series, prominence, distance)

    dates = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in series.index]
    values = [float(v) for v in series.to_numpy()]
    points = [{"time": d, "value": v} for d, v in zip(dates, values)]

    def marks(indices) -> list[dict[str, Any]]:
        return [
            {"time": dates[int(i)], "value": values[int(i)]}
            for i in cast(Any, indices)
            if 0 <= int(i) < len(dates)
        ]

    return {
        "symbol": symbol.upper(),
        "points": points,
        "highs": marks(high_idx),
        "lows": marks(low_idx),
        "trend": label,
        "trend_label": TREND_LABELS.get(label, label),
        "window": _TREND_WINDOW,
        "message": None,
    }
