"""
Symbol discovery, normalization, and state management (Topics 3.1 & 3.2).

Pipeline order:
  1. Discover the US-investable universe — Polygon ticker list (primary) +
     SEC EDGAR company_tickers.json (gap-fill, source=edgar, is_validated=False).
  2. Resolve type first: Polygon `type` -> config.type_map; yfinance quoteType is
     the later fallback for unknowns (done during the yfinance fetch). The
     normalized security_type + sub_type live on every symbol.
  3. Normalize the Polygon symbol to yfinance/E*Trade format and store it as the
     canonical `symbol` (joined across every database), keeping the raw Polygon
     ticker in `polygon_symbol`.
  4. State management: new symbols enter is_active=True; in_polygon reflects
     presence in the latest Polygon run (dropped symbols are flagged, never
     deleted); existing is_active / is_validated flags are preserved across runs.
     The confirmed is_active=False flip happens in the end-of-run reassessment,
     after the data fetch can distinguish "no data" from "API error".

The canonical key everywhere downstream is `symbol` (the normalized form).
"""

from __future__ import annotations

import re

import pandas as pd
from ratelimit import limits, sleep_and_retry
from tenacity import retry, stop_after_attempt, wait_fixed

from config import secrets, settings, type_map
from core.database import Database
from core.logging_config import get_logger
from data_layer import cancel

log = get_logger("symbols")

TABLE = "symbols"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_FUND_TICKERS_URL = "https://www.sec.gov/files/company_tickers_mf.json"
POLYGON_TICKERS_URL = "https://api.polygon.io/v3/reference/tickers"


# --------------------------------------------------------------------------- #
# Symbol format normalization (Polygon -> yfinance / E*Trade)
# --------------------------------------------------------------------------- #
def normalize_symbol(polygon_symbol: str, poly_type: str = "", market: str = "") -> str:
    """Convert a Polygon ticker to the yfinance/E*Trade convention.

    Rules (Topic 3.2), applied in order:
      * index   (I: prefix / indices market)  ->  leading "I:" becomes "^"
      * warrant (type WARRANT)                 ->  trailing .WS[.A] -> -WT
      * unit    (type UNIT)                    ->  trailing .U / .WS.A -> -UN
      * preferred notation (BASEp[X])          ->  BASE-P[X]  (ABRpD -> ABR-PD)
      * class shares (a "." remains)           ->  every "." becomes "-"
    """
    sym = (polygon_symbol or "").strip()
    if not sym:
        return sym

    ptype = (poly_type or "").upper()
    mkt = (market or "").lower()

    # Indices: I:SPX -> ^SPX
    if sym.startswith("I:") or mkt == "indices":
        return "^" + (sym[2:] if sym.startswith("I:") else sym)

    # Warrants: ABC.WS / ABC.WS.A -> ABC-WT
    if ptype == "WARRANT":
        for suffix in (".WS.A", ".WS"):
            if sym.endswith(suffix):
                return sym[: -len(suffix)] + "-WT"

    # Units: ABC.U / ABC.WS.A -> ABC-UN
    if ptype == "UNIT":
        for suffix in (".WS.A", ".U"):
            if sym.endswith(suffix):
                return sym[: -len(suffix)] + "-UN"

    # Preferred shares: Polygon spells these BASEp / BASEpX (lowercase 'p' +
    # optional series letter); yfinance/E*Trade use BASE-P / BASE-PX (verified
    # live 2026-06-10: ABRpD -> ABR-PD, ETIp -> ETI-P). Polygon types most as
    # PFD but some as SP/ADRC, so match on the notation, not the type.
    m = re.fullmatch(r"([A-Z]+)p([A-Z]?)", sym)
    if m:
        return f"{m.group(1)}-P{m.group(2)}"

    # Class shares and any remaining dotted ticker: BRK.B -> BRK-B
    if "." in sym:
        return sym.replace(".", "-")

    return sym


# --------------------------------------------------------------------------- #
# Discovery sources
# --------------------------------------------------------------------------- #
def _polygon_page_getter():
    """Build a rate-limited, retrying single-page GET for the tickers endpoint.

    The Polygon SDK's ``list_tickers`` auto-paginates inside a generator, so the
    per-page ``?cursor=...`` requests never pass through a throttle — on the free
    tier (5 req/min) they fire back-to-back and trip a 429 wall. We paginate
    manually instead so EVERY page is one throttled call.

    Decorator order matters: ``@limits`` is innermost (one HTTP GET = one slot
    against ``RATE_LIMITS['polygon']``), ``@retry`` is outermost so a retried
    request re-acquires a throttle slot rather than firing unthrottled.
    """
    import requests  # lazy: EDGAR-only runs need no Polygon path

    calls, period = settings.RATE_LIMITS.get("polygon", (5, 60))

    @retry(
        stop=stop_after_attempt(settings.RETRY_MAX_ATTEMPTS),
        wait=wait_fixed(settings.RETRY_WAIT_SECONDS),
        reraise=True,
    )
    @sleep_and_retry
    @limits(calls=calls, period=period)
    def _get(url: str, params: dict) -> dict:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    return _get


