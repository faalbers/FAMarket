"""Sector & sub-industry index series (Analysis stage).

Builds a daily **base-100 level series** for every Yahoo *sector* and every
*sector | industry* group, using the SPDR Select Sector index formula with the
current ("new", post-2024-09-23) capping rules. Adapted from the self-contained
`sector_industry_index.py` prototype at the repo root — the formula, capping and
quarterly-rebalance mechanics are kept verbatim (those ARE the spec); only the
loaders were rewired to consume the panels the analysis pipeline has *already*
loaded, so this stage adds no extra large DB reads.

THE FORMULA (unchanged from the prototype)
    Index_t = ( sum_i  P_{i,t} * Shares_{i,t} * IWF_i * AWF_i ) / Divisor
  P            adj_close (total return) drives the daily moves
  Shares       shares outstanding interpolated daily from financials reports,
               put on the SAME split basis as the price (see below)
  IWF          floatShares / sharesOutstanding (snapshot, from quotes)
  AWF          capping factor set each quarterly rebalance (1.0 if uncapped)
Weights are float-MC each quarter (third-Friday effective date), capped with
`cap_weights_new`, then held fixed between rebalances. Each series is rebased to
100 at its own effective start, so levels are *growth since start*, not absolute.

MARKET CAP ON ONE SPLIT BASIS (2026-11-08, ROADMAP 4.5)
  Yahoo's `close`/`adj_close` are adjusted for every LATER split, but report share
  counts are as filed. Multiplying the two mis-sized every company that split after
  a date: NVDA pre-2021 ~40x too small, while reverse-split penny stocks got absurd
  caps (PPCB 2013: $4.7e13/share) and the 23% max weight, wrecking whole indices.
  Fixed the way Compustat (CSHOQ x AJEXQ) and CRSP (cfacshr) do it: each share
  count is multiplied by the splits after its date (`_split_adjust_shares`, which
  also spots counts a later filing already restated). Weights use `close` (split-
  only) x those shares, so dividends don't shrink old caps; returns use adj_close.
  Then units: a count in other units than the ticker's price (Class A counts on
  BRK-B, home-market shares on an ADR) is rescaled so today's cap matches Yahoo's
  marketCap (`_calibrate_share_units`), and a float share below 5% — mixed units —
  is recomputed from another share count (`_iwf`).

UNIVERSE / DISTORTION GUARD
  The raw universe includes illiquid/penny names that can dominate (and warp) a
  group's index. We feed in only active+validated symbols that also clear a
  liquidity floor TODAY (avg daily dollar volume, `settings.INDEX_MIN_AVG_DOLLAR_VOLUME`),
  and at every rebalance a name is only weighted if it cleared the same floor
  THEN (trailing close x volume, split-invariant) — point-in-time eligibility, as
  S&P re-checks liquidity each quarter — and only if a year of that trading is at
  least `settings.INDEX_MIN_LIQUIDITY_RATIO` of its float cap (S&P's float-adjusted
  liquidity ratio), which drops phantom caps from bad share counts (REGN 2012 came
  out at $8.5T). Plus a `min_active` breadth guard so no
  single early name defines the base. And a price-break guard: a one-day move beyond
  `settings.INDEX_PRICE_BREAK_RATIO` (10x either way — an unadjusted reverse split,
  a bankruptcy re-listing) counts as 0% for that name, as a divisor adjustment would.

DATE RANGE (decoupled from the analysis OHLCV window — 2026-06-14)
  The index history is NOT tied to `ANALYSIS_OHLCV_LOOKBACK_DAYS` (that ~2yr bound
  exists only to cap the full-table read the per-symbol metrics need). Instead the
  start is DATA-DRIVEN: the earliest date by which at least
  `settings.INDEX_START_MIN_REPORTERS` constituents have a share report in
  financials.db (a breadth guard so one deep-history outlier can't drag the read
  back decades). Prices are then read with a dedicated, memory-efficient reader
  (`_deep_panels`): only adj_close/close/volume, only the liquid constituents (~4-5k, not
  the 38k universe), in symbol-chunks so the peak footprint is ~one chunk — far
  below the full-table read that forced the analysis window in the first place.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import settings
from core.database import Database
from core.logging_config import get_logger

log = get_logger("analysis")

TABLE = "sector_industry_index"
META_TABLE = "index_meta"

# Symbols per IN(...) chunk for the deep price read — stays well under SQLite's
# bound-parameter limit and keeps each query's materialized frame small.
_PRICE_CHUNK = 500


def _chunked(seq, n=_PRICE_CHUNK):
    """Yield slices of <=n items (so an IN (...) list stays under SQLite's limit)."""
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]

