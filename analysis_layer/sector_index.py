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
  P            adj_close (total return)
  Shares       shares outstanding interpolated daily from financials reports
  IWF          floatShares / sharesOutstanding (snapshot, from quotes)
  AWF          capping factor set each quarterly rebalance (1.0 if uncapped)
Weights are float-MC each quarter (third-Friday effective date), capped with
`cap_weights_new`, then held fixed between rebalances. Each series is rebased to
100 at its own effective start, so levels are *growth since start*, not absolute.

UNIVERSE / DISTORTION GUARD
  The raw universe includes illiquid/penny names that can dominate (and warp) a
  group's index. We feed in only active+validated symbols that also clear a
  liquidity floor (avg daily dollar volume, `settings.INDEX_MIN_AVG_DOLLAR_VOLUME`)
  plus a `min_active` breadth guard so no single early name defines the base.

DATE RANGE (decoupled from the analysis OHLCV window — 2026-06-14)
  The index history is NOT tied to `ANALYSIS_OHLCV_LOOKBACK_DAYS` (that ~2yr bound
  exists only to cap the full-table read the per-symbol metrics need). Instead the
  start is DATA-DRIVEN: the earliest date by which at least
  `settings.INDEX_START_MIN_REPORTERS` constituents have a share report in
  financials.db (a breadth guard so one deep-history outlier can't drag the read
  back decades). Prices are then read with a dedicated, memory-efficient reader
  (`_deep_price_panel`): only `adj_close`, only the liquid constituents (~4-5k, not
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
# CORE ENGINE — one group of constituents -> a base-100 level series.  (verbatim)
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
) -> pd.Series:
    """Float-adjusted, capped, quarterly-rebalanced base-100 level series for one group."""
    cols = [c for c in constituents if c in prices.columns]
    if not cols:
        return pd.Series(dtype=float)

    px = prices[cols]
    shr = shares[cols]
    w_iwf = iwf.reindex(cols).fillna(1.0)
    if start is not None:
        px, shr = px[px.index >= pd.Timestamp(start)], shr[shr.index >= pd.Timestamp(start)]
    if end is not None:
        px, shr = px[px.index <= pd.Timestamp(end)], shr[shr.index <= pd.Timestamp(end)]

    # Forward-fill the odd missing price so a single NaN day doesn't drop a constituent.
    px_ff = px.ffill()
    active = px_ff.notna() & shr.notna() & (px_ff > 0)

    # Float-adjusted market cap, used purely to set the weights at each rebalance.
    fmc = px_ff.mul(shr).mul(w_iwf, axis=1)

    # Effective start = first day with at least `min_active` active constituents.
    n_active = active.sum(axis=1)
    eligible = n_active[n_active >= min(min_active, max(1, len(cols)))]
    if eligible.empty:
        return pd.Series(dtype=float)
    eff_start = eligible.index[0]
    px_ff, fmc, active = (d[d.index >= eff_start] for d in (px_ff, fmc, active))

    reb_dates = quarterly_rebalance_dates(px_ff.index)

    level = pd.Series(index=px_ff.index, dtype=float)
    cur_level = BASE_LEVEL
    holdings = None       # share-equivalent quantities; portfolio value = sum(holdings * price)
    prev_value = None

    for day in px_ff.index:
        # (a) Apply today's market move using the holdings fixed at the last rebalance.
        if holdings is not None:
            value = float((holdings * px_ff.loc[day, holdings.index]).sum())
            cur_level *= value / prev_value
            prev_value = value
        # (b) On a rebalance day (and day one) recompute capped weights; level stays continuous.
        if day in reb_dates or holdings is None:
            day_fmc = fmc.loc[day]
            names = day_fmc.index[active.loc[day] & day_fmc.notna() & (day_fmc > 0)]
            if len(names):
                w = day_fmc[names] / day_fmc[names].sum()   # raw float-adjusted weights
                if cap:
                    w = cap_weights_new(w)                  # current Select Sector capping
                p = px_ff.loc[day, names]
                holdings = w / p                            # quantities reproducing those weights
                prev_value = float((holdings * p).sum())
        level.loc[day] = cur_level

    return level.dropna()


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