def discover_polygon() -> pd.DataFrame:
    """Fetch the full active Polygon ticker universe.

    Returns one row per ticker with the canonical `symbol`, the raw
    `polygon_symbol`, `name`, resolved `security_type`, and source metadata.
    Requires POLYGON_API_KEY. Manual cursor pagination keeps every page request
    under the configured Polygon rate limit (see `_polygon_page_getter`).
    """
    api_key = secrets.require("POLYGON_API_KEY")
    get_page = _polygon_page_getter()
    rows: list[dict] = []

    # Pull every market segment Polygon exposes for the US universe.
    for market in ("stocks", "indices"):
        url = POLYGON_TICKERS_URL
        params = {
            "market": market,
            "active": "true",
            "limit": 1000,
            "order": "asc",
            "sort": "ticker",
            "apiKey": api_key,
        }
        page = 0
        while url:
            # Honour a Stop between pages — discovery is the slowest stage (~6 min
            # on Polygon's free tier). The partial result is discarded by
            # run_discovery's cancel check, so symbols.db is never left half-written.
            if cancel.is_cancelled():
                log.warning("Polygon discovery cancelled — %s page %d", market, page)
                return pd.DataFrame()
            data = get_page(url, params)
            for t in data.get("results", []):
                raw = t.get("ticker")
                poly_type = t.get("type", "") or ""
                # Polygon never types the indices market; we know they're indices.
                sec_type = type_map.normalize_type(poly_type) or (
                    "index" if market == "indices" else None
                )
                rows.append(
                    {
                        "symbol": normalize_symbol(raw, poly_type, market),
                        "polygon_symbol": raw,
                        "name": t.get("name"),
                        "security_type": sec_type,
                        "polygon_type": poly_type,
                        "market": market,
                        "primary_exchange": t.get("primary_exchange"),
                        "source": "polygon",
                        "in_polygon": True,
                    }
                )
            page += 1
            log.info("Polygon %s — page %d, %d tickers so far", market, page, len(rows))
            # next_url already carries cursor/market/limit; only apiKey must be re-added.
            url = data.get("next_url")
            params = {"apiKey": api_key} if url else {}
        log.info("Polygon %s — done, %d pages", market, page)

    df = pd.DataFrame(rows).drop_duplicates(subset="symbol", keep="first")
    log.info("Polygon discovery — %d unique symbols", len(df))
    return df