BASE_LEVEL = 100.0  # every index series starts at 100

# Current ("new", effective 2024-09-23) Select Sector capping parameters.
SINGLE_CAP = 0.24        # no single constituent may exceed 24% ...
SINGLE_CAP_TO = 0.23     # ... and any that does is reduced to 23%
GROUP_THRESHOLD = 0.048  # constituents weighing > 4.8% form the "concentrated" group ...
GROUP_CAP = 0.50         # ... whose combined weight may not exceed 50% ...
GROUP_CAP_TO = 0.45      # ... and is scaled down to 45% when it does
SMALL_CAP = 0.045        # all remaining (tail) names are capped at 4.5%


# ==================================================================================
# CAPPING — the current ("new") Select Sector rule only.  (verbatim from prototype)
# ==================================================================================

def _distribute_to_tail(w: pd.Series, excess: float, cap: float = SMALL_CAP) -> pd.Series:
    """Water-fill `excess` weight onto names below `cap`, never pushing any above `cap`."""
    w = w.copy()
    for _ in range(200):
        if excess <= 1e-15:
            break
        room = (cap - w).clip(lower=0.0)
        room = room[room > 1e-15]
        if room.sum() <= 1e-15:
            break  # no capacity left below the cap
        add = min(excess, room.sum())
        w.loc[room.index] = w.loc[room.index] + room / room.sum() * add  # proportional to free room
        excess -= add
    return w


def cap_weights_new(weights: pd.Series) -> pd.Series:
    """Apply the current Select Sector capping to a weight vector (input/output sum to 1)."""
    w = weights.astype(float).copy()
    for _ in range(100):
        excess = 0.0
        # Step 1: cap any single oversized name.
        over = w[w > SINGLE_CAP]
        if len(over):
            excess += float((over - SINGLE_CAP_TO).sum())
            w.loc[over.index] = SINGLE_CAP_TO
        # Step 2: cap the combined weight of the concentrated (>4.8%) cohort.
        grp = w[w > GROUP_THRESHOLD]
        if grp.sum() > GROUP_CAP + 1e-12:
            scaled = grp * (GROUP_CAP_TO / grp.sum())
            excess += float((grp - scaled).sum())
            w.loc[grp.index] = scaled
        # Step 3: hand the freed weight to the tail (names under 4.5%).
        if excess > 1e-15:
            w = _distribute_to_tail(w, excess, cap=SMALL_CAP)
        # Stop once both diversification limits hold.
        if not ((w > SINGLE_CAP + 1e-9).any() or w[w > GROUP_THRESHOLD].sum() > GROUP_CAP + 1e-9):
            break
    return w / w.sum()


# ==================================================================================
# REBALANCE SCHEDULE — quarterly, third Friday of Mar/Jun/Sep/Dec.  (verbatim)
# ==================================================================================

def quarterly_rebalance_dates(index: pd.DatetimeIndex) -> set:
    """Trading days on/after the third Friday of each quarter-end month, plus the start."""
    if len(index) == 0:
        return set()
    dates = {index[0]}  # always treat the first available day as the initial rebalance
    for year in range(index[0].year, index[-1].year + 1):
        for month in (3, 6, 9, 12):
            first = pd.Timestamp(year=year, month=month, day=1)
            first_friday = first + pd.Timedelta(days=(4 - first.dayofweek) % 7)
            third_friday = first_friday + pd.Timedelta(days=14)
            pos = index.searchsorted(third_friday)
            if pos < len(index):
                dates.add(index[pos])
    return {d for d in dates if index[0] <= d <= index[-1]}


# ==================================================================================
# CORE ENGINE — one group of constituents -> a base-100 level series.
#
# Same method as the original day-by-day prototype loop, run on NumPy arrays instead
# (2026-11-08, ROADMAP 8.5). The prototype did a pandas label lookup per trading day
# (~3 ms of overhead x ~4,900 days x 154 groups = ~43 min a full run); this version
# takes ~1 min. Holdings are fixed between quarterly rebalances, so each quarter's
# daily values are one array product. The level chain is multiplied in the same order
# as the loop; checked against the loop's stored output on real data: all 647,165 rows
# present, max relative difference 1.5e-14 (summation-order rounding).
# ==================================================================================

