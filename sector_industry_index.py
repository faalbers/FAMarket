"""Build sector and sub-industry indices for the full symbol universe.

This is a SELF-CONTAINED, drop-in script: it reads directly from the three SQLite databases
(`ohlcv.db`, `quotes.db`, `financials.db`) and reconstructs, for every Yahoo *sector* and every
Yahoo *industry* (sub-industry), a daily index level series using the SPDR Select Sector index
formula with the CURRENT ("new", post-2024-09-23) capping rules only.

It does not depend on the other modules in this repo, so it can be copied into another project
that shares the same database files. Results are returned in memory as pandas DataFrames (one
column per group); nothing is written to disk.

==================================================================================================
INTEGRATION INSTRUCTIONS FOR CLAUDE  (read this first when asked to "implement this script here")
==================================================================================================
GOAL: expose sector and sub-industry index time series (base-100 daily levels) computed with the
formula below, to the host project, using the host project's existing database files.

PREREQUISITES (verify, don't assume):
  * Python 3.9+ with `pandas` and `numpy` installed. If missing, install them.
  * The three SQLite DBs exist with these EXACT names and the columns this script reads:
      - ohlcv.db      -> table `ohlcv`      : symbol, date, adj_close (and `close`)
      - quotes.db     -> table `quotes`     : symbol, sector, industry, sharesOutstanding, floatShares
      - financials.db -> table `financials` : symbol, period_end, ordinary_shares_number, share_issued
    If the host project's schema differs, adapt ONLY the loader queries (load_prices / load_meta /
    load_shares_daily) to map to its column names — do NOT change the formula or capping logic.

STEPS TO IMPLEMENT:
  1. Copy this file into the host project (anywhere on its import path).
  2. Point it at the host project's databases. Either set the env var PEERINDICES_DB_DIR to the
     folder containing the .db files, or edit the DB_DIR constant below. (Default: a `databases/`
     folder next to this file.)
  3. Call the entry point:
         from sector_industry_index import build_sector_and_industry_indices
         sector_df, industry_df = build_sector_and_industry_indices()
     - `sector_df`   : DataFrame, index = trading dates, columns = Yahoo sector names.
     - `industry_df` : DataFrame, index = trading dates, columns = "Sector | Industry" labels.
     - Each column is a base-100 level series (see normalization caveat below).
  4. Consume the DataFrames wherever the host project needs them (API response, table, chart,
     persisted table, etc.). This script intentionally does NOT persist anything; if the host
     project wants them stored, write the DataFrames to its own store after calling.

THINGS TO DECIDE WITH THE USER / ADAPT FOR PRODUCTION (do not silently assume):
  * UNIVERSE FILTERING — `load_universe()` returns the raw ~8k-symbol universe, which includes
    illiquid/penny names that distort some groups (see caveat #1). For production, filter to a
    sensible universe (min market cap / liquidity / an index membership list) and feed it in.
  * `field`  : 'adj_close' (total return, default) vs 'close' (price return).
  * `start`  : defaults to 2022-01-01 (where share data is broad); adjust if needed.
  * `cap`    : leave True to apply the current Select Sector capping (this is the requested behavior).

DO NOT CHANGE: the formula (FMC weighting), the current-capping algorithm (`cap_weights_new`),
the quarterly rebalance schedule, or the divisor/normalization mechanics — those ARE the spec.

--------------------------------------------------------------------------------------------------
THE FORMULA
--------------------------------------------------------------------------------------------------
For a group of constituents (a sector or an industry), the index level on trading day t is:

    Index_t = ( sum_i  P_{i,t} * Shares_{i,t} * IWF_i * AWF_i ) / Divisor

  P_{i,t}      price on day t                      (adj_close = total return)
  Shares_{i,t} shares outstanding on day t          (interpolated daily from financials reports)
  IWF_i        investable weight factor             (floatShares / sharesOutstanding, snapshot)
  AWF_i        capping factor set each rebalance     (1.0 if the name is not capped)
  Divisor      keeps the series continuous + base 100

Float-adjusted market cap  FMC_{i,t} = P_{i,t} * Shares_{i,t} * IWF_i  — i.e. market cap is taken
on each day's price multiplied by that day's share count, never a single snapshot cap.

Constituents are reweighted by FMC each quarter (third-Friday effective date) and then capped with
the current Select Sector rules (see `cap_weights_new`). Between rebalances the weights are held
fixed, so the index level simply tracks the constituents' price moves.

--------------------------------------------------------------------------------------------------
TWO IMPORTANT CAVEATS THE HOST PROJECT MUST UNDERSTAND
--------------------------------------------------------------------------------------------------
1. NAME DISTORTION FROM ILLIQUID / MICRO-CAP CONSTITUENTS.
   This runs over the FULL Yahoo-tagged universe (~8,000 symbols), which includes many tiny,
   illiquid, and penny stocks. A single such name can distort — or completely dominate — its
   group's index. Examples seen in practice: the "Basic Materials | Gold" sub-industry (a micro-cap
   miner exploding thousands of percent) and the "Communication Services" sector (a penny stock
   with a huge share count getting real float-MC weight). Float-MC weighting suppresses *small*
   names, but a penny stock with billions of shares can still carry meaningful weight, and a 10-50x
   move then warps the level (you will see absurd index values like tens of thousands).
   => The host project should FILTER THE UNIVERSE before building indices — e.g. by a minimum
      market cap, minimum average dollar volume / liquidity, or restricting to an index membership
      list. Pass the filtered symbol list in rather than relying on the raw `load_universe()`.

2. NORMALIZATION AT THE START DATE (levels are relative, not absolute).
   Every index series is rebased to 100 at its own start date (see `BASE_LEVEL` and the effective-
   start logic in `compute_index`). The start defaults to 2022-01-01 because share-count data only
   has broad coverage from ~2022 (earliest report 2021-06); before then too few names report shares
   to weight a group. Consequences:
     - Index LEVELS are only meaningful as *growth since the common start date*, not as absolute
       values, and two series are only comparable over their overlapping dates.
     - A `min_active` breadth guard delays a group's start until enough constituents are active, so
       a single early-reporting stock cannot define the base. Groups can still start later than the
       requested `start` if their members report shares later.
"""

