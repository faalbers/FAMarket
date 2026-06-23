"""
Earnings-surprise & ownership signals (Topic 4.1 extension).

Reads each symbol's RAW rows from signals.db — `earnings_surprise` (tidy, one row
per reported quarter) and `ownership` (one snapshot row per symbol), written by
`YFinanceSignals` — and derives the filterable per-symbol metrics the analysis
layer exposes: wishlist #6 (earnings surprise + next earnings date) and #7
(insider buying).

Percent convention (see [[param-unit-percent-storage]]): the fetcher stored the
yfinance fractions RAW; here we convert to percent numbers (0.10 -> 10.0). Every
output is NaN ("not applicable") when its inputs are missing (funds, no coverage).
These are all "good-everywhere" signals — judged absolute/universe, never vs sector
peers — so they carry no currency/peer baggage. See [[analyst-estimates-feature]].
"""

from __future__ import annotations

import pandas as pd

# Most-recent reported quarters the surprise stats summarize.
_SURPRISE_WINDOW = 4

COLUMNS = (
    "earnings_surprise_avg", "earnings_surprise_last", "earnings_beat_rate",
    "days_to_next_earnings", "insider_net_buy_pct", "institutions_count",
)


def compute(symbol: str, surprise: pd.DataFrame | None,
            ownership: pd.Series | None, *, asof: pd.Timestamp | None = None) -> dict:
    """All earnings-surprise + ownership metrics for one symbol.

    `surprise` = this symbol's `earnings_surprise` rows (or None); `ownership` = its
    one `ownership` row as a Series (or None); `asof` = the reference date for
    days_to_next_earnings (the analysis vintage; pass it once from the caller).
    """
    out: dict[str, float] = {c: float("nan") for c in COLUMNS}

    # -- earnings surprise (last N reported quarters) ----------------------- #
    if (surprise is not None and len(surprise)
            and "surprise_percent" in surprise.columns):
        s = surprise
        if "period_end" in s.columns:  # chronological so "last" is the newest quarter
            s = s.assign(_pe=pd.to_datetime(s["period_end"], errors="coerce")).sort_values("_pe")
        sp = pd.to_numeric(s["surprise_percent"], errors="coerce").dropna().tail(_SURPRISE_WINDOW)
        if not sp.empty:
            out["earnings_surprise_avg"] = float(sp.mean()) * 100
            out["earnings_surprise_last"] = float(sp.iloc[-1]) * 100
            out["earnings_beat_rate"] = float((sp > 0).mean()) * 100

    # -- ownership snapshot ------------------------------------------------- #
    if ownership is not None:
        net = pd.to_numeric(_cell(ownership, "insider_pct_net"), errors="coerce")
        if pd.notna(net):
            out["insider_net_buy_pct"] = float(net) * 100
        ic = pd.to_numeric(_cell(ownership, "institutions_count"), errors="coerce")
        if pd.notna(ic):
            out["institutions_count"] = float(ic)
        days = _days_until(_cell(ownership, "next_earnings_date"), asof)
        if days is not None:
            out["days_to_next_earnings"] = days

    return out


def _cell(row: pd.Series, key: str):
    """A cell from a Series, NaN when the column is absent."""
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return float("nan")


def _days_until(date_val, asof: pd.Timestamp | None) -> float | None:
    """Calendar days from `asof` (the analysis vintage) to a stored date string."""
    if date_val is None or (isinstance(date_val, float) and pd.isna(date_val)):
        return None
    ts = pd.to_datetime(date_val, errors="coerce")
    if pd.isna(ts):
        return None
    base = pd.Timestamp(asof) if asof is not None else pd.Timestamp.utcnow().tz_localize(None)
    return float((ts.normalize() - base.normalize()).days)