def compute_index(
    constituents,
    prices: pd.DataFrame,
    shares: pd.DataFrame,
    iwf: pd.Series,
    cap: bool = True,
    start=None,
    end=None,
    min_active: int = 2,
    cap_prices: pd.DataFrame | None = None,
    eligible: pd.DataFrame | None = None,
    break_ratio: float | None = None,
) -> pd.Series:
    """Float-adjusted, capped, quarterly-rebalanced base-100 level series for one group.

    `prices` drive the daily moves. `cap_prices` (default: `prices`) size the market
    caps that set the weights; `shares` must be on the same split basis. `eligible`
    (bool, default all True) says which names may be weighted on each day — a name
    that fails it at a rebalance is left out until a later rebalance. `break_ratio`
    (default `settings.INDEX_PRICE_BREAK_RATIO`, 0 = off) neutralises one-day price
    jumps beyond that factor as data breaks.
    """
    if break_ratio is None:
        break_ratio = settings.INDEX_PRICE_BREAK_RATIO
    cols = [c for c in constituents if c in prices.columns]
    if not cols:
        return pd.Series(dtype=float)

    def window(df: pd.DataFrame) -> pd.DataFrame:
        if start is not None:
            df = df[df.index >= pd.Timestamp(start)]
        if end is not None:
            df = df[df.index <= pd.Timestamp(end)]
        return df

    px = window(prices[cols])
    shr = window(shares[cols]).reindex(px.index)
    cpx = window(cap_prices[cols]).reindex(px.index) if cap_prices is not None else px
    ok_liq = (window(eligible[cols]).reindex(px.index).fillna(False).to_numpy(dtype=bool)
              if eligible is not None else np.ones(px.shape, dtype=bool))
    w_iwf = iwf.reindex(cols).fillna(1.0)

    # Forward-fill the odd missing price so a single NaN day doesn't drop a constituent.
    px_ff = px.ffill()
    prc = px_ff.to_numpy(dtype=float)
    cap_prc = cpx.ffill().to_numpy(dtype=float)
    shr_a = shr.to_numpy(dtype=float)
    with np.errstate(invalid="ignore"):
        active = (~np.isnan(prc) & ~np.isnan(shr_a) & (prc > 0)
                  & ~np.isnan(cap_prc) & (cap_prc > 0) & ok_liq)
        # Float-adjusted market cap, used purely to set the weights at each rebalance.
        fmc = cap_prc * shr_a * w_iwf.to_numpy(dtype=float)

    # Effective start = first day with at least `min_active` active constituents.
    n_active = active.sum(axis=1)
    started = np.flatnonzero(n_active >= min(min_active, max(1, len(cols))))
    if started.size == 0:
        return pd.Series(dtype=float)
    s0 = int(started[0])
    dates = px_ff.index[s0:]
    prc, fmc, active = prc[s0:], fmc[s0:], active[s0:]
    n = len(dates)

    is_reb = np.zeros(n, dtype=bool)
    is_reb[dates.get_indexer(sorted(quarterly_rebalance_dates(dates)))] = True
    col_index = pd.Index(cols)

    def rebalance(t: int):
        """Capped weights on day t -> (column positions, holdings, portfolio value)."""
        f = fmc[t]
        with np.errstate(invalid="ignore"):
            m = active[t] & ~np.isnan(f) & (f > 0)
        if not m.any():
            return None
        w = pd.Series(f[m] / f[m].sum(), index=col_index[m])   # raw float-adjusted weights
        if cap:
            w = cap_weights_new(w)                              # current Select Sector capping
        p = prc[t, m]
        hold = w.to_numpy() / p                                 # quantities reproducing those weights
        return np.flatnonzero(m), hold, float(np.nansum(hold * p))

    level = np.empty(n)
    cur_level = BASE_LEVEL
    pos: np.ndarray | None = None   # column positions of the current holdings
    hold: np.ndarray | None = None  # share-equivalent quantities; value = sum(hold * price)
    prev_value = 0.0

    t = 0
    while t < n:
        if pos is None or hold is None:
            # No holdings yet (day one, or no eligible names so far): try to start here.
            first = rebalance(t)
            if first is not None:
                pos, hold, prev_value = first
            level[t] = cur_level
            t += 1
            continue
        # Holdings stay fixed through the next rebalance day r. Day r itself first takes
        # the market move on the OLD holdings, then rebalances (level stays continuous).
        ahead = np.flatnonzero(is_reb[t:])
        r = t + int(ahead[0]) if ahead.size else n - 1
        seg = prc[t:r + 1][:, pos]
        if break_ratio > 0:
            # Price-break guard: a one-day jump beyond break_ratio either way is a data
            # break, not a move. Scale that holding by old/new price from that day on,
            # so its value (and the level) carries on unchanged — a divisor-style
            # corporate-action adjustment.
            with np.errstate(invalid="ignore", divide="ignore"):
                day = seg / np.vstack((prc[t - 1, pos], seg[:-1]))
                brk = (day > break_ratio) | (day < 1.0 / break_ratio)
            scale = np.cumprod(np.where(brk, 1.0 / day, 1.0), axis=0)
            values = np.nansum(seg * hold * scale, axis=1)
            hold = hold * scale[-1]   # carries on if no rebalance happens at r
        else:
            values = np.nansum(seg * hold, axis=1)
        ratios = values / np.concatenate(([prev_value], values[:-1]))
        # Multiply in the loop's order: ((cur * r1) * r2) * ...
        chain = np.cumprod(np.concatenate(([cur_level], ratios)))[1:]
        level[t:r + 1] = chain
        cur_level, prev_value = float(chain[-1]), float(values[-1])
        if is_reb[r]:
            nxt = rebalance(r)
            if nxt is not None:
                pos, hold, prev_value = nxt
        t = r + 1

    return pd.Series(level, index=dates).dropna()