from __future__ import annotations

import os
import sqlite3

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------------------------------
# Configuration — DB file names are kept identical to the host project on purpose.
# --------------------------------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
# DB location: set the PEERINDICES_DB_DIR env var to the folder holding the .db files, or edit the
# fallback below. The DB FILE NAMES are fixed on purpose to match the host project.
DB_DIR = os.environ.get("PEERINDICES_DB_DIR", os.path.join(HERE, "databases"))
OHLCV_DB = os.path.join(DB_DIR, "ohlcv.db")        # daily OHLCV per symbol
QUOTES_DB = os.path.join(DB_DIR, "quotes.db")      # snapshot: sector/industry tags + float/shares
FINANCIALS_DB = os.path.join(DB_DIR, "financials.db")  # quarterly/annual share counts

BASE_LEVEL = 100.0  # every index series starts at 100

# Current ("new", effective 2024-09-23) Select Sector capping parameters.
SINGLE_CAP = 0.24       # no single constituent may exceed 24% of the index ...
SINGLE_CAP_TO = 0.23    # ... and any that does is reduced to 23%
GROUP_THRESHOLD = 0.048  # constituents weighing > 4.8% form the "concentrated" group ...
GROUP_CAP = 0.50        # ... whose combined weight may not exceed 50% ...
GROUP_CAP_TO = 0.45     # ... and is scaled down to 45% when it does
SMALL_CAP = 0.045       # all remaining (tail) names are capped at 4.5%


# ==================================================================================================
# 1. DATA LOADERS — pull only what the formula needs for the requested symbol universe.
# ==================================================================================================

