"""
Reported-period series: the fundamentals bar and growth-line views, plus the
dividend-yield line.

Ratios reuse the SAME formula functions as the analysis snapshot
(`metrics.RATIO_PERIOD_METRICS`), so a ratio is defined once and never
re-implemented for a chart. Extracted from `ui/pages/charts.py` unchanged.
"""

from __future__ import annotations

from typing import Any, cast

import pandas as pd

from analysis_layer import metrics
from config import settings
from core.database import Database

FREQ_LABELS = {"Annual": "annual", "Quarterly": "quarterly"}

# A period-aware gap break: reporting periods sit a quarter/year apart, so a
# MISSING period is a much wider hole than a daily-price gap.
PERIOD_GAP_DAYS = {"annual": 550, "quarterly": 135}


# --------------------------------------------------------------------------- #
# loads
# --------------------------------------------------------------------------- #
def _true_annual(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep only genuine fiscal-year-end rows.

    The EDGAR backfill tags some quarter-end frames as freq='annual' (sparse,
    off-cycle), so a raw annual read shows several rows per year, most empty.
    The real annual rows share the fiscal year-end month and carry the full-year
    figures. Detect that month from the most recent revenue-bearing row, keep
    only that month, then one row per year (the most complete).
    """
    if frame.empty:
        return frame
    period_end = frame["period_end"]
    anchor = frame
    if "total_revenue" in frame.columns:
        revenue = frame[pd.to_numeric(frame["total_revenue"], errors="coerce").notna()]
        if not revenue.empty:
            anchor = revenue
    month = int(period_end.loc[anchor.index[-1]].month)
    kept = frame[period_end.dt.month == month].copy()
    if kept.empty:  # heuristic missed — better everything than a blank chart
        return frame
    kept["_year"] = kept["period_end"].dt.year
    kept["_filled"] = kept.notna().sum(axis=1)  # completeness breaks source dupes
    kept = (
        kept.sort_values(["_year", "_filled", "period_end"])
        .drop_duplicates("_year", keep="last")
        .drop(columns=["_year", "_filled"])
    )
    return kept.sort_values("period_end")


def load_financials(symbol: str, freq: str) -> pd.DataFrame:
    """One symbol's reported periods for `freq`, oldest -> newest."""
    if not settings.FINANCIALS_DB.exists():
        return pd.DataFrame()
    with Database(settings.FINANCIALS_DB) as db:
        if not db.table_exists("financials"):
            return pd.DataFrame()
        frame = db.read("financials", where="symbol = ? AND freq = ?", params=[symbol, freq])
    if frame.empty:
        return frame
    frame["period_end"] = pd.to_datetime(frame["period_end"], errors="coerce")
    frame = frame[frame["period_end"].notna()].sort_values("period_end")
    return _true_annual(frame) if freq == "annual" else frame


def load_splits(symbol: str) -> pd.Series:
    """Split events as a datetime-indexed factor series (full history)."""
    if not settings.OHLCV_DB.exists():
        return pd.Series(dtype=float)
    with Database(settings.OHLCV_DB) as db:
        if not db.table_exists("ohlcv"):
            return pd.Series(dtype=float)
        frame = db.read("ohlcv", where="symbol = ?", params=[symbol])
    if frame.empty or "splits" not in frame.columns:
        return pd.Series(dtype=float)
    values = pd.Series(pd.to_numeric(frame["splits"], errors="coerce"))
    index = pd.DatetimeIndex(pd.to_datetime(frame["date"], errors="coerce"))
    series = pd.Series(values.to_numpy(), index=index)
    series = series[series.notna()]
    return cast(pd.Series, series[(series != 0) & (series != 1)])


def load_div_prices(symbol: str) -> pd.DataFrame:
    """Full daily RAW close + dividend events, date-indexed.

    Uses raw `close`, NOT adj_close: the `dividends` column is the nominal cash
    paid at the time, so each period's yield must divide by the contemporaneous
    unadjusted close — back-adjusted prices would inflate historical yields.
    """
    if not settings.OHLCV_DB.exists():
        return pd.DataFrame()
    with Database(settings.OHLCV_DB) as db:
        if not db.table_exists("ohlcv"):
            return pd.DataFrame()
        frame = db.read("ohlcv", where="symbol = ?", params=[symbol])
    if frame.empty or "close" not in frame.columns:
        return pd.DataFrame()

    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["dividends"] = (
        pd.to_numeric(frame["dividends"], errors="coerce") if "dividends" in frame.columns else 0.0
    )
    frame = frame[frame["date"].notna() & frame["close"].notna()]
    frame["dividends"] = frame["dividends"].fillna(0.0)
    return frame.set_index("date").sort_index()[["close", "dividends"]]


# --------------------------------------------------------------------------- #
# values
# --------------------------------------------------------------------------- #
def _num(value: Any) -> float:
    number = pd.to_numeric(value, errors="coerce")
    return float(number) if pd.notna(number) else float("nan")


def param_label(key: str) -> str:
    return metrics.RAW_PERIOD_FIELDS.get(key) or _RATIO_LABELS.get(key, key)


_RATIO_LABELS: dict[str, str] = {
    "gross_margin": "Gross margin",
    "operating_margin": "Operating margin",
    "net_margin": "Net margin",
    "fcf_margin": "FCF margin",
    "roe": "ROE",
    "roa": "ROA",
    "asset_turnover": "Asset turnover",
    "equity_multiplier": "Equity multiplier",
    "roe_roa_gap": "ROE-ROA gap",
    "debt_to_equity": "Debt / equity",
    "debt_to_ebitda": "Debt / EBITDA",
    "current_ratio": "Current ratio",
    "interest_coverage": "Interest coverage",
}


def options() -> list[dict[str, Any]]:
    """Every parameter the reported-period views can plot, grouped."""
    out: list[dict[str, Any]] = []
    for key, label in metrics.RAW_PERIOD_FIELDS.items():
        out.append({"key": key, "label": label, "category": "Statement items", "unit": "$"})
    for key, (_fn, _fields, unit) in metrics.RATIO_PERIOD_METRICS.items():
        out.append({"key": key, "label": param_label(key), "category": "Ratios", "unit": unit})
    return out


def period_values(frame: pd.DataFrame, param: str, symbol: str) -> tuple[list[float], str, str]:
    """Per-period values for `param` (oldest -> newest), with its kind and label.

    `kind` in {ratio, eps, money} tells the caller whether to scale (money ->
    B/M/K) or show as-is.
    """
    if param in metrics.RATIO_PERIOD_METRICS:
        func, fields, unit = metrics.RATIO_PERIOD_METRICS[param]
        values = [func(*[_num(row.get(f)) for f in fields]) for _, row in frame.iterrows()]
        return values, "ratio", f"{param_label(param)} ({unit})"

    if param == "diluted_eps":
        eps = pd.Series(
            [_num(row.get(param)) for _, row in frame.iterrows()],
            index=pd.DatetimeIndex(pd.to_datetime(frame["period_end"].values)),
        )
        eps = metrics.split_adjust(eps, load_splits(symbol))
        return (
            [float(v) if pd.notna(v) else float("nan") for v in eps.to_numpy()],
            "eps",
            f"{param_label(param)} (split-adjusted)",
        )

    return [_num(row.get(param)) for _, row in frame.iterrows()], "money", param_label(param)


def _scale_factor(max_abs: float) -> tuple[float, str]:
    """B/M/K divisor + suffix for the largest magnitude on an axis."""
    for divisor, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if max_abs >= divisor:
            return divisor, suffix
    return 1.0, ""


def period_label(when: Any, freq: str) -> str:
    """The period a value was computed FOR: `2025` or `2025-Q3`."""
    ts = pd.Timestamp(when)
    return f"{ts.year}" if freq == "annual" else f"{ts.year}-Q{(ts.month - 1) // 3 + 1}"


def _clean(value: float) -> float | None:
    return round(float(value), 4) if value == value else None


# --------------------------------------------------------------------------- #
# views
# --------------------------------------------------------------------------- #
def bar_view(symbol: str, param: str, freq: str) -> dict[str, Any]:
    """One symbol × one parameter, as bars across its reported periods."""
    frame = load_financials(symbol, freq)
    if frame.empty:
        return {"bars": [], "message": f"No {freq} data reported for {symbol}."}

    values, kind, label = period_values(frame, param, symbol)
    if not any(v == v for v in values):
        return {"bars": [], "message": f"No {freq} {param_label(param)} reported for {symbol}."}

    suffix = ""
    if kind == "money":
        divisor, suffix = _scale_factor(max((abs(v) for v in values if v == v), default=0.0))
        values = [v / divisor if v == v else float("nan") for v in values]

    bars = [
        {"period": period_label(when, freq), "value": _clean(value)}
        for when, value in zip(frame["period_end"], values)
    ]
    return {
        "bars": bars,
        "kind": kind,
        "y_label": label + (f" ({suffix})" if suffix else ""),
        "message": None,
    }


def _last_clean_run_start(dates: list[Any], values: list[float], gap: int) -> int:
    """Index where the most recent unbroken run of periods begins."""
    start = 0
    previous = None
    for i, (when, value) in enumerate(zip(dates, values)):
        valid = value is not None and value == value
        if previous is not None and (when - previous).days > gap:
            start = i  # time gap before i -> run restarts at i
        if not valid:
            start = i + 1  # NaN at i -> run restarts after i
        previous = when
    return start


def _points(dates: list[Any], values: list[float], freq: str) -> list[dict[str, Any]]:
    """Points with a null where a reporting period is missing, so the line breaks."""
    gap = PERIOD_GAP_DAYS.get(freq, 135)
    out: list[dict[str, Any]] = []
    previous = None
    for when, value in zip(dates, values):
        if previous is not None and (when - previous).days > gap:
            middle = previous + (when - previous) / 2
            out.append({"time": middle.strftime("%Y-%m-%d"), "value": None})
        out.append({"time": pd.Timestamp(when).strftime("%Y-%m-%d"), "value": _clean(value)})
        previous = when
    return out


def line_view(symbols: list[str], param: str, freq: str, normalized: bool) -> dict[str, Any]:
    """One parameter across all symbols, as lines over reported periods."""
    per_symbol: list[tuple[str, list[Any], list[float]]] = []
    no_data: list[str] = []
    label = ""
    kind = "money"

    for symbol in symbols:
        frame = load_financials(symbol, freq)
        if frame.empty:
            no_data.append(symbol)
            continue
        values, kind, label = period_values(frame, param, symbol)
        if not any(v == v for v in values):
            no_data.append(symbol)
            continue
        per_symbol.append((symbol, list(frame["period_end"]), values))

    if not per_symbol:
        return {
            "lines": [],
            "no_data": no_data,
            "message": f"No {freq} {param_label(param)} reported for any selected symbol.",
        }

    # Trim to the most recent unbroken run: per symbol find where its last gap
    # ends, then cut every symbol at the EARLIEST such start so they share one
    # window. A gap-free parameter keeps its full history.
    gap = PERIOD_GAP_DAYS.get(freq, 135)
    starts = [
        dates[index]
        for _, dates, values in per_symbol
        if (index := _last_clean_run_start(dates, values, gap)) < len(dates)
    ]
    cut = min(starts) if starts else None
    if cut is not None:
        trimmed: list[tuple[str, list[Any], list[float]]] = []
        for symbol, dates, values in per_symbol:
            kept = [(d, v) for d, v in zip(dates, values) if d >= cut]
            if kept and any(v == v for _, v in kept):
                trimmed.append((symbol, [d for d, _ in kept], [v for _, v in kept]))
            else:
                no_data.append(symbol)
        per_symbol = trimmed

    lines: list[dict[str, Any]] = []
    skipped: list[str] = []

    if normalized:
        y_label = f"{label} — normalized to 100"
        for symbol, dates, values in per_symbol:
            # A negative/zero base can't be indexed meaningfully.
            base = next((v for v in values if v == v and v > 0), None)
            if base is None:
                skipped.append(symbol)
                continue
            scaled = [v / base * 100.0 if v == v else float("nan") for v in values]
            lines.append({"name": symbol, "points": _points(dates, scaled, freq)})
    else:
        divisor, suffix = 1.0, ""
        if kind == "money":
            largest = max(
                (abs(v) for _, _, values in per_symbol for v in values if v == v), default=0.0
            )
            divisor, suffix = _scale_factor(largest)
        y_label = label + (f" ({suffix})" if suffix else "")
        for symbol, dates, values in per_symbol:
            scaled = [v / divisor if v == v else float("nan") for v in values]
            lines.append({"name": symbol, "points": _points(dates, scaled, freq)})

    return {
        "lines": lines,
        "no_data": no_data,
        "skipped": skipped,
        "baseline": 100.0 if normalized else None,
        "y_label": y_label,
        "cut_from": pd.Timestamp(cut).strftime("%Y-%m-%d") if cut is not None else None,
        "message": None
        if lines
        else "Nothing to plot normalized (every symbol's first value is ≤ 0). Try Actual.",
    }


def period_yields(frame: pd.DataFrame, freq: str) -> tuple[list[Any], list[float]]:
    """Calendar-period dividend yields (%), oldest -> newest.

    Per period: summed dividends ÷ the period-end close × 100. A period with a
    price but no payout yields a real 0%. The still-running period is dropped —
    its dividends and price aren't final yet.
    """
    if frame.empty:
        return [], []
    rule = "YE" if freq == "annual" else "QE"
    period_close = frame["close"].resample(rule).last()
    dividends = frame["dividends"].resample(rule).sum()
    last_bar = frame.index.max()
    mask = period_close.notna() & (period_close > 0) & (period_close.index <= last_bar)
    yields = dividends[mask] / period_close[mask] * 100.0
    return list(yields.index), [float(v) for v in yields.to_numpy()]


def dividend_view(symbols: list[str], freq: str, normalized: bool) -> dict[str, Any]:
    """Dividend yield per calendar period, one line per symbol."""
    per_symbol: list[tuple[str, list[Any], list[float]]] = []
    no_data: list[str] = []

    for symbol in symbols:
        frame = load_div_prices(symbol)
        dates, values = period_yields(frame, freq) if not frame.empty else ([], [])
        # An all-zero series means the symbol never paid a dividend.
        if not dates or not any(v == v and v != 0 for v in values):
            no_data.append(symbol)
            continue
        per_symbol.append((symbol, dates, values))

    if not per_symbol:
        return {
            "lines": [],
            "no_data": no_data,
            "message": "None of the selected symbols paid a dividend in the available history.",
        }

    lines: list[dict[str, Any]] = []
    skipped: list[str] = []
    if normalized:
        y_label = "Dividend yield — normalized to 100"
        for symbol, dates, values in per_symbol:
            base = next((v for v in values if v == v and v > 0), None)
            if base is None:
                skipped.append(symbol)
                continue
            scaled = [v / base * 100.0 if v == v else float("nan") for v in values]
            lines.append({"name": symbol, "points": _points(dates, scaled, freq)})
    else:
        y_label = "Dividend yield (%)"
        for symbol, dates, values in per_symbol:
            lines.append({"name": symbol, "points": _points(dates, values, freq)})

    return {
        "lines": lines,
        "no_data": no_data,
        "skipped": skipped,
        "baseline": 100.0 if normalized else None,
        "y_label": y_label,
        "message": None if lines else "Nothing to plot normalized. Try Actual.",
    }