# ==================================================================================
# PANEL BUILDERS — rebuilt from the panels the pipeline already loaded.
# ==================================================================================

def _shares_points(financials: pd.DataFrame) -> pd.DataFrame:
    """Tidy (symbol, period_end, shares) report points — ordinary_shares_number ▸ share_issued."""
    if financials.empty:
        return pd.DataFrame(columns=["symbol", "period_end", "shares"])
    shares = financials.get("ordinary_shares_number")
    if shares is None:
        shares = pd.Series(np.nan, index=financials.index)
    if "share_issued" in financials.columns:
        shares = shares.fillna(financials["share_issued"])
    pts = pd.DataFrame({
        "symbol": financials["symbol"],
        "period_end": financials.get("period_end_dt",
                                     pd.to_datetime(financials.get("period_end"), errors="coerce")),
        "shares": pd.to_numeric(shares, errors="coerce"),
    })
    pts = pts.dropna(subset=["period_end", "shares"])
    return pts[pts["shares"] > 0]


def _start_date(members, shares_pts: pd.DataFrame):
    """Data-driven index start: earliest date by which >= INDEX_START_MIN_REPORTERS
    constituents have a share report (breadth guard against deep-history outliers).

    Returns a Timestamp, or None when no member has share data (caller falls back).
    """
    if shares_pts.empty:
        return None
    f = shares_pts[shares_pts["symbol"].isin(set(members))]
    if f.empty:
        return None
    first = f.groupby("symbol")["period_end"].min().sort_values()
    k = min(max(1, settings.INDEX_START_MIN_REPORTERS), len(first))
    return first.iloc[k - 1]