def _deep_price_panel(symbols, start, field: str) -> pd.DataFrame:
    """Memory-efficient wide price panel from ohlcv.db, read back to `start`.

    Reads only `field` for the given (liquid) symbols, in IN(...) chunks, assembling
    one column per symbol directly — so the peak footprint is ~one chunk's frame plus
    the (narrow) growing panel, never the full OHLCV table.
    """
    if field not in ("adj_close", "close"):  # guard: only known price columns
        field = "adj_close"
    start_s = pd.Timestamp(start).strftime("%Y-%m-%d") if start is not None else "0001-01-01"
    cols: dict = {}
    with Database(settings.OHLCV_DB) as db:
        for chunk in _chunked(symbols):
            ph = ",".join("?" * len(chunk))
            df = db.query(
                f"SELECT symbol, date, {field} AS px FROM ohlcv "
                f"WHERE date >= ? AND symbol IN ({ph})",
                [start_s, *chunk],
            )
            if df.empty:
                continue
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df["px"] = pd.to_numeric(df["px"], errors="coerce")
            df = df.dropna(subset=["date"])
            for sym, g in df.groupby("symbol", sort=False):
                s = pd.Series(g["px"].to_numpy(), index=pd.DatetimeIndex(g["date"]))
                cols[sym] = s[~s.index.duplicated(keep="last")].sort_index()
            del df
    if not cols:
        return pd.DataFrame()
    return pd.DataFrame(cols).sort_index()


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


def _iwf(quotes: pd.DataFrame, symbols) -> pd.Series:
    """Investable weight factor = floatShares / sharesOutstanding, clipped to (0, 1]."""
    if quotes.empty:
        return pd.Series(1.0, index=list(symbols))
    so = pd.to_numeric(quotes.get("sharesOutstanding"), errors="coerce")
    fl = pd.to_numeric(quotes.get("floatShares"), errors="coerce")
    iwf = fl / so
    iwf = iwf.where((so > 0) & (fl > 0), 1.0)
    return iwf.clip(lower=0.0, upper=1.0).fillna(1.0)


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


def _to_long(kind: str, groups: dict, prices, shares, iwf) -> pd.DataFrame:
    """Compute each group's series and stack into tidy (kind, label, date, level) rows."""
    frames = []
    for label, names in groups.items():
        lvl = compute_index(names, prices, shares, iwf, cap=True)
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
    # Universe filter: sector-tagged active+validated names that clear the liquidity floor.
    liquid = _liquid_symbols(universe_symbols, ohlcv_by)
    sector_groups, industry_groups = _groups(quotes, liquid)
    members = sorted({s for v in sector_groups.values() for s in v})
    if not members:
        log.warning("Sector index — no eligible constituents after filtering; skipping")
        return {"sectors": 0, "industries": 0, "constituents": 0}

    # Data-driven start (from share coverage) + dedicated memory-efficient deep read.
    shares_pts = _shares_points(financials)
    start = _start_date(members, shares_pts)
    prices = _deep_price_panel(members, start, field)
    if prices.empty:
        log.warning("Sector index — empty price panel; skipping")
        return {"sectors": 0, "industries": 0, "constituents": 0}
    shares = _shares_panel(members, shares_pts, prices.index)
    iwf = _iwf(quotes, members)

    log.info("Sector index — %d constituents, %d sectors, %d industries (%s, $-vol floor "
             "%.0fM, history from %s @ %d+ reporters)",
             len(members), len(sector_groups), len(industry_groups), field,
             settings.INDEX_MIN_AVG_DOLLAR_VOLUME / 1e6,
             start.date() if start is not None else "n/a", settings.INDEX_START_MIN_REPORTERS)

    sec_long = _to_long("sector", sector_groups, prices, shares, iwf)
    ind_long = _to_long("industry", industry_groups, prices, shares, iwf)
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
