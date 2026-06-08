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
    return updated


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
def reassess_state(db: Database, assess_symbols: list[str] | None = None) -> dict:
    """End-of-run reassessment of is_validated / is_active (Topics 3.1 & 9.2).

    Only the symbols actually fetched this run are judged: pass `assess_symbols`
    (the fetch universe). A symbol that wasn't fetched is left untouched — without
    this scoping, a partial/subset run would wrongly deactivate everything it
    didn't touch. `assess_symbols=None` falls back to all active symbols (a full
    run, where the universe IS every active symbol).

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

        got_any = has_quote or bool(last_ohlcv)
        new_active = 0 if (not got_any and sym not in err_syms) else 1
        if validated:
            n_validated += 1
        if new_active == 0:
            n_deactivated += 1
        updates.append((int(validated), new_active, last_ohlcv, sym))

    db.conn.executemany(
        f"UPDATE {TABLE} SET is_validated=?, is_active=?, last_active_date=? WHERE symbol=?",
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