def _deep_panels(symbols, start, field: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Memory-efficient wide panels from ohlcv.db, read back to `start`.

    Returns (return prices = `field`, split-only `close` for market caps, daily dollar
    volume = close x volume). Yahoo adjusts close down and volume up by the same split
    factor, so their product is the real money traded that day, split-invariant.
    Reads only those columns for the given (liquid) symbols, in IN(...) chunks,
    assembling one column per symbol directly — so the peak footprint is ~one chunk's
    frame plus the (narrow) growing panels, never the full OHLCV table.
    """
    if field not in ("adj_close", "close"):  # guard: only known price columns
        field = "adj_close"
    start_s = pd.Timestamp(start).strftime("%Y-%m-%d") if start is not None else "0001-01-01"
    ret_cols: dict = {}
    cap_cols: dict = {}
    dv_cols: dict = {}
    with Database(settings.OHLCV_DB) as db:
        for chunk in _chunked(symbols):
            ph = ",".join("?" * len(chunk))
            df = db.query(
                f"SELECT symbol, date, {field} AS px, close, volume FROM ohlcv "
                f"WHERE date >= ? AND symbol IN ({ph})",
                [start_s, *chunk],
            )
            if df.empty:
                continue
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            for c in ("px", "close", "volume"):
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df = df.dropna(subset=["date"])
            for sym, g in df.groupby("symbol", sort=False):
                g = g.drop_duplicates("date", keep="last").sort_values("date")
                idx = pd.DatetimeIndex(g["date"])
                close = g["close"].to_numpy()
                ret_cols[sym] = pd.Series(g["px"].to_numpy(), index=idx)
                cap_cols[sym] = pd.Series(close, index=idx)
                dv_cols[sym] = pd.Series(close * g["volume"].to_numpy(), index=idx)
            del df
    if not ret_cols:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    ret = pd.DataFrame(ret_cols).sort_index()
    return ret, pd.DataFrame(cap_cols).reindex(ret.index), pd.DataFrame(dv_cols).reindex(ret.index)


def _point_in_time_eligible(dollar_volume: pd.DataFrame, cap_prices: pd.DataFrame,
                            shares: pd.DataFrame, iwf: pd.Series) -> pd.DataFrame:
    """Bool panel: on each day, may the name be weighted? Two checks on that day's data.

    1. Trailing avg dollar volume clears `INDEX_MIN_AVG_DOLLAR_VOLUME` — the same floor
       and window as the today-only `_liquid_symbols` filter, so a rebalance only
       weights names that were actually traded THEN.
    2. Annualised dollar volume / float market cap >= `INDEX_MIN_LIQUIDITY_RATIO`
       (S&P's float-adjusted liquidity ratio). A market cap no real trading supports
       is a bad share count, and dropping it keeps a phantom giant out of the weights.
    Needs at least half a window of bars, so a brand-new listing waits a little.
    """
    ok = pd.DataFrame(True, index=dollar_volume.index, columns=dollar_volume.columns)
    win = settings.INDEX_LIQUIDITY_WINDOW_DAYS
    avg = dollar_volume.rolling(win, min_periods=max(1, win // 2)).mean()
    if settings.INDEX_MIN_AVG_DOLLAR_VOLUME > 0:
        ok &= avg >= settings.INDEX_MIN_AVG_DOLLAR_VOLUME
    if settings.INDEX_MIN_LIQUIDITY_RATIO > 0:
        fmc = (cap_prices.ffill() * shares.reindex(columns=cap_prices.columns)
               * iwf.reindex(cap_prices.columns).fillna(1.0))
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = avg * 252 / fmc
        ok &= ratio >= settings.INDEX_MIN_LIQUIDITY_RATIO
    return ok


def _split_events(symbols) -> dict[str, list[tuple[pd.Timestamp, float]]]:
    """{symbol: [(ex-date, ratio), ...] ascending} from ohlcv.db's sparse `splits` column.

    ratio = new shares per old share (2.0 for a 2:1 split, 0.1 for a 1-for-10
    reverse split). Read over the FULL history: a share report from 2008 needs every
    split after it, however recent.
    """
    out: dict[str, list[tuple[pd.Timestamp, float]]] = {}
    with Database(settings.OHLCV_DB) as db:
        for chunk in _chunked(symbols):
            ph = ",".join("?" * len(chunk))
            df = db.query(
                f"SELECT symbol, date, splits FROM ohlcv WHERE symbol IN ({ph}) "
                f"AND splits IS NOT NULL AND splits > 0 AND splits != 1",
                list(chunk),
            )
            for sym, g in df.groupby("symbol", sort=False):
                ev = sorted((pd.Timestamp(d), float(r)) for d, r in zip(g["date"], g["splits"]))
                out[sym] = ev
    return out


def _split_adjust_shares(shares_pts: pd.DataFrame, splits: dict) -> pd.DataFrame:
    """Put every share report on today's split basis (the basis Yahoo's prices use).

    A count reported for period P is multiplied by every split with an ex-date after
    P — Compustat's CSHOQ x AJEXQ. The catch: a filing made AFTER a split restates
    earlier periods (ASC 260), so some stored counts already include some of those
    splits, and adjusting them again would double-count. For each report the
    candidates are "already includes the first k later splits" (k = 0..m); walking
    from the newest report back, the candidate closest (log distance) to the next
    newer, already-resolved count wins, with ties going to k = 0. Real share counts
    rarely jump by a split-sized factor between two reports; splits always do.
    """
    if shares_pts.empty or not splits:
        return shares_pts
    parts = []
    for sym, grp in shares_pts.groupby("symbol", sort=False):
        ev = splits.get(sym)
        if not ev:
            parts.append(grp)
            continue
        grp = grp.sort_values("period_end", ascending=False)
        adjusted = []
        ref: float | None = None
        for pe, sh in zip(grp["period_end"], grp["shares"]):
            later = [r for d, r in ev if d > pe]
            # cands[k] = count if the report already includes the first k later splits
            cands = [sh * float(np.prod(later[k:])) for k in range(len(later) + 1)]
            if ref is None or not later:
                best = cands[0]
            else:
                best = min(cands, key=lambda c: abs(np.log(c / ref)))
            adjusted.append(best)
            ref = best
        g2 = grp.copy()
        g2["shares"] = adjusted
        parts.append(g2)
    return pd.concat(parts, ignore_index=True)


def _shares_panel(symbols, shares_pts: pd.DataFrame, date_index: pd.DatetimeIndex) -> pd.DataFrame:
    """Daily share-count panel aligned to `date_index`, interpolated from sparse reports.

    `shares_pts` are the tidy (symbol, period_end, shares) points from `_shares_points`.
    Linear (time) interpolation between report dates, forward-filled past the last
    report; days before a symbol's first report stay NaN (so it only enters once it
    has shares).
    """
    out = pd.DataFrame(index=date_index, columns=list(symbols), dtype=float)
    if shares_pts.empty:
        return out
    pts = shares_pts.sort_values(["symbol", "period_end"]).drop_duplicates(
        ["symbol", "period_end"], keep="last")
    wanted = set(symbols)
    for sym, grp in pts.groupby("symbol", sort=False):
        if sym not in wanted:
            continue
        s = pd.Series(grp["shares"].to_numpy(), index=pd.DatetimeIndex(grp["period_end"])).sort_index()
        s = s[~s.index.duplicated(keep="last")]
        union = s.index.union(date_index)
        daily = s.reindex(union).interpolate(method="time").ffill()
        out[sym] = daily.reindex(date_index)
    return out


# A float share below this is almost always mixed units, not a real float (BRK-B:
# Yahoo's floatShares is in Class A units, sharesOutstanding in Class B -> 0.09%).
_IWF_SANITY_FLOOR = 0.05


def _iwf(quotes: pd.DataFrame, symbols, reported_shares: pd.Series | None = None) -> pd.Series:
    """Investable weight factor = floatShares / sharesOutstanding, clipped to (0, 1].

    When that ratio is below `_IWF_SANITY_FLOOR` the two counts are probably in
    different units, so the other share counts we have — Yahoo's
    impliedSharesOutstanding, then the latest reported count (`reported_shares`) — are
    tried as the denominator, and the first giving a sane ratio wins. Berkshire's
    float comes out at 86% that way instead of 0.09%.
    """
    symbols = list(symbols)
    if quotes.empty:
        return pd.Series(1.0, index=symbols)

    def col(name: str) -> pd.Series:
        s = quotes[name] if name in quotes.columns else pd.Series(np.nan, index=quotes.index)
        return pd.to_numeric(s, errors="coerce").reindex(symbols)

    fl = col("floatShares")
    denominators = [col("sharesOutstanding"), col("impliedSharesOutstanding")]
    if reported_shares is not None:
        denominators.append(pd.to_numeric(reported_shares, errors="coerce").reindex(symbols))
    with np.errstate(divide="ignore", invalid="ignore"):
        ratios = [(fl / d).where((d > 0) & (fl > 0)) for d in denominators]
    iwf = ratios[0]
    for alt in ratios[1:]:
        bad = ~(iwf >= _IWF_SANITY_FLOOR)
        iwf = iwf.where(~bad | ~(alt >= _IWF_SANITY_FLOOR), alt)
    return iwf.clip(lower=0.0, upper=1.0).fillna(1.0)


def _calibrate_share_units(shares: pd.DataFrame, cap_prices: pd.DataFrame,
                           quotes: pd.DataFrame) -> pd.DataFrame:
    """Rescale a constituent's whole share history when its units are wrong.

    Compares today's market cap from our data (latest close x latest share count)
    with Yahoo's quoted `marketCap`, which covers every share class. Off by more than
    `settings.INDEX_SHARE_UNIT_TOLERANCE` either way means the count is in other units
    than the price — Class A shares on a Class B ticker (BRK-B, ~1,500x too small) or
    home-market shares on an ADR (far too big) — so the column is multiplied by the
    measured factor. That fixes the units and keeps the history's shape.
    """
    tol = settings.INDEX_SHARE_UNIT_TOLERANCE
    if tol <= 0 or quotes.empty or "marketCap" not in quotes.columns or shares.empty:
        return shares
    ours = cap_prices.ffill().iloc[-1] * shares.ffill().iloc[-1]
    theirs = pd.to_numeric(quotes["marketCap"], errors="coerce").reindex(shares.columns)
    with np.errstate(divide="ignore", invalid="ignore"):
        factor = theirs / ours
    fix = factor[(factor > tol) | (factor < 1.0 / tol)].dropna()
    fix = fix[np.isfinite(fix) & (fix > 0)]
    if fix.empty:
        return shares
    out = shares.copy()
    out[fix.index] = out[fix.index] * fix
    log.info("Sector index — rescaled share units for %d constituents (our market cap "
             "today off Yahoo's by >%gx; e.g. %s)", len(fix), tol,
             ", ".join(f"{s} x{fix[s]:.3g}" for s in fix.abs().sort_values().index[-5:]))
    return out


def _liquid_symbols(symbols, ohlcv_by: dict) -> list[str]:
    """Keep only names whose trailing avg daily dollar volume clears the floor.

    Dollar volume = adj_close * volume, averaged over the last
    `settings.INDEX_LIQUIDITY_WINDOW_DAYS` bars. Directly removes illiquid/penny
    names (and zero-volume fund NAV series) that would otherwise distort a group.
    """
    floor = settings.INDEX_MIN_AVG_DOLLAR_VOLUME
    win = settings.INDEX_LIQUIDITY_WINDOW_DAYS
    if floor <= 0:
        return list(symbols)
    out = []
    for sym in symbols:
        g = ohlcv_by.get(sym)
        if g is None or g.empty or "volume" not in g.columns:
            continue
        tail = g.tail(win)
        dv = (pd.to_numeric(tail["adj_close"], errors="coerce")
              * pd.to_numeric(tail["volume"], errors="coerce"))
        if dv.notna().any() and float(dv.mean(skipna=True)) >= floor:
            out.append(sym)
    return out


def _dedupe_share_classes(symbols, shares_pts: pd.DataFrame, quotes: pd.DataFrame,
                           ohlcv_by: dict) -> list[str]:
    """Collapse multiple listed classes of the same company to one constituent.

    Both yfinance and EDGAR report a single consolidated share count per company
    (CIK), not a per-class split — so a dual/multi-class company (BRK-A/BRK-B,
    GOOG/GOOGL, FOX/FOXA, ...) gets the SAME share count on every one of its
    tickers. Left alone, `compute_index()` would sum float market cap per
    constituent and count that company's weight once per listed class. This
    detects the fingerprint (two symbols in the same sector reporting an
    identical latest share count) and keeps only the most liquid ticker of each
    such group, so the company enters the index's weighting exactly once.
    """
    symbols = list(symbols)
    if shares_pts.empty:
        return symbols
    latest = (shares_pts[shares_pts["symbol"].isin(set(symbols))]
              .sort_values("period_end")
              .groupby("symbol")["shares"].last())
    if latest.empty:
        return symbols
    sec_col = quotes["sector"] if "sector" in quotes.columns else pd.Series(dtype=object)
    win = settings.INDEX_LIQUIDITY_WINDOW_DAYS

    def dollar_volume(sym: str) -> float:
        g = ohlcv_by.get(sym)
        if g is None or g.empty or "volume" not in g.columns:
            return 0.0
        tail = g.tail(win)
        dv = (pd.to_numeric(tail["adj_close"], errors="coerce")
              * pd.to_numeric(tail["volume"], errors="coerce"))
        return float(dv.mean(skipna=True)) if dv.notna().any() else 0.0

    drop: set[str] = set()
    for _, grp in latest.groupby([sec_col.reindex(latest.index), latest]):
        if len(grp) < 2:
            continue
        family = list(grp.index)
        best = max(family, key=dollar_volume)
        drop.update(s for s in family if s != best)
    if drop:
        log.info("Sector index — collapsing %d duplicate-share-class symbols "
                  "(same company, multiple listed classes): %s", len(drop), sorted(drop))
    return [s for s in symbols if s not in drop]


def _groups(quotes: pd.DataFrame, symbols) -> tuple[dict, dict]:
    """Group symbols by sector and by 'sector | industry' from the Yahoo tags."""
    sector_groups: dict[str, list[str]] = {}
    industry_groups: dict[str, list[str]] = {}
    sec_col = quotes["sector"] if "sector" in quotes.columns else pd.Series(dtype=object)
    ind_col = quotes["industry"] if "industry" in quotes.columns else pd.Series(dtype=object)
    for sym in symbols:
        sec = sec_col.get(sym)
        ind = ind_col.get(sym)
        if isinstance(sec, str) and sec:
            sector_groups.setdefault(sec, []).append(sym)
            if isinstance(ind, str) and ind:
                industry_groups.setdefault(f"{sec} | {ind}", []).append(sym)
    min_members = settings.INDEX_MIN_INDUSTRY_MEMBERS
    industry_groups = {k: v for k, v in industry_groups.items() if len(v) >= min_members}
    return sector_groups, industry_groups


def _to_long(kind: str, groups: dict, prices, shares, iwf, cap_prices=None,
             eligible=None) -> pd.DataFrame:
    """Compute each group's series and stack into tidy (kind, label, date, level) rows."""
    frames = []
    for label, names in groups.items():
        lvl = compute_index(names, prices, shares, iwf, cap=True,
                            cap_prices=cap_prices, eligible=eligible)
        if len(lvl):
            frames.append(pd.DataFrame({
                "kind": kind,
                "label": label,
                "date": lvl.index.strftime("%Y-%m-%d"),
                "level": lvl.to_numpy(),
            }))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["kind", "label", "date", "level"])


