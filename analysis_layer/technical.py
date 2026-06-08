"""
Technical indicators (Topic 4.2) — per symbol from ohlcv.db -> analysis.db.

Everything is current-state (recalculated each run) and computed on `adj_close`
of the last completed session — the same canonical price metrics.py uses. ATR is
the one indicator that needs intraday range, so high/low are adjusted by the
`adj_close/close` factor before use (keeps a split inside the window honest).

Gating is data-driven rather than keyed off security_type: a flat-NAV mutual fund
has high==low and zero volume, so its true range and volume signals are
meaningless — we detect that from the data and NULL ATR / volume, while MA, RSI,
MACD, Bollinger, 52-week and trend still compute on the NAV series (which does
move day to day). See [[mutual-fund-ohlcv-is-flat-nav]].

`rs_rank` is intentionally NOT here: it ranks each symbol against the whole
universe, so the pipeline computes it after every symbol's metrics exist.

Text/bool outputs (macd_crossover, macd_hist_trend, bb_position, vol_trend,
bb_squeeze, trend) are stored alongside the numerics as raw values for the filter.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.signal import find_peaks

from config import settings

_WEEK52 = 252      # trading days in ~1 year
_TREND_WINDOW = 252  # classify trend over the last year of swings
_TREND_MIN = 60      # too little history to call a trend
_SQUEEZE_LOOKBACK = 126  # ~6 months to judge "unusually narrow" bands


def compute(symbol: str, ohlcv: pd.DataFrame) -> dict:
    """All technical indicators for one symbol. Empty dict if no price history."""
    if ohlcv is None or ohlcv.empty:
        return {}
    df = ohlcv.sort_values("date")
    close = pd.to_numeric(df["adj_close"], errors="coerce").reset_index(drop=True)
    close = close.dropna()
    if len(close) < 2:
        return {}
    price = float(close.iloc[-1])

    out: dict = {}
    out.update(_moving_averages(close, price))
    out["rsi_14"] = _rsi(close, settings.RSI_PERIOD)
    out.update(_macd(close))
    out.update(_bollinger(close, price))
    out.update(_volume(df))
    out["atr_pct"] = _atr_pct(df, price)
    out.update(_week52(close, price))
    out["trend"] = _trend(close)
    return out


# --------------------------------------------------------------------------- #
def _moving_averages(close: pd.Series, price: float) -> dict:
    out: dict = {}
    for p in settings.MOVING_AVERAGES:
        ma = float(close.rolling(p).mean().iloc[-1]) if len(close) >= p else float("nan")
        out[f"ma_{p}"] = ma
        out[f"price_vs_ma_{p}"] = (price / ma - 1) * 100 if ma and ma > 0 else float("nan")
    return out


def _rsi(close: pd.Series, period: int) -> float:
    """Wilder's RSI (EMA smoothing). 100 when there are no losses in the window."""
    if len(close) <= period:
        return float("nan")
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    ag, al = float(avg_gain.iloc[-1]), float(avg_loss.iloc[-1])
    if pd.isna(ag) or pd.isna(al):
        return float("nan")
    if al == 0:
        return 100.0
    rs = ag / al
    return 100 - 100 / (1 + rs)


def _macd(close: pd.Series) -> dict:
    f, s, sig = settings.MACD_FAST, settings.MACD_SLOW, settings.MACD_SIGNAL
    if len(close) < s:
        return {k: float("nan") for k in ("macd_line", "macd_signal", "macd_hist")} | {
            "macd_crossover": None, "macd_hist_trend": None
        }
    line = close.ewm(span=f, adjust=False).mean() - close.ewm(span=s, adjust=False).mean()
    signal = line.ewm(span=sig, adjust=False).mean()
    hist = line - signal
    out = {
        "macd_line": float(line.iloc[-1]),
        "macd_signal": float(signal.iloc[-1]),
        "macd_hist": float(hist.iloc[-1]),
        "macd_crossover": _macd_crossover(hist),
        "macd_hist_trend": _macd_hist_trend(hist),
    }
    return out


def _macd_crossover(hist: pd.Series) -> str:
    """'bullish'/'bearish' if the histogram crossed zero within the persist window."""
    persist = settings.MACD_CROSSOVER_PERSIST_DAYS
    h = hist.dropna().to_numpy()
    if len(h) < 2:
        return "none"
    recent = h[-(persist + 1):]
    for i in range(len(recent) - 1, 0, -1):  # most recent cross wins
        if recent[i - 1] <= 0 < recent[i]:
            return "bullish"
        if recent[i - 1] >= 0 > recent[i]:
            return "bearish"
    return "none"


def _macd_hist_trend(hist: pd.Series) -> str:
    """'growing'/'shrinking'/'flat' from the slope of the recent histogram."""
    h = hist.dropna()
    if len(h) < 4:
        return "flat"
    recent = h.iloc[-4:].to_numpy()
    slope = float(np.polyfit(np.arange(len(recent)), recent, 1)[0])
    deadband = 0.1 * float(np.abs(h.iloc[-20:]).mean()) if len(h) >= 20 else 0.0
    if slope > deadband:
        return "growing"
    if slope < -deadband:
        return "shrinking"
    return "flat"