def discover_edgar() -> pd.DataFrame:
    """Fetch SEC EDGAR company_tickers.json as gap-fill rows.

    EDGAR tickers already follow the hyphenated convention, so they are used
    as-is. Rows enter unvalidated (is_validated=False) and get validated later as
    a byproduct of the yfinance fetch.
    """
    import requests

    resp = requests.get(
        SEC_TICKERS_URL,
        headers={"User-Agent": secrets.SEC_USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()  # {"0": {"cik_str":..,"ticker":..,"title":..}, ...}
    rows = [
        {
            "symbol": rec["ticker"].strip().upper(),
            "polygon_symbol": None,
            "name": rec.get("title"),
            "security_type": None,  # unknown until yfinance quoteType fallback
            "cik": rec.get("cik_str"),
            "source": "edgar",
            "is_validated": False,
            "in_polygon": False,
        }
        for rec in data.values()
        if rec.get("ticker")
    ]
    df = pd.DataFrame(rows).drop_duplicates(subset="symbol", keep="first")
    log.info("EDGAR discovery — %d unique symbols", len(df))
    return df


def discover_edgar_funds() -> pd.DataFrame:
    """Fetch SEC company_tickers_mf.json — the mutual-fund share-class tickers.

    This is the only free, enumerable source of mutual-fund symbols (Polygon
    doesn't list open-end funds at all; FMP's free tier can look one up but not
    enumerate them). The file is funds-only, so security_type is set to
    'mutual_fund' directly — no later type resolution needed. Rows enter
    unvalidated and get validated as a byproduct of the yfinance fetch.

    Schema is the columnar SEC form: {"fields": [cik, seriesId, classId, symbol],
    "data": [[...], ...]}. A handful of tickers arrive wrapped in parentheses
    (e.g. "(NWAGX)") and one is blank — both are sanitized out.
    """
    import requests

    resp = requests.get(
        SEC_FUND_TICKERS_URL,
        headers={"User-Agent": secrets.SEC_USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    fields = payload.get("fields", [])
    data = payload.get("data", [])
    try:
        si, ci = fields.index("symbol"), fields.index("cik")
        sei, cli = fields.index("seriesId"), fields.index("classId")
    except ValueError:
        log.warning("EDGAR funds — unexpected schema %s, skipping", fields)
        return pd.DataFrame()

    rows = []
    for rec in data:
        sym = _sanitize_fund_ticker(rec[si])
        if not sym:
            continue
        rows.append(
            {
                "symbol": sym,
                "polygon_symbol": None,
                "name": None,  # MF file carries no fund name; yfinance fills it later
                "security_type": "mutual_fund",
                "cik": rec[ci],
                "series_id": rec[sei],
                "class_id": rec[cli],
                "source": "edgar_mf",
                "is_validated": False,
                "in_polygon": False,
            }
        )
    df = pd.DataFrame(rows).drop_duplicates(subset="symbol", keep="first")
    log.info("EDGAR funds discovery — %d unique fund symbols", len(df))
    return df


def _sanitize_fund_ticker(raw) -> str:
    """Clean a SEC mutual-fund ticker: strip whitespace + wrapping parens, upper."""
    return (str(raw) if raw is not None else "").strip().strip("()").strip().upper()


# --------------------------------------------------------------------------- #
# Merge + persist
# --------------------------------------------------------------------------- #
def _merge_sources(*frames: pd.DataFrame) -> pd.DataFrame:
    """Merge discovery sources in priority order — earlier frames win.

    Pass frames most-authoritative first (Polygon, then EDGAR operating
    companies, then EDGAR funds). Each later frame contributes only the symbols
    no earlier frame already supplied, so an ETF that's both a Polygon ticker and
    in the SEC fund file keeps its richer Polygon row.
    """
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()
    merged = frames[0]
    for f in frames[1:]:
        new = f[~f["symbol"].isin(set(merged["symbol"]))]
        merged = pd.concat([merged, new], ignore_index=True)
    return merged


#: Gap-fill sources are additive only — they may add symbols Polygon doesn't
#: carry, but must never overwrite an existing row (e.g. clobber a Polygon ETF's
#: type with 'mutual_fund' just because the SEC fund file also lists it).
GAPFILL_SOURCES = frozenset({"edgar", "edgar_mf"})


def save_symbols(db: Database, discovered: pd.DataFrame) -> int:
    """Upsert discovered symbols, preserving existing state flags.

    New symbols get is_active=True and is_validated default (False unless the
    source already set it). Existing symbols keep their is_active / is_validated;
    in_polygon is refreshed from this run. Rows from a gap-fill source whose
    symbol already exists are dropped (additive-only), so a funds- or EDGAR-only
    run can't downgrade a symbol an authoritative source already wrote.
    """
    if discovered.empty:
        return 0

    existing = db.read(TABLE)
    existing_flags = {}
    existing_syms: set = set()
    if not existing.empty:
        existing_syms = set(existing["symbol"])
        for _, r in existing.iterrows():
            existing_flags[r["symbol"]] = (
                r.get("is_active", 1),
                r.get("is_validated", 0),
            )

    df = discovered.copy()
    if "source" in df.columns and existing_syms:
        clobber = df["source"].isin(GAPFILL_SOURCES) & df["symbol"].isin(existing_syms)
        if clobber.any():
            log.info("Skipping %d gap-fill rows already present", int(clobber.sum()))
            df = df[~clobber]
    if df.empty:
        log.info("symbols.db — no new symbols to upsert")
        return 0
    if "is_validated" not in df.columns:
        df["is_validated"] = False

    def _is_active(sym: str) -> int:
        return int(existing_flags.get(sym, (1, 0))[0])  # new -> active

    def _is_validated(sym: str, fallback) -> int:
        if sym in existing_flags:
            return int(existing_flags[sym][1])  # preserve prior validation
        return int(bool(fallback))

    df["is_active"] = df["symbol"].map(_is_active)
    df["is_validated"] = [
        _is_validated(s, v) for s, v in zip(df["symbol"], df["is_validated"])
    ]

    db.upsert(TABLE, df, key="symbol")
    log.info("symbols.db — upserted %d rows (%d total)", len(df), len(db.read(TABLE)))
    return len(df)


def ensure_benchmark_symbols(db: Database) -> int:
    """Give every settings.BENCHMARK_SYMBOLS entry a symbols.db row. Additive-only.

    Discovery finds most benchmarks via Polygon, but not all: Polygon names the
    S&P 500 `^SPX` and has no Nasdaq Composite entry, so `^GSPC` and `^IXIC` are
    absent. They still get price history (load_ohlcv_universe builds their fetch
    rows from the constant, not from this table), which would otherwise leave a
    symbol holding OHLCV with no name or type for a chart to label it with.

    Only MISSING symbols are written — an existing row is never touched, so this
    can't disturb what discovery owns. Seeded rows are typed `index`, which keeps
    them out of the shared fetch universe (no quotes, no fundamentals) and out of
    the analysis universe, exactly like every other index.
    """
    existing: set = set()
    if db.table_exists(TABLE):
        stored = db.read(TABLE)
        if not stored.empty:
            existing = set(stored["symbol"])
    missing = {s: n for s, n in settings.BENCHMARK_SYMBOLS.items() if s not in existing}
    if not missing:
        return 0
    df = pd.DataFrame({
        "symbol": list(missing),
        "name": list(missing.values()),
        "security_type": "index",
        "source": "benchmark",
        "is_active": 1,
        "is_validated": 0,
    })
    db.upsert(TABLE, df, key="symbol")
    log.info("symbols.db — seeded %d benchmark index symbol(s): %s",
             len(df), ", ".join(missing))
    return len(df)


def resolve_types_from_quotes() -> int:
    """Fill missing security_type in symbols.db from the yfinance quoteType.

    EDGAR-sourced symbols enter without a type; the quotes fetcher records the
    normalized type in quotes.security_type_yf. This closes the loop by copying
    that into symbols.db wherever security_type is still NULL (Polygon-resolved
    types are left untouched). Returns the number of rows updated.
    """
    with Database(settings.QUOTES_DB) as q:
        if not q.table_exists("quotes") or "security_type_yf" not in q.columns("quotes"):
            return 0
        quotes = q.read("quotes")[["symbol", "security_type_yf"]].dropna(
            subset=["security_type_yf"]
        )
    if quotes.empty:
        return 0

    updated = 0
    with Database(settings.SYMBOLS_DB) as s:
        if not s.table_exists(TABLE):
            return 0
        for sym, st in quotes.itertuples(index=False):
            cur = s.conn.execute(
                f"UPDATE {TABLE} SET security_type=? "
                f"WHERE symbol=? AND (security_type IS NULL OR security_type='')",
                (st, sym),
            )
            updated += cur.rowcount
        s.conn.commit()
    log.info("Type write-back — resolved %d symbols from yfinance quoteType", updated)
    updated += _correct_types()
    purge_out_of_scope_financials()
    return updated


# A stock row whose name reads like a preferred / baby bond (fix list §6). Only used
# together with "no market cap": Preferred Bank (PFBC) is a real bank with one.
_PREFERRED_NAME = re.compile(
    r"Preferred|Deposit[ao]ry Shares|Notes due|Senior Notes|Subordinated Notes|Debentures|\bPfd\b",
    re.IGNORECASE,
)
_ADR_NAME = re.compile(r"\bADR\b|/ADR|American Depositary", re.IGNORECASE)
#: Types the financials fetchers (yfinance + EDGAR) cover.
FINANCIALS_TYPES = ("stock", "reit", "adr")


def _correct_types() -> int:
    """Correct types Polygon / the SEC lists got wrong (fix list §6, 2026-11-12).

    * `mutual_fund` that Yahoo calls EQUITY -> `adr` if the name says ADR, else
      `stock` (ASBHY, OBOCY: companies on the SEC mutual-fund list).
    * `stock` with no Yahoo market cap whose name reads as a preferred / note
      (ACGLN, AGNCL, ADAMG) -> `preferred`.

    Runs every fetch, after the quote fetch: Polygon discovery re-writes its own
    type (CS -> stock) each run, so a one-off fix would not stick. The review's
    other rule ("no market cap and the same longName as a stock that has one") was
    rejected — it hits real stocks (LBRDK Class C, ADRs with an F-share twin, units).
    """
    with Database(settings.QUOTES_DB) as q:
        if not q.table_exists("quotes"):
            return 0
        have = q.columns("quotes")
        cols = [c for c in ("symbol", "quoteType", "marketCap", "longName") if c in have]
        quotes = q.read("quotes")[cols].set_index("symbol")
    with Database(settings.SYMBOLS_DB) as s:
        if not s.table_exists(TABLE):
            return 0
        syms = s.query(f"SELECT symbol, name, security_type FROM {TABLE}")
        changes: list[tuple[str, str]] = []
        for sym, name, st in syms.itertuples(index=False):
            if sym not in quotes.index:
                continue
            qrow = quotes.loc[sym]
            label = " ".join(str(x) for x in (name, qrow.get("longName")) if isinstance(x, str))
            if st == "mutual_fund" and qrow.get("quoteType") == "EQUITY":
                changes.append(("adr" if _ADR_NAME.search(label) else "stock", sym))
            elif (st == "stock" and pd.isna(qrow.get("marketCap"))
                  and _PREFERRED_NAME.search(label)):
                changes.append(("preferred", sym))
        if changes:
            s.conn.executemany(f"UPDATE {TABLE} SET security_type=? WHERE symbol=?", changes)
            s.conn.commit()
    if changes:
        by = pd.Series([t for t, _ in changes]).value_counts().to_dict()
        log.info("Type corrections — %d symbols retyped %s", len(changes), by)
    return len(changes)


def purge_out_of_scope_financials() -> int:
    """Delete `financials` rows of symbols typed outside stock / reit / adr.

    Found 2026-11-12 (fix list §6 / Group B): ~340 preferreds, SPACs and others
    carried their issuer's statements (fetched while mistyped as `stock`), so the
    analysis computed company ratios with the PREFERRED's price (USB-PA P/E 149).
    The financials fetchers never touch these types, so the rows would only ever go
    stale. Runs after every type correction, so a retyped symbol is cleaned at once.
    """
    with Database(settings.SYMBOLS_DB) as s:
        if not s.table_exists(TABLE):
            return 0
        out = s.query(
            f"SELECT symbol FROM {TABLE} WHERE security_type IS NOT NULL "
            f"AND security_type NOT IN ({', '.join('?' for _ in FINANCIALS_TYPES)})",
            list(FINANCIALS_TYPES),
        )["symbol"].tolist()
    if not out:
        return 0
    with Database(settings.FINANCIALS_DB) as f:
        if not f.table_exists("financials"):
            return 0
        n = 0
        for i in range(0, len(out), 900):  # SQLite parameter limit
            chunk = out[i:i + 900]
            marks = ", ".join("?" for _ in chunk)
            n += int(f.query(f"SELECT count(*) AS n FROM financials WHERE symbol IN ({marks})",
                             chunk)["n"].iloc[0])
            f.execute(f"DELETE FROM financials WHERE symbol IN ({marks})", chunk)
    if n:
        log.info("Financials — removed %d rows of symbols outside stock / reit / adr", n)
    return n


def _log_composition(db: Database) -> None:
    """Log symbols.db composition (source + security_type) at summary level.

    Emitted through the logger so it lands in the rotating application log on
    every discovery run — not only when stdout happens to be captured.
    """
    df = db.read(TABLE)
    if df.empty:
        return
    if "source" in df.columns:
        log.info("symbols by source — %s", df["source"].value_counts().to_dict())
    if "security_type" in df.columns:
        counts = df["security_type"].value_counts(dropna=False).to_dict()
        counts = {
            ("none" if (k is None or (isinstance(k, float) and pd.isna(k))) else k): v
            for k, v in counts.items()
        }
        log.info("symbols by security_type — %s", counts)


def reassess_in_polygon(db: Database, current_symbols: set[str]) -> None:
    """Flag symbols absent from the latest Polygon run as in_polygon=False.

    Never deletes — keeps history. The is_active flip is handled separately in the
    end-of-run reassessment once the data fetch confirms missing data.
    """
    if not db.table_exists(TABLE):
        return
    placeholders = ",".join("?" for _ in current_symbols) or "''"
    db.execute(
        f"UPDATE {TABLE} SET in_polygon=0 "
        f"WHERE source='polygon' AND symbol NOT IN ({placeholders})",
        tuple(current_symbols),
    )


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def reassess_state(
    db: Database,
    assess_symbols: list[str] | None = None,
    allow_deactivate: bool = True,
) -> dict:
    """End-of-run reassessment of is_validated / is_active (Topics 3.1 & 9.2).

    Only the symbols actually fetched this run are judged: pass `assess_symbols`
    (the fetch universe). A symbol that wasn't fetched is left untouched — without
    this scoping, a partial/subset run would wrongly deactivate everything it
    didn't touch. `assess_symbols=None` falls back to all active symbols (a full
    run, where the universe IS every active symbol).

    `allow_deactivate=False` re-derives is_validated but never flips is_active off
    — used on a *stopped* run, where a symbol the stop never reached looks data-less
    but is simply un-fetched, so deactivating it (and dropping it from future fetch
    universes) would be wrong. Validation still only rises for symbols that do have
    recent data, so this safely lets the symbols fetched-so-far enter analysis.

    Validation (is_validated) is a byproduct of the normal fetch — no extra API
    calls. Requirements are type-aware:
      * stock / reit / adr      -> quote + recent OHLCV + recent financials
      * etf / closed-end / pref -> quote + recent OHLCV
      * everything else         -> quote
    "Recent" = OHLCV within OHLCV_INACTIVE_AFTER_WEEKS, financials within ~15 months.

    is_active is only flipped to False when a symbol returned NO data AND had no
    fetch errors — i.e. genuinely missing, not an API hiccup. Symbols with errors
    stay active so the next run retries them. last_active_date is set to the most
    recent OHLCV date.
    """
    from datetime import date, datetime, timedelta, timezone

    from data_layer import fetch_status

    if not db.table_exists(TABLE):
        return {}
    if "last_active_date" not in db.columns(TABLE):
        db.execute(f"ALTER TABLE {TABLE} ADD COLUMN last_active_date TEXT")
    if "deactivated_date" not in db.columns(TABLE):
        db.execute(f"ALTER TABLE {TABLE} ADD COLUMN deactivated_date TEXT")

    syms = db.read(TABLE)
    if assess_symbols is not None:
        active = syms[syms["symbol"].isin(set(assess_symbols))]
    elif "is_active" in syms:
        active = syms[syms["is_active"] == 1]
    else:
        active = syms
    if active.empty:
        return {}

    quote_syms: set = set()
    with Database(settings.QUOTES_DB) as q:
        if q.table_exists("quotes"):
            quote_syms = set(q.read("quotes")["symbol"])
    ohlcv_recent: dict = {}
    with Database(settings.OHLCV_DB) as o:
        if o.table_exists("ohlcv"):
            ohlcv_recent = dict(
                o.query("SELECT symbol, MAX(date) d FROM ohlcv GROUP BY symbol").itertuples(
                    index=False, name=None
                )
            )
    fin_recent: dict = {}
    with Database(settings.FINANCIALS_DB) as f:
        if f.table_exists("financials"):
            fin_recent = dict(
                f.query(
                    "SELECT symbol, MAX(period_end) d FROM financials GROUP BY symbol"
                ).itertuples(index=False, name=None)
            )

    errs = fetch_status.error_counts(db)
    err_syms = {sym for (sym, _f), n in errs.items() if n > 0}

    today = datetime.now(timezone.utc).date()
    ohlcv_cutoff = today - timedelta(weeks=settings.OHLCV_INACTIVE_AFTER_WEEKS)
    fin_cutoff = today - timedelta(days=460)  # ~15 months

    def _on_or_after(d: str | None, cutoff) -> bool:
        try:
            return bool(d) and date.fromisoformat(d) >= cutoff
        except (TypeError, ValueError):
            return False

    updates = []
    n_validated = n_deactivated = 0
    for row in active.itertuples():
        sym = row.symbol
        st = getattr(row, "security_type", None)
        has_quote = sym in quote_syms
        last_ohlcv = ohlcv_recent.get(sym)
        recent_ohlcv = _on_or_after(last_ohlcv, ohlcv_cutoff)
        recent_fin = _on_or_after(fin_recent.get(sym), fin_cutoff)

        if st in ("stock", "reit", "adr"):
            validated = has_quote and recent_ohlcv and recent_fin
        elif st in ("etf", "closed_end_fund", "preferred"):
            validated = has_quote and recent_ohlcv
        else:
            validated = has_quote

        if allow_deactivate:
            got_any = has_quote or bool(last_ohlcv)
            new_active = 0 if (not got_any and sym not in err_syms) else 1
        else:  # stopped run: never deactivate an un-reached symbol
            new_active = int(getattr(row, "is_active", 1) or 0)
        if validated:
            n_validated += 1
        if new_active == 0:
            n_deactivated += 1
        # deactivated_date starts the re-check window (rearm_inactive); a re-check
        # that finds nothing again keeps the ORIGINAL date, so the window ends.
        prev_off = getattr(row, "deactivated_date", None)
        off_date = (prev_off if isinstance(prev_off, str) and prev_off else today.isoformat())             if new_active == 0 else None
        updates.append((int(validated), new_active, last_ohlcv, off_date, sym))

    db.conn.executemany(
        f"UPDATE {TABLE} SET is_validated=?, is_active=?, last_active_date=?, "
        "deactivated_date=? WHERE symbol=?",
        updates,
    )
    db.conn.commit()
    summary = {
        "assessed": len(updates),
        "validated": n_validated,
        "deactivated": n_deactivated,
    }
    log.info("Reassessment — %s", summary)
    return summary


def _ensure_integer_column(db: Database, col: str) -> None:
    """Make `col` an INTEGER column (add it, or rebuild a REAL/TEXT one in place)."""
    info = db.query(f"PRAGMA table_info({TABLE})")
    row = info[info["name"] == col]
    if row.empty:
        db.execute(f"ALTER TABLE {TABLE} ADD COLUMN {col} INTEGER")
        return
    if str(row["type"].iloc[0]).upper() == "INTEGER":
        return
    tmp = f"{col}_int"
    for sql in (f"ALTER TABLE {TABLE} ADD COLUMN {tmp} INTEGER",
                f"UPDATE {TABLE} SET {tmp} = CAST({col} AS INTEGER) WHERE {col} IS NOT NULL",
                f"ALTER TABLE {TABLE} DROP COLUMN {col}",
                f"ALTER TABLE {TABLE} RENAME COLUMN {tmp} TO {col}"):
        db.execute(sql)


def refresh_company_ids(db: Database, cik_map: dict[str, int]) -> int:
    """Store the SEC CIK (an integer) and `company_id` for every symbol (fix list §25).

    `cik_map` is the SEC ticker -> CIK map (`EDGARFinancials._cik_map`). It covers
    ~96% of live stocks / ADRs (2026-11-12: 7,330 of 7,611; 1,987 had a CIK before).
    `company_id` = CIK, so share classes (GOOG / GOOGL) and an ADR with its foreign
    OTC twin (DTEGY / DTEGF) group as one company. Symbols the map doesn't know keep
    the CIK they had; their company_id follows it.
    """
    if not db.table_exists(TABLE):
        return 0
    _ensure_integer_column(db, "cik")
    _ensure_integer_column(db, "company_id")
    syms = db.query(f"SELECT symbol FROM {TABLE}")["symbol"].tolist()
    updates = [(int(cik), int(cik), s) for s in syms
               if (cik := cik_map.get(str(s).upper())) is not None]
    db.conn.executemany(f"UPDATE {TABLE} SET cik=?, company_id=? WHERE symbol=?", updates)
    db.execute(f"UPDATE {TABLE} SET company_id = cik WHERE company_id IS NULL AND cik IS NOT NULL")
    db.conn.commit()
    log.info("Company ids — %d symbols mapped to an SEC CIK", len(updates))
    return len(updates)


def rearm_inactive(db: Database) -> int:
    """Switch recently deactivated symbols back on for one re-check (fix list §15).

    `is_active = 0` is final in the fetch universe, so a symbol switched off during
    a data gap was never looked at again. Once every OHLCV_RECHECK_DAYS, a symbol
    deactivated within RECHECK_WINDOW_DAYS is switched back on and its no-data
    counters reset, so this run's fetchers try it. If it still returns nothing, the
    end-of-run `reassess_state` switches it off again and keeps its original
    `deactivated_date`, so the window closes. Run before the fetch universe loads.
    """
    from datetime import date, timedelta

    from data_layer import fetch_status

    if not db.table_exists(TABLE) or "deactivated_date" not in db.columns(TABLE):
        return 0
    today = date.today()
    since = (today - timedelta(days=settings.RECHECK_WINDOW_DAYS)).isoformat()
    off = db.query(f"SELECT symbol FROM {TABLE} WHERE is_active = 0 AND deactivated_date >= ?",
                   [since])["symbol"].tolist()
    if not off:
        return 0
    tried = fetch_status.last_fetched_dates(db, "yfinance_quotes")
    due = [s for s in off
           if s not in tried or (today - tried[s]).days >= settings.OHLCV_RECHECK_DAYS]
    if not due:
        return 0
    db.conn.executemany(f"UPDATE {TABLE} SET is_active = 1 WHERE symbol = ?", [(s,) for s in due])
    db.conn.executemany(
        f"UPDATE {fetch_status.TABLE} SET no_data_count = 0 WHERE symbol = ?", [(s,) for s in due])
    db.conn.commit()
    log.info("Re-check — %d recently deactivated symbol(s) switched on for one more try", len(due))
    return len(due)


#: Security types that get daily price history (YFinanceOHLCV.applies_to, minus
#: index — only the benchmark indices are fetched, and they are never screened).
PRICE_HISTORY_TYPES = ("stock", "etf", "reit", "adr", "closed_end_fund", "preferred", "mutual_fund")
_DELISTED_MIN_SESSIONS = 5          # one-day halts and holidays never count
_DELISTED_GAP_MULTIPLE = 3          # sparse NAV funds: 3x their own usual gap
_DELISTED_GAP_LOOKBACK_DAYS = 365


def mark_delisted(db: Database, benchmark: str = "^GSPC") -> dict:
    """Mark symbols whose price history has stopped (fix list §15, 2026-11-12).

    `is_active` only flips when a fetch returns nothing at all, and Yahoo keeps
    serving a dead symbol's old bars — so ~3,200 dead funds and stocks stayed in
    every screen. This judges each price-history symbol by its OWN last OHLCV fetch:

      sessions_behind = trading sessions (the `benchmark` bars) after the symbol's
                        last bar, up to the session it was last fetched in
      threshold       = max(5, 3 x its mean gap between bars over the last year)
      is_delisted     = sessions_behind > threshold; delisted_date = last bar

    Judging against the symbol's own fetch date means a partial run can't mark
    un-fetched symbols dead; the per-symbol threshold keeps funds that price only
    weekly or monthly from being marked between prints. Recomputed after every
    fetch, so a symbol clears itself when a new bar arrives. `is_active` is NOT
    touched (a dead symbol must stay fetchable to clear). Types without price
    history (warrant, unit, spac) and never-fetched symbols are not judged.
    The analysis universe skips `is_delisted = 1` (`pipeline._universe`).
    """
    import bisect
    from datetime import date, timedelta

    from data_layer import fetch_status

    if not db.table_exists(TABLE):
        return {}
    for col, typ in (("is_delisted", "INTEGER"), ("delisted_date", "TEXT")):
        if col not in db.columns(TABLE):
            db.execute(f"ALTER TABLE {TABLE} ADD COLUMN {col} {typ}")

    syms = db.query(f"SELECT symbol, security_type, is_delisted FROM {TABLE}")
    was_delisted = set(syms.loc[syms["is_delisted"] == 1, "symbol"])
    syms = syms[syms["security_type"].isin(PRICE_HISTORY_TYPES)]
    fetched = fetch_status.last_fetched_dates(db, "yfinance_ohlcv")
    judged = [s for s in syms["symbol"] if s in fetched]
    if not judged:
        return {}

    since = (date.today() - timedelta(days=_DELISTED_GAP_LOOKBACK_DAYS)).isoformat()
    with Database(settings.OHLCV_DB) as o:
        if not o.table_exists("ohlcv"):
            return {}
        sessions = o.query("SELECT date FROM ohlcv WHERE symbol = ? ORDER BY date",
                           [benchmark])["date"].tolist()
        bars = o.query(
            "SELECT symbol, MAX(date) AS last, "
            "SUM(date >= ?) AS n_recent, MIN(CASE WHEN date >= ? THEN date END) AS first_recent "
            "FROM ohlcv GROUP BY symbol", [since, since]
        ).set_index("symbol")
    if len(sessions) < 2 * _DELISTED_MIN_SESSIONS:
        log.warning("Delisted check skipped — %s has too few bars", benchmark)
        return {}

    def _count_between(after: str, upto: str) -> int:
        """Sessions s with after < s <= upto."""
        return max(bisect.bisect_right(sessions, upto) - bisect.bisect_right(sessions, after), 0)

    updates = []
    n_dead = 0
    for sym in judged:
        if sym not in bars.index:
            continue
        last = str(bars.at[sym, "last"])[:10]
        behind = _count_between(last, fetched[sym].isoformat())
        n_recent = int(bars.at[sym, "n_recent"] or 0)
        first = bars.at[sym, "first_recent"]
        gap = (_count_between(str(first)[:10], last) / (n_recent - 1)
               if n_recent > 1 and isinstance(first, str) else 1.0)
        threshold = max(_DELISTED_MIN_SESSIONS, _DELISTED_GAP_MULTIPLE * gap)
        dead = behind > threshold
        n_dead += dead
        updates.append((int(dead), last if dead else None, sym))

    db.conn.executemany(
        f"UPDATE {TABLE} SET is_delisted=?, delisted_date=? WHERE symbol=?", updates)
    db.conn.commit()
    # Outage fingerprint: many symbols newly stopping on the same date. Real fund-
    # family closures cluster too (193 funds on 2025-09-23), so this only warns.
    new = pd.Series([d for dead, d, s in updates if dead and s not in was_delisted])
    bursts = new.value_counts()
    bursts = bursts[bursts >= settings.DELISTED_BURST_WARN]
    for day, n in bursts.items():
        log.warning("Delisted check — %d symbols newly stopped on %s: a Yahoo data outage "
                    "or a fund-family closure. They return by themselves if prices resume "
                    "(monthly re-check).", n, day)
    summary = {"judged": len(updates), "delisted": n_dead, "newly_delisted": int(len(new))}
    log.info("Delisted check — %s", summary)
    return summary


def run_discovery(
    use_polygon: bool = True,
    use_edgar: bool = True,
    use_edgar_funds: bool = True,
) -> int:
    """Full symbol-discovery run -> writes symbols.db. Returns rows upserted.

    Sources are merged in priority order: Polygon (primary) -> EDGAR operating
    companies -> EDGAR mutual funds (gap-fill only). Any source can be disabled
    (e.g. EDGAR-only when no Polygon key is set, or skip the ~24k funds).
    """
    from core.net import configure_tls
    from data_layer import fetch_status

    configure_tls()

    df_poly = pd.DataFrame()
    df_edgar = pd.DataFrame()
    df_funds = pd.DataFrame()
    if use_polygon and secrets.has("POLYGON_API_KEY"):
        df_poly = discover_polygon()
    elif use_polygon:
        log.warning("Polygon skipped — POLYGON_API_KEY not set")
    # A Stop during any source aborts the whole run BEFORE the write, so symbols.db
    # is never updated from a partial (e.g. half-paged Polygon) universe — that
    # would wrongly flip in_polygon flags for the symbols not yet seen.
    if cancel.is_cancelled():
        log.warning("Discovery cancelled — symbols.db not modified")
        return 0
    if use_edgar:
        df_edgar = discover_edgar()
    if use_edgar_funds:
        df_funds = discover_edgar_funds()
    if cancel.is_cancelled():
        log.warning("Discovery cancelled — symbols.db not modified")
        return 0

    merged = _merge_sources(df_poly, df_edgar, df_funds)
    if merged.empty:
        log.warning("Discovery produced no symbols")
        return 0

    with Database(settings.SYMBOLS_DB) as db:
        fetch_status.ensure_table(db)
        n = save_symbols(db, merged)
        if not df_poly.empty:
            reassess_in_polygon(db, set(df_poly["symbol"]))
        _log_composition(db)
    return n