def _read_sql(db_path: str, query: str, params: tuple = ()) -> pd.DataFrame:
    """Run a read-only query against a SQLite file and return a DataFrame."""
    con = sqlite3.connect(db_path)
    try:
        return pd.read_sql_query(query, con, params=params)
    finally:
        con.close()


def _chunked(seq, n=900):
    """Yield slices of <=900 items so we stay under SQLite's parameter limit for IN (...) lists."""
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def load_universe() -> list[str]:
    """Return every symbol in quotes.db that carries a (Yahoo) sector tag — the raw index universe.

    NOTE (name distortion): this is the UNFILTERED universe and includes tiny/illiquid/penny stocks
    that can distort a group's index (see caveat #1 in the module docstring). For production you
    will likely want to filter this list (min market cap / liquidity / membership) and pass the
    result into `build_sector_and_industry_indices` via custom group construction.
    """
    df = _read_sql(
        QUOTES_DB,
        "SELECT DISTINCT symbol FROM quotes WHERE sector IS NOT NULL AND sector != ''",
    )
    return df["symbol"].tolist()


def load_meta(symbols) -> pd.DataFrame:
    """Per-symbol classification + float factor from the quotes.db snapshot.

    Returns a DataFrame indexed by symbol with columns: sector, industry, iwf.
    IWF = floatShares / sharesOutstanding, clipped to (0, 1]; defaults to 1.0 when float data is
    missing. (Yahoo's floatShares is occasionally bad, but float adjustment barely moves a
    cap-weighted index, so this is safe.)
    """
    frames = []
    for chunk in _chunked(symbols):
        ph = ",".join("?" * len(chunk))
        frames.append(_read_sql(
            QUOTES_DB,
            f"""SELECT symbol, sector, industry, sharesOutstanding, floatShares
                FROM quotes WHERE symbol IN ({ph})""",
            tuple(chunk),
        ))
    meta = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    meta = meta.drop_duplicates("symbol").set_index("symbol")
    iwf = meta["floatShares"] / meta["sharesOutstanding"]
    # Use IWF=1 where the float ratio is unavailable or non-positive.
    iwf = iwf.where((meta["sharesOutstanding"] > 0) & (meta["floatShares"] > 0), 1.0)
    meta["iwf"] = iwf.clip(lower=0.0, upper=1.0).fillna(1.0)
    return meta


def load_prices(symbols, field: str = "adj_close") -> pd.DataFrame:
    """Daily price panel from ohlcv.db: rows = dates, columns = symbols.

    field='adj_close' gives a total-return series (dividends + splits); 'close' gives price return.
    """
    frames = []
    for chunk in _chunked(symbols):
        ph = ",".join("?" * len(chunk))
        frames.append(_read_sql(
            OHLCV_DB,
            f"SELECT symbol, date, {field} AS px FROM ohlcv WHERE symbol IN ({ph})",
            tuple(chunk),
        ))
    raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["symbol", "date", "px"])
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
    raw["px"] = pd.to_numeric(raw["px"], errors="coerce")  # guard against stray non-numeric values
    raw = raw.dropna(subset=["date"]).drop_duplicates(["symbol", "date"], keep="last")
    return raw.pivot(index="date", columns="symbol", values="px").sort_index()