# ==================================================================================
# ENTRY POINT — called by pipeline.run_analysis on full runs.
# ==================================================================================

def build_and_write(universe_symbols, quotes: pd.DataFrame, financials: pd.DataFrame,
                    ohlcv_by: dict, prices_as_of: str | None) -> dict:
    """Build sector + sub-industry indices over the (filtered) universe and write indices.db.

    `quotes` is indexed by symbol; `financials` is the full analysis frame; `ohlcv_by`
    maps symbol -> its date-sorted OHLCV slice (the bounded analysis window, used ONLY
    for the recent liquidity filter — prices come from a dedicated deep read). Returns a
    short summary dict. Writes the tidy `sector_industry_index` table + an `index_meta`
    row, replacing both (clean-slate, like analysis.db).
    """
    field = settings.INDEX_FIELD
    shares_pts = _shares_points(financials)
    # Universe filter: sector-tagged active+validated names that clear the liquidity floor,
    # then collapse multi-class companies (BRK-A/BRK-B, GOOG/GOOGL, ...) to one constituent
    # each — see _dedupe_share_classes for why that guard is needed.
    liquid = _liquid_symbols(universe_symbols, ohlcv_by)
    liquid = _dedupe_share_classes(liquid, shares_pts, quotes, ohlcv_by)
    sector_groups, industry_groups = _groups(quotes, liquid)
    members = sorted({s for v in sector_groups.values() for s in v})
    if not members:
        log.warning("Sector index — no eligible constituents after filtering; skipping")
        return {"sectors": 0, "industries": 0, "constituents": 0}

    # Data-driven start (from share coverage) + dedicated memory-efficient deep read.
    start = _start_date(members, shares_pts)
    prices, cap_prices, dollar_volume = _deep_panels(members, start, field)
    if prices.empty:
        log.warning("Sector index — empty price panel; skipping")
        return {"sectors": 0, "industries": 0, "constituents": 0}
    adj_pts = _split_adjust_shares(shares_pts[shares_pts["symbol"].isin(set(members))],
                                   _split_events(members))
    shares = _shares_panel(members, adj_pts, prices.index)
    shares = _calibrate_share_units(shares, cap_prices, quotes)
    reported = shares_pts.sort_values("period_end").groupby("symbol")["shares"].last()
    iwf = _iwf(quotes, members, reported_shares=reported)
    eligible = _point_in_time_eligible(dollar_volume, cap_prices, shares, iwf)
    del dollar_volume

    log.info("Sector index — %d constituents, %d sectors, %d industries (%s, $-vol floor "
             "%.0fM, history from %s @ %d+ reporters)",
             len(members), len(sector_groups), len(industry_groups), field,
             settings.INDEX_MIN_AVG_DOLLAR_VOLUME / 1e6,
             start.date() if start is not None else "n/a", settings.INDEX_START_MIN_REPORTERS)

    sec_long = _to_long("sector", sector_groups, prices, shares, iwf, cap_prices, eligible)
    ind_long = _to_long("industry", industry_groups, prices, shares, iwf, cap_prices, eligible)
    long_df = pd.concat([sec_long, ind_long], ignore_index=True)

    n_sectors = sec_long["label"].nunique() if not sec_long.empty else 0
    n_industries = ind_long["label"].nunique() if not ind_long.empty else 0
    date_span = (long_df["date"].min(), long_df["date"].max()) if not long_df.empty else (None, None)
    meta = pd.DataFrame([{
        "built_at": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"),
        "prices_as_of": prices_as_of,
        "start_date": date_span[0],
        "end_date": date_span[1],
        "field": field,
        "n_sectors": int(n_sectors),
        "n_industries": int(n_industries),
        "n_constituents": len(members),
    }])
    with Database(settings.INDICES_DB) as db:
        db.replace(TABLE, long_df)
        db.replace(META_TABLE, meta)
    log.info("indices.db — %d sector + %d industry series, %d rows (%s..%s)",
             n_sectors, n_industries, len(long_df), date_span[0], date_span[1])
    return {"sectors": n_sectors, "industries": n_industries, "constituents": len(members),
            "rows": len(long_df)}
