"""
Regular dividends — specials removed, pay rates, cuts (fix list §9, §21; 2026-11-15).

Turns one symbol's full-history dividend payments into the numbers the Income
metrics and the dividend fair-value model need. Replaces "each calendar year's
highest single payment", which a special dividend inflated (T. Rowe Price's
streak reset to 3), a switch to monthly payments lowered without a cut (UDR) and
a late-year cut didn't touch until a full year had passed (Alexandria: streak 15
after a 45% cut).

Steps, all on plain numpy arrays (runs for ~26k payers incl. funds per analysis):

  1. **Pay rate per payment** = amount × payments per year, the frequency snapped
     to {1, 2, 3, 4, 6, 12, 26, 52} from the larger gap to a neighbour. Rates,
     not amounts, so a frequency switch (UDR quarterly -> monthly) changes nothing.
  2. **Special** = a rate > `DIV_SPECIAL_MULTIPLE` × the median of the lower half
     of the rates within ±400 days, unless the level persists (no lower payment
     follows within 400 days: STAG's monthly -> quarterly switch). Lower half, so
     frequent specials can't raise the yardstick (W.R. Berkley). A special that
     sits ON the regular schedule (dropping it would leave a hole > 1.5x the
     normal spacing: CME's $7.45 = $1.30 regular + special) is kept at the regular
     level. Not run for semi-annual / annual payers: their interim and final
     payments differ by design (Diageo, most UK / European ADRs).
  3. **Yearly rate** = median pay rate of a calendar year's regular payments
     (semi-annual / annual payers: the trailing-year sum at each payment).
  4. **Recent cut** (last ~2 years) = current rate < 92% of the median rate 1-2
     years ago AND the last 12 months' regular total < 95% of the 12 months
     before — both, so a shifted payment date doesn't read as a cut. A payer
     whose last payment is overdue (> 2x its spacing + 30 days) counts as
     suspended: current rate 0.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from config import settings

_FREQS = np.array([1, 2, 3, 4, 6, 12, 26, 52], dtype=float)
_LOG_FREQS = np.log(_FREQS)
_WINDOW_DAYS = 400          # ±window for the special yardstick / "level persisted" test
_LOW_FREQ_SPACING = 120     # median spacing above this = semi-annual / annual payer
_HOLE_FACTOR = 1.5          # dropping a special leaves a hole this x normal spacing -> on-schedule
_TTM_DAYS = 365
_LOW_FREQ_SUM_DAYS = 305    # trailing window for a semi-annual / annual payer's rate


@dataclass(frozen=True)
class DividendView:
    """Regular-dividend summary of one payer (amounts in the listing's currency)."""

    rate_now: float            # current regular annual rate (0 when suspended)
    reg_ttm: float             # regular payments in the last 365 days
    year_rates: pd.Series      # complete calendar year -> regular annual rate
    recent_cut: float          # 1.0 / 0.0, NaN when too few payments to judge
    years_paid: int            # unbroken calendar years with any payment, to the last full year
    n_specials: int


def _med(a: np.ndarray) -> float:
    """Median of a small 1-D array — np.median's overhead (~50 µs a call) dominated
    the run once the per-payment loop was gone."""
    a = np.sort(a)
    k = len(a)
    return float(a[(k - 1) // 2] + a[k // 2]) / 2


def _per_year(gap_days: np.ndarray) -> np.ndarray:
    """Payments per year for spacings in days, snapped to the usual schedules."""
    f = 365.25 / np.maximum(gap_days, 1.0)
    return _FREQS[np.abs(np.log(f)[:, None] - _LOG_FREQS[None, :]).argmin(axis=1)]


def _lower_half_median(x: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """Per row i, the median of the lower half of x[lo[i]:hi[i]] — one padded 2-D
    sort instead of a median call per payment (that loop was ~90% of the run time:
    230 s for the 30k payers incl. funds)."""
    n = len(x)
    width = int((hi - lo).max())
    cols = lo[:, None] + np.arange(width)[None, :]
    inside = cols < hi[:, None]
    w = np.where(inside, x[np.minimum(cols, n - 1)], np.inf)
    w.sort(axis=1)
    h = np.maximum(1, ((hi - lo) + 1) // 2)        # lower-half length per row
    rows = np.arange(n)
    return (w[rows, (h - 1) // 2] + w[rows, h // 2]) / 2


def _specials(t: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    """(keep mask, amounts with on-schedule specials reset to the regular level, count)."""
    n = len(v)
    keep, out = np.ones(n, bool), v.copy()
    gaps = np.diff(t).astype(float)
    if n < 3 or _med(gaps) > _LOW_FREQ_SPACING:
        return keep, out, 0
    prev = np.r_[gaps[0], gaps]
    nxt = np.r_[gaps, gaps[-1]]
    rate = v * _per_year(np.maximum(prev, nxt))
    lo = np.searchsorted(t, t - _WINDOW_DAYS, side="left")
    hi = np.searchsorted(t, t + _WINDOW_DAYS, side="right")
    high = rate > settings.DIV_SPECIAL_MULTIPLE * _lower_half_median(rate, lo, hi)
    if not high.any():
        return keep, out, 0
    pair_ok = ~(high[1:] | high[:-1])
    normal = _med(gaps[pair_ok]) if pair_ok.any() else _med(gaps)
    count = 0
    for i in np.flatnonzero(high):
        later = slice(i + 1, hi[i])
        if hi[i] > i + 1:
            if high[later].all():
                continue                      # the level persisted: a new normal
        elif i > 0 and high[i - 1]:
            continue                          # latest payment, the one before also high
        count += 1
        if 0 < i < n - 1 and t[i + 1] - t[i - 1] > _HOLE_FACTOR * normal:
            near = ~high[lo[i]:hi[i]]
            out[i] = _med(v[lo[i]:hi[i]][near]) if near.any() else v[i]
        else:
            keep[i] = False
    return keep, out, count


def _rates(t: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Annual pay rate at each regular payment (median of up to 4 trailing gaps)."""
    n = len(v)
    if n < 2:
        return np.full(n, np.nan)
    gaps = np.diff(t).astype(float)
    # Payment i (>= 1) uses the median of gaps[i-4:i]; the first payments use what
    # they have, payment 0 borrows the first gap.
    med = np.empty(n)
    med[0] = gaps[0]
    for i in range(1, min(n, 4)):
        med[i] = _med(gaps[:i])
    if n > 4:
        med[4:] = np.median(np.lib.stride_tricks.sliding_window_view(gaps, 4), axis=1)
    f = _per_year(med)
    rate = v * f
    low = f <= 2
    if low.any():
        csum = np.r_[0.0, np.cumsum(v)]
        start = np.searchsorted(t, t - _LOW_FREQ_SUM_DAYS, side="right")
        idx = np.arange(n)
        rate[low] = (csum[idx + 1] - csum[start])[low]
    return rate


def analyse(paid: pd.Series, as_of: pd.Timestamp) -> DividendView:
    """Regular-dividend view of `paid` (datetime-indexed amounts > 0) as of `as_of`."""
    paid = paid.sort_index()
    t = paid.index.values.astype("datetime64[D]").astype(np.int64)
    v = paid.to_numpy(dtype=float)
    now = np.datetime64(pd.Timestamp(as_of).date(), "D").astype(np.int64)
    last_full = pd.Timestamp(as_of).year - 1

    keep, amounts, n_special = _specials(t, v)
    rt, rv = t[keep], amounts[keep]
    rates = _rates(rt, rv)

    years = paid.index.year.to_numpy()[keep]
    ok = ~np.isnan(rates) & (years <= last_full)
    ys, rs = years[ok], rates[ok]
    order = np.lexsort((rs, ys))                   # by year, then rate
    ys, rs = ys[order], rs[order]
    uy, start, cnt = np.unique(ys, return_index=True, return_counts=True)
    med = (rs[start + (cnt - 1) // 2] + rs[start + cnt // 2]) / 2   # per-year median
    year_rates = pd.Series(med, index=uy.astype(int), dtype="float64")

    all_years = set(paid.index.year)
    years_paid, y = 0, last_full
    while y in all_years:
        years_paid, y = years_paid + 1, y - 1

    reg_ttm = float(rv[rt > now - _TTM_DAYS].sum())
    rate_now = float(rates[-1]) if len(rates) and not np.isnan(rates[-1]) else float("nan")
    if len(rt) >= 2:
        spacing = _med(np.diff(rt[-6:]).astype(float))
        if now - rt[-1] > 2 * spacing + 30:   # overdue -> suspended
            rate_now = 0.0

    recent_cut = float("nan")
    if (rt > now - 3 * 365).sum() >= 5:
        before = rates[(rt <= now - 365) & (rt > now - 730)]
        before = before[~np.isnan(before)]
        if len(before) and pd.notna(rate_now):
            prev_ttm = float(rv[(rt > now - 730) & (rt <= now - 365)].sum())
            recent_cut = float(rate_now < 0.92 * _med(before)
                               and reg_ttm < 0.95 * prev_ttm)
    return DividendView(rate_now, reg_ttm, year_rates, recent_cut, years_paid, n_special)


def no_cut_streak(year_rates: pd.Series, tolerance: float) -> int:
    """Trailing run of consecutive calendar years whose rate did not fall by more
    than `tolerance` (a fraction: rounding, or currency moves for foreign payers)."""
    if year_rates.empty:
        return 0
    idx = year_rates.index.to_numpy()
    v = year_rates.to_numpy(dtype=float)
    n = 0
    for i in range(len(v) - 1, 0, -1):
        if idx[i] - idx[i - 1] != 1 or v[i] < v[i - 1] * (1 - tolerance):
            break
        n += 1
    return n