def load_shares_daily(symbols, date_index: pd.DatetimeIndex) -> pd.DataFrame:
    """Daily share-count panel aligned to `date_index`, interpolated from sparse reports.

    financials.db only reports shares quarterly/annually, so we linearly interpolate between report
    dates to get a value for every trading day. The tail (after the last report) is carried forward;
    the front (before the first report) is left NaN, so a stock only enters an index once it has
    share data. This daily interpolation is the ONLY gap-filling in the pipeline.
    """
    # Pull the sparse report points: prefer ordinary_shares_number, fall back to share_issued.
    frames = []
    for chunk in _chunked(symbols):
        ph = ",".join("?" * len(chunk))
        frames.append(_read_sql(
            FINANCIALS_DB,
            f"""SELECT symbol, period_end,
                       COALESCE(ordinary_shares_number, share_issued) AS shares
                FROM financials
                WHERE symbol IN ({ph})
                  AND COALESCE(ordinary_shares_number, share_issued) IS NOT NULL""",
            tuple(chunk),
        ))
    pts = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["symbol", "period_end", "shares"])
    pts["period_end"] = pd.to_datetime(pts["period_end"], errors="coerce")
    pts = pts.dropna(subset=["period_end"])
    pts = pts[pts["shares"] > 0]
    # Both quarterly and annual rows can share a period_end; keep one value per (symbol, date).
    pts = pts.sort_values(["symbol", "period_end"]).drop_duplicates(["symbol", "period_end"], keep="last")

    out = pd.DataFrame(index=date_index, columns=list(symbols), dtype=float)
    for sym, grp in pts.groupby("symbol"):
        s = pd.Series(grp["shares"].values, index=grp["period_end"].values).sort_index()
        s = s[~s.index.duplicated(keep="last")]
        # Interpolate on the union of report dates + trading days, then keep only trading days.
        union = s.index.union(date_index)
        daily = s.reindex(union).interpolate(method="time").ffill()
        out[sym] = daily.reindex(date_index)
    return out


# ==================================================================================================
# 2. CAPPING — the current ("new") Select Sector rule only.
# ==================================================================================================

def _distribute_to_tail(w: pd.Series, excess: float, cap: float = SMALL_CAP) -> pd.Series:
    """Water-fill `excess` weight onto names currently below `cap`, never pushing any above `cap`."""
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
    """Apply the current Select Sector capping to a weight vector (input/output sum to 1).

    Iterates the following until no diversification limit is breached:
      1. any single name > 24%            -> reduced to 23%
      2. if the >4.8% cohort sums to >50% -> scaled down so the cohort sums to 45%
      3. all freed-up "excess" weight     -> redistributed across names under 4.5%, none over 4.5%
    This is the rule (effective 2024-09-23) that de-concentrated funds like XLK.
    """
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


# ==================================================================================================
# 3. REBALANCE SCHEDULE — quarterly, third Friday of Mar/Jun/Sep/Dec.
# ==================================================================================================

def quarterly_rebalance_dates(index: pd.DatetimeIndex) -> set:
    """Trading days on/after the third Friday of each quarter-end month, plus the series start.

    The third-Friday effective date is mapped forward to the first trading day that exists in the
    price index. Weights are recomputed on these days and held fixed in between.
    """
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


# ==================================================================================================
# 4. CORE ENGINE — turn one group of constituents into a base-100 level series.
# ==================================================================================================

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
    """Compute the float-adjusted, capped, quarterly-rebalanced index level for one group.

    `prices`, `shares`, `iwf` are the pre-loaded panels (passed in so they can be shared across all
    groups rather than re-queried). `start`/`end` bound the date range. `min_active` is the minimum
    number of active constituents required before the index is allowed to start — this prevents a
    single early-reporting stock from defining (and distorting) the base-100 level during the thin
    pre-2022 period. Returns a base-100 pd.Series indexed by date, or an empty series if the group
    never has usable data.
    """
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

    # Forward-fill the odd missing price so a single NaN day doesn't drop a constituent. A name is
    # only "active" on a day once it has BOTH a price and a (interpolated) share count.
    px_ff = px.ffill()
    active = px_ff.notna() & shr.notna() & (px_ff > 0)

    # Float-adjusted market cap, used purely to set the weights at each rebalance.
    fmc = px_ff.mul(shr).mul(w_iwf, axis=1)

    # Effective start = first day with at least `min_active` active constituents (breadth guard).
    n_active = active.sum(axis=1)
    eligible = n_active[n_active >= min(min_active, max(1, len(cols)))]
    if eligible.empty:
        return pd.Series(dtype=float)
    eff_start = eligible.index[0]
    px_ff, fmc, active = (df[df.index >= eff_start] for df in (px_ff, fmc, active))

    reb_dates = quarterly_rebalance_dates(px_ff.index)

    # NORMALIZATION: the series is rebased to 100 (BASE_LEVEL) on `eff_start` and then chained
    # forward by daily portfolio returns. Levels therefore express growth since each group's own
    # start date — they are relative, not absolute, and comparable only over overlapping dates.
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
        # (b) On a rebalance day (and on day one) recompute capped weights; level stays continuous.
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


