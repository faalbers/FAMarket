"""Relative strength vs the industry (fix list §21, Group H part 4).

A stock's total return minus its industry's benchmark ETF's return, over 1 and
3 years, in percentage points. `rs_rank` says a stock beat the market; this says
whether it beat its own industry (a gold miner in a gold rally is not special).

The benchmark map (`build_map`, full runs only, ~10 s) picks one ETF per Yahoo
industry and is stored as the `industry_benchmark` table in analysis.db; subset
runs reuse the stored map.

  * Yardstick: an equal-weighted basket of the industry's 10 largest listed
    companies of $2B and up (peer universe, clean prices), daily returns over 3
    years, each day's move capped at ±25% (one bad print can't steer it).
    Fewer than 3 such companies: topped up with the next-largest listed ones
    of $300M and up.
  * Candidates: active ETFs whose Morningstar category is a sector or theme
    (no style, size, leveraged or inverse funds), plus uncategorised ones whose
    name doesn't mark an option-income, leveraged or broad fund; $50M+ in
    assets, 3 years of prices.
  * Pick: the best daily-return correlation with the yardstick. It needs 0.70+
    and 0.05+ above the sector SPDR, else the sector SPDR is used, marked as a
    weak fit ("sector"). ETFs within 0.01 of the best count as a tie; the
    longest price history wins (SOXX over the newer SOXQ).

Returns come from `adj_close` at three dates read straight from ohlcv.db (the
analysis window is only 2 years). A return is NULL when either side lacks a
bar near both dates, a `level_shift` / `bad_adj` price flag overlaps the period
or sits in the row's `price_issues` (an unrecorded split or a broken adjusted
close), or the total return / price return ratio (the reinvested-dividend
factor) is below 0.98 or above a 15%-a-year yield (TOKCF: adj_close 0.18 next
to a close of 37 outside its flagged spans). `bad_print` and `stale` bars are
skipped when picking the end bars.

Residual momentum (beta-adjusted, optional in the fix list) is left out.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from analysis_layer.sector_index import _chunked, _deep_panels
from config import settings
from core.database import Database
from data_layer import price_checks

log = logging.getLogger(__name__)

TABLE = "industry_benchmark"
COLUMNS = ("rs_vs_industry_1y", "rs_vs_industry_3y", "industry_etf", "industry_etf_fit")

SECTOR_SPDR = {
    "Technology": "XLK", "Healthcare": "XLV", "Financial Services": "XLF",
    "Consumer Cyclical": "XLY", "Consumer Defensive": "XLP", "Energy": "XLE",
    "Industrials": "XLI", "Basic Materials": "XLB", "Real Estate": "XLRE",
    "Utilities": "XLU", "Communication Services": "XLC",
}
# Morningstar categories (quotes `fund_categoryName`, else `category`) of
# sector / theme funds. Style, size, leveraged and inverse categories are not here.
ETF_CATEGORIES = frozenset({
    "Technology", "Industrials", "Health", "Financial", "Equity Energy",
    "Real Estate", "Miscellaneous Sector", "Natural Resources", "Consumer Cyclical",
    "Infrastructure", "Equity Precious Metals", "Communications", "Consumer Defensive",
    "Utilities", "Energy Limited Partnership", "Global Real Estate",
})
# Yahoo leaves ~67 larger ETFs without a category (URA, COPX, SLX, TAN, LIT, ...).
# They are candidates too, unless the name marks a single-stock option-income,
# leveraged / inverse or broad fund (2026-11-21: 28 theme funds kept).
_UNCATEGORISED_EXCLUDE = (r"income|option|covered call|premium|yield|target|lever|inverse|"
                          r"short|bear|ultra|\b\d+x\b|flexible|large cap|core|titans|"
                          r"magnificent|dogs|berkshire|crypto")
ETF_MIN_ASSETS = 50e6
MEMBER_MIN_CAP = 2e9
TOPUP_MIN_CAP = 300e6
TOP_MEMBERS = 10
MIN_MEMBERS = 3
DAILY_CLIP = 0.25
MIN_CORR = 0.70
MIN_GAIN_OVER_SPDR = 0.05
TIE = 0.01
HISTORY_COVERAGE = 0.95
_NEAR_DAYS = 10            # an end bar may be up to this many calendar days early
_NULL_FLAGS = ("level_shift", "bad_adj")
_FACTOR_MIN = 0.98         # adj / close return ratio: rounding slack below 1
_MAX_YIELD = 0.15          # ... and at most a 15%-a-year dividend factor above it
_SKIP_FLAGS = ("bad_print", "stale")


# --------------------------------------------------------------------------- #
# Benchmark map
# --------------------------------------------------------------------------- #
def _members(df: pd.DataFrame) -> dict[str, list[str]]:
    """Yardstick members per industry, largest first."""
    co = df[df["company_type"].notna() & df["industry"].notna()]
    listed = co[(co["peer_universe"] == 1) & co["price_issues"].isna()
                & (pd.to_numeric(co["market_cap"], errors="coerce") >= TOPUP_MIN_CAP)]
    listed = listed.sort_values("market_cap", ascending=False)
    out: dict[str, list[str]] = {}
    for ind, g in listed.groupby("industry", sort=False):
        big = g[g["market_cap"] >= MEMBER_MIN_CAP]["symbol"].head(TOP_MEMBERS).tolist()
        if len(big) < MIN_MEMBERS:
            big = g["symbol"].head(MIN_MEMBERS).tolist()
        out[str(ind)] = big
    return out


def _candidates(df: pd.DataFrame, quotes: pd.DataFrame) -> list[str]:
    etfs = df.loc[df["security_type"] == "etf", "symbol"]
    q = quotes.reindex(etfs)
    cat = (q["fund_categoryName"] if "fund_categoryName" in q.columns else pd.Series(index=q.index, dtype=object))
    if "category" in q.columns:
        cat = cat.fillna(q["category"])
    assets = pd.to_numeric(q.get("totalAssets"), errors="coerce")
    name = (q["longName"] if "longName" in q.columns else pd.Series(index=q.index, dtype=object))
    themed_name = ~name.fillna("").str.contains(_UNCATEGORISED_EXCLUDE, case=False, regex=True)
    ok = (cat.isin(ETF_CATEGORIES) | (cat.isna() & themed_name)) & (assets >= ETF_MIN_ASSETS)
    return q.index[ok.fillna(False)].tolist()


def _sector_of(df: pd.DataFrame) -> dict[str, str]:
    co = df[df["company_type"].notna() & df["industry"].notna() & df["sector"].notna()]
    return co.groupby("industry")["sector"].agg(lambda s: s.mode().iloc[0]).to_dict()


def build_map(df: pd.DataFrame, quotes: pd.DataFrame, first_date_by: dict,
              prices_as_of: str | None) -> pd.DataFrame:
    """One benchmark ETF per industry (see the module docstring)."""
    end = pd.Timestamp(prices_as_of) if prices_as_of else pd.Timestamp.today().normalize()
    start = end - pd.DateOffset(years=3)
    members = _members(df)
    cands = _candidates(df, quotes)
    syms = sorted(set().union(*members.values(), cands, SECTOR_SPDR.values()))
    px, _, _ = _deep_panels(syms, start - pd.Timedelta(days=_NEAR_DAYS), "adj_close")
    px = px[(px.index >= start) & (px.index <= end)]
    r = px.pct_change(fill_method=None).iloc[1:]
    first = px.apply(pd.Series.first_valid_index)
    full = (first <= start + pd.Timedelta(days=_NEAR_DAYS)) & (r.notna().mean() > HISTORY_COVERAGE)
    cand_ok = [c for c in cands if c in r.columns and bool(full.get(c, False))]
    C = r[cand_ok]
    first_trade = {s: pd.Timestamp(first_date_by[s]) for s in cand_ok
                   if first_date_by.get(s) is not None and pd.notna(first_date_by.get(s))}

    rows = []
    for ind, sec in sorted(_sector_of(df).items()):
        spdr = SECTOR_SPDR.get(sec)
        mem = [m for m in members.get(ind, []) if m in r.columns]
        row = dict(sector=sec, industry=ind, benchmark=spdr, fit="sector",
                   benchmark_corr=np.nan, spdr=spdr, spdr_corr=np.nan,
                   runner_up=None, runner_up_corr=np.nan,
                   n_members=len(mem), members=",".join(mem))
        if len(mem) >= MIN_MEMBERS and not C.empty:
            yard = r[mem].clip(-DAILY_CLIP, DAILY_CLIP).mean(axis=1, skipna=True)
            sc = yard.corr(r[spdr]) if spdr in r.columns else np.nan
            row["spdr_corr"] = sc
            if spdr is not None:
                row["benchmark_corr"] = sc
            cs = C.corrwith(yard).dropna().sort_values(ascending=False)
            if len(cs):
                # near-ties: the longest price history wins
                tied = cs[cs >= cs.iloc[0] - TIE]
                best = min(tied.index, key=lambda s: (first_trade.get(s, end), -tied[s]))
                bc = float(cs[best])
                rest = cs.drop(best)
                if len(rest):
                    row["runner_up"], row["runner_up_corr"] = rest.index[0], float(rest.iloc[0])
                floor = MIN_CORR if pd.isna(sc) else max(MIN_CORR, sc + MIN_GAIN_OVER_SPDR)
                if bc >= floor:
                    row.update(benchmark=best, fit="industry", benchmark_corr=bc)
        rows.append(row)
    m = pd.DataFrame(rows)
    for c in ("benchmark_corr", "spdr_corr", "runner_up_corr"):
        m[c] = m[c].astype(float).round(3)
    m["prices_as_of"] = prices_as_of
    m["built_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    prev = read_map()
    if not prev.empty:
        old = prev.set_index("industry")["benchmark"]
        was = m["industry"].map(old)
        m["previous_benchmark"] = was.where(was.notna() & (was != m["benchmark"]))
    else:
        m["previous_benchmark"] = None
    return m


def read_map() -> pd.DataFrame:
    try:
        with Database(settings.ANALYSIS_DB) as db:
            return db.read(TABLE)
    except Exception:
        return pd.DataFrame()


def write_map(m: pd.DataFrame) -> None:
    """Store the map (after the analysis.db write). Never raises."""
    try:
        with Database(settings.ANALYSIS_DB) as db:
            db.replace(TABLE, m)
    except Exception:
        log.exception("Industry benchmark map write failed; analysis.db is unaffected")


# --------------------------------------------------------------------------- #
# Returns
# --------------------------------------------------------------------------- #
def _flags(symbols: list[str], start: pd.Timestamp) -> pd.DataFrame:
    """The price flags of `symbols` that reach past `start`, read from ohlcv.db.
    Read here, not taken from the pipeline: it loads only the flags of its 2-year
    window, and a 3-year return needs the year before that too (140 rows slipped
    through on 2026-11-21)."""
    cols = ["symbol", "check", "start_date", "end_date"]
    parts = []
    with Database(settings.OHLCV_DB) as db:
        if not db.table_exists(price_checks.TABLE):
            return pd.DataFrame(columns=cols)
        for chunk in _chunked(symbols):
            ph = ",".join("?" * len(chunk))
            q = db.query(f'SELECT symbol, "check", start_date, end_date FROM {price_checks.TABLE} '
                         f"WHERE end_date >= ? AND symbol IN ({ph})",
                         [start.strftime("%Y-%m-%d"), *chunk])
            if not q.empty:
                parts.append(q)
    if not parts:
        return pd.DataFrame(columns=cols)
    f = pd.concat(parts, ignore_index=True)[cols]
    f["start_date"] = pd.to_datetime(f["start_date"])
    f["end_date"] = pd.to_datetime(f["end_date"])
    return f


def _end_prices(symbols: list[str], targets: list[pd.Timestamp],
                flags: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """`adj_close` and `close` of each symbol's last usable bar at or up to
    `_NEAR_DAYS` before each target date -> two frames symbol x target (NaN when none)."""
    near = pd.Timedelta(days=_NEAR_DAYS)
    wins = [((t - near).strftime("%Y-%m-%d"), t.strftime("%Y-%m-%d")) for t in targets]
    parts = []
    with Database(settings.OHLCV_DB) as db:
        for chunk in _chunked(symbols):
            ph = ",".join("?" * len(chunk))
            for lo, hi in wins:  # one index range per window (an OR defeats it)
                q = db.query(f"SELECT symbol, date, adj_close, close FROM ohlcv "
                             f"WHERE symbol IN ({ph}) AND date BETWEEN ? AND ?",
                             [*chunk, lo, hi])
                if not q.empty:
                    parts.append(q)
    idx0 = pd.Index(symbols, name="symbol")
    adj = pd.DataFrame(np.nan, index=idx0, columns=range(len(targets)))
    close = adj.copy()
    if not parts:
        return adj, close
    bars = pd.concat(parts, ignore_index=True)
    bars["date"] = pd.to_datetime(bars["date"], errors="coerce")
    for c in ("adj_close", "close"):
        bars[c] = pd.to_numeric(bars[c], errors="coerce")
    bars = bars.dropna(subset=["date", "adj_close", "close"])
    bars = bars[(bars["adj_close"] > 0) & (bars["close"] > 0)]
    # drop bad-print / stale bars
    skip = flags[flags["check"].isin(_SKIP_FLAGS)]
    if not skip.empty:
        b = bars.reset_index().merge(skip, on="symbol")
        hit = b.loc[(b["date"] >= b["start_date"]) & (b["date"] <= b["end_date"]), "index"]
        bars = bars.drop(index=hit.unique())
    bars = bars.sort_values("date")
    for i, t in enumerate(targets):
        w = bars[(bars["date"] >= t - near) & (bars["date"] <= t)]
        last = w.groupby("symbol")[["adj_close", "close"]].last()
        hit = last.index.intersection(idx0)
        adj.loc[hit, i] = last.loc[hit, "adj_close"]
        close.loc[hit, i] = last.loc[hit, "close"]
    return adj, close


def _broken(flags: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> set:
    """Symbols with a level-shift / bad-adj flag overlapping [start, end]."""
    f = flags[flags["check"].isin(_NULL_FLAGS)]
    return set(f.loc[(f["end_date"] >= start) & (f["start_date"] <= end), "symbol"])


def compute(df: pd.DataFrame, bench: pd.DataFrame, prices_as_of: str | None,
            symbols: list[str] | None = None) -> pd.DataFrame:
    """Fill COLUMNS for the company rows in `symbols` (all when None); other rows
    keep what they have (subset runs keep the stored values)."""
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = np.nan if c.startswith("rs_") else None
    if bench.empty or not prices_as_of:
        return df
    end = pd.Timestamp(prices_as_of)
    targets = [end, end - pd.DateOffset(years=1), end - pd.DateOffset(years=3)]
    bmap = bench.set_index("industry")
    rows = df["company_type"].notna() & df["industry"].isin(bmap.index)
    if symbols is not None:
        rows &= df["symbol"].isin(symbols)
    sub = df.loc[rows, ["symbol", "industry"]]
    etf = sub["industry"].map(bmap["benchmark"])
    fit = sub["industry"].map(bmap["fit"])
    need = sorted(set(sub["symbol"]) | set(etf.dropna()))
    flags = _flags(need, targets[-1] - pd.Timedelta(days=_NEAR_DAYS))
    adj, close = _end_prices(need, targets, flags)
    issues = df.loc[sub.index, "price_issues"] if "price_issues" in df.columns else None
    row_bad = (issues.fillna("").str.contains("|".join(_NULL_FLAGS)).to_numpy()
               if issues is not None else np.zeros(len(sub), dtype=bool))

    def ret(syms: pd.Series, k: int, years: int) -> pd.Series:
        keys = syms.to_numpy()
        tr = adj[0].reindex(keys).to_numpy() / adj[k].reindex(keys).to_numpy()
        pr = close[0].reindex(keys).to_numpy() / close[k].reindex(keys).to_numpy()
        # total / price return = the reinvested-dividend factor: below 1 or above a
        # 15%-a-year yield means a broken adjusted close (TOKCF: adj_close 0.18 vs close 37)
        factor = tr / pr
        bad = ~((factor >= _FACTOR_MIN) & (factor <= (1 + _MAX_YIELD) ** years))
        broken = _broken(flags, targets[k], end)
        bad |= np.array([(not s) or s in broken for s in keys], dtype=bool)
        return pd.Series(tr - 1.0, index=syms.index).mask(bad)

    for k, years, col in ((1, 1, "rs_vs_industry_1y"), (2, 3, "rs_vs_industry_3y")):
        own = ret(sub["symbol"], k, years).mask(row_bad)
        ref = ret(etf.fillna(""), k, years)
        df.loc[sub.index, col] = ((own - ref) * 100).round(2)
    df.loc[sub.index, "industry_etf"] = etf
    df.loc[sub.index, "industry_etf_fit"] = fit
    return df


def run(df: pd.DataFrame, quotes: pd.DataFrame, first_date_by: dict,
        prices_as_of: str | None, subset: list[str] | None) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """Pipeline entry: full runs rebuild the map (returned for writing after the
    analysis.db write); subset runs reuse the stored one. Never raises."""
    try:
        if subset is None:
            bench = build_map(df, quotes, first_date_by, prices_as_of)
            fits = bench["fit"].value_counts().to_dict()
            moved = int(bench["previous_benchmark"].notna().sum())
            log.info("Industry benchmarks — %d industries: %s; %d changed since last build",
                     len(bench), fits, moved)
            new = bench
        else:
            bench, new = read_map(), None
        df = compute(df, bench, prices_as_of, subset)
        n = int(df["rs_vs_industry_1y"].notna().sum())
        log.info("Relative strength vs industry — %d companies with a 1-year value", n)
        return df, new
    except Exception:
        log.exception("Relative strength vs industry failed; columns left empty")
        for c in COLUMNS:
            if c not in df.columns:
                df[c] = np.nan if c.startswith("rs_") else None
        return df, None