def _bollinger(close: pd.Series, price: float) -> dict:
    p, k = settings.BOLLINGER_PERIOD, settings.BOLLINGER_STD
    keys = ("bb_upper", "bb_middle", "bb_lower", "bb_width", "bb_pct")
    if len(close) < p:
        return {x: float("nan") for x in keys} | {"bb_position": None, "bb_squeeze": None}
    mid = close.rolling(p).mean()
    std = close.rolling(p).std(ddof=0)
    upper, lower = mid + k * std, mid - k * std
    up, mi, lo = float(upper.iloc[-1]), float(mid.iloc[-1]), float(lower.iloc[-1])
    width = up - lo
    pct = (price - lo) / width if width > 0 else float("nan")
    out = {
        "bb_upper": up, "bb_middle": mi, "bb_lower": lo, "bb_width": width,
        "bb_pct": pct, "bb_position": _bb_position(pct),
        "bb_squeeze": _bb_squeeze((upper - lower) / mid),
    }
    return out


def _bb_position(pct: float) -> str | None:
    if pd.isna(pct):
        return None
    if pct > 1:
        return "above_upper"
    if pct >= 0.8:
        return "near_upper"
    if pct < 0:
        return "below_lower"
    if pct <= 0.2:
        return "near_lower"
    return "middle"


def _bb_squeeze(norm_width: pd.Series) -> bool | None:
    """True when band width (normalized by the mid) is in its recent bottom 20%."""
    nw = norm_width.dropna()
    if len(nw) < 20:
        return None
    look = nw.iloc[-_SQUEEZE_LOOKBACK:]
    return bool(nw.iloc[-1] <= look.quantile(0.2))


def _volume(df: pd.DataFrame) -> dict:
    keys = ("vol_20d_avg", "vol_ratio")
    vol = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0).reset_index(drop=True)
    if vol.sum() == 0:  # flat-NAV fund / no volume reported
        return {x: float("nan") for x in keys} | {"vol_trend": None}
    period = settings.VOLUME_AVG_PERIOD
    avg = vol.rolling(period).mean()
    avg_now = float(avg.iloc[-1]) if len(vol) >= period else float("nan")
    ratio = float(vol.iloc[-1]) / avg_now if avg_now and avg_now > 0 else float("nan")
    return {"vol_20d_avg": avg_now, "vol_ratio": ratio, "vol_trend": _vol_trend(avg, period)}


def _vol_trend(avg: pd.Series, period: int) -> str | None:
    """Recent 20d average volume vs the 20d average one period earlier."""
    a = avg.dropna()
    if len(a) < period + 1:
        return None
    now, prior = float(a.iloc[-1]), float(a.iloc[-1 - period])
    if prior <= 0:
        return None
    r = now / prior
    return "increasing" if r > 1.1 else "decreasing" if r < 0.9 else "flat"


def _atr_pct(df: pd.DataFrame, price: float) -> float:
    """Wilder ATR as % of price, on split-adjusted high/low. NaN for flat NAV."""
    period = settings.ATR_PERIOD
    if len(df) <= period:
        return float("nan")
    close = pd.to_numeric(df["close"], errors="coerce")
    adj = pd.to_numeric(df["adj_close"], errors="coerce")
    factor = (adj / close).replace([np.inf, -np.inf], np.nan)
    high = pd.to_numeric(df["high"], errors="coerce") * factor
    low = pd.to_numeric(df["low"], errors="coerce") * factor
    if float((high - low).abs().sum()) == 0:  # no intraday range -> not applicable
        return float("nan")
    prev = adj.shift()
    tr = pd.concat([(high - low).abs(), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().iloc[-1]
    return float(atr) / price * 100 if price and price > 0 and pd.notna(atr) else float("nan")


def _week52(close: pd.Series, price: float) -> dict:
    """Signed distance from the 52-week high/low (0 = at the extreme)."""
    win = close.tail(_WEEK52)
    hi, lo = float(win.max()), float(win.min())
    return {
        "pct_from_52w_high": (price / hi - 1) * 100 if hi > 0 else float("nan"),
        "pct_from_52w_low": (price / lo - 1) * 100 if lo > 0 else float("nan"),
    }


def _trend(close: pd.Series) -> str:
    """Peak-detection trend over the last year (HH/HL vs LL/LH on swing points)."""
    win = close.tail(_TREND_WINDOW).to_numpy()
    if len(win) < _TREND_MIN:
        return "sideways"
    prom = settings.PEAK_PROMINENCE * float(np.mean(win))
    highs, _ = find_peaks(win, prominence=prom, distance=settings.PEAK_DISTANCE)
    lows, _ = find_peaks(-win, prominence=prom, distance=settings.PEAK_DISTANCE)
    if len(highs) < 2 or len(lows) < 2:
        return "sideways"
    hh = win[highs[-1]] > win[highs[-2]]
    hl = win[lows[-1]] > win[lows[-2]]
    ll = win[lows[-1]] < win[lows[-2]]
    lh = win[highs[-1]] < win[highs[-2]]
    if hh and hl:
        return "strong_uptrend"
    if ll and lh:
        return "strong_downtrend"
    if hh or hl:
        return "weak_uptrend"
    if ll or lh:
        return "weak_downtrend"
    return "sideways"