# ==================================================================================================
# 5. GROUP BUILDERS — sector indices and sub-industry indices over the whole universe.
# ==================================================================================================

# Share data only has broad coverage from ~2022 (earliest report is 2021-06), so we default the
# index start there; before that, too few stocks report shares to weight a group meaningfully.
DEFAULT_START = "2022-01-01"


def _build_group_indices(groups, prices, shares, iwf, cap=True, start=None, end=None) -> pd.DataFrame:
    """Compute an index for each {label: [symbols]} group and return them as DataFrame columns."""
    series = {}
    for label, names in groups.items():
        lvl = compute_index(names, prices, shares, iwf, cap=cap, start=start, end=end)
        if len(lvl):
            series[label] = lvl
    # Outer-join all series on date; columns are the group labels.
    return pd.DataFrame(series).sort_index() if series else pd.DataFrame()


def build_sector_and_industry_indices(
    field: str = "adj_close",
    cap: bool = True,
    min_industry_members: int = 1,
    start: str | None = DEFAULT_START,
    end: str | None = None,
):
    """Build every sector index and every sub-industry index for the full universe.

    Parameters
    ----------
    field                : 'adj_close' (total return, default) or 'close' (price return).
    cap                  : apply the current Select Sector capping (recommended; True).
    min_industry_members : skip industries with fewer than this many tagged symbols.
    start, end           : date bounds; start defaults to 2022-01-01 (broad share coverage).

    Returns
    -------
    (sector_df, industry_df) : two DataFrames indexed by date.
        sector_df   columns = Yahoo sector names.
        industry_df columns = "Sector | Industry" labels.
    """
    universe = load_universe()
    meta = load_meta(universe)

    # Load every panel ONCE for the whole universe, then slice per group inside compute_index.
    prices = load_prices(universe, field=field)
    shares = load_shares_daily(universe, prices.index)
    iwf = meta["iwf"]

    # Group symbols by sector and by (sector, industry) using the Yahoo tags.
    sector_groups: dict[str, list[str]] = {}
    industry_groups: dict[str, list[str]] = {}
    for sym in universe:
        sec = meta["sector"].get(sym)
        ind = meta["industry"].get(sym)
        if isinstance(sec, str) and sec:
            sector_groups.setdefault(sec, []).append(sym)
            if isinstance(ind, str) and ind:
                industry_groups.setdefault(f"{sec} | {ind}", []).append(sym)

    # Drop tiny industries if requested (they tend to be dominated by illiquid names).
    industry_groups = {k: v for k, v in industry_groups.items() if len(v) >= min_industry_members}

    sector_df = _build_group_indices(sector_groups, prices, shares, iwf, cap=cap, start=start, end=end)
    industry_df = _build_group_indices(industry_groups, prices, shares, iwf, cap=cap, start=start, end=end)
    return sector_df, industry_df


# ==================================================================================================
# 6. CLI — build the indices and print a short summary (results stay in memory).
# ==================================================================================================

if __name__ == "__main__":
    sectors, industries = build_sector_and_industry_indices()

    print(f"Sector indices:       {sectors.shape[1]} groups, "
          f"{sectors.index.min().date()}..{sectors.index.max().date()}")
    print(f"Sub-industry indices: {industries.shape[1]} groups")
    print("\nSector index latest levels (base 100 at each series start):")
    print(sectors.ffill().iloc[-1].round(1).sort_values(ascending=False).to_string())
