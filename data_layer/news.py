"""
On-demand symbol news (the Output "Latest news" action).

Aggregates recent headlines for a *selection* of symbols from three sources —
yfinance, Polygon and finviz — into one de-duplicated, newest-first table.

This is **not** a `BaseFetcher` and is **never** wired into the fetch orchestrator
or `fetch_status`: it runs only when the user triggers the Output news action, so
news calls stay completely outside the main fetch runs. Nothing is persisted — the
result is display-only and time-sensitive (the Charts view caches it briefly in
session instead).

Each source is throttled with `ratelimit` keyed off `settings.RATE_LIMITS`
(mirroring `data_layer.symbols._polygon_page_getter`) and fails soft per
symbol/source — one bad symbol or a missing API key never breaks the table.

Normalized article shape (one dict per article, before de-dup):
    {symbol, published (tz-aware UTC datetime|None), title, url,
     publisher, source, sentiment}
`sentiment` is only populated by Polygon.
"""

from __future__ import annotations

import re
from typing import Callable

import pandas as pd
from ratelimit import limits, sleep_and_retry
from tenacity import retry, stop_after_attempt, wait_fixed

from config import secrets, settings
from core.logging_config import get_logger

log = get_logger(__name__)

POLYGON_NEWS_URL = "https://api.polygon.io/v2/reference/news"

# Stable source ordering for the merged "Sources" label and richest-row choice.
_SOURCE_ORDER = {"yfinance": 0, "polygon": 1, "finviz": 2}

_NONWORD = re.compile(r"[^a-z0-9]+")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _to_utc(value, *, assume_et: bool = False) -> pd.Timestamp | None:
    """Parse a timestamp to tz-aware UTC. ISO 'Z' strings are already UTC; finviz
    timestamps are naive US/Eastern (`assume_et=True`). Returns None on failure."""
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        return None
    if ts is None or pd.isna(ts):
        return None
    if ts.tzinfo is None:
        ts = ts.tz_localize("US/Eastern" if assume_et else "UTC")
    return ts.tz_convert("UTC")


def _norm_title(title: str) -> str:
    """Collapse a headline to a comparison key: lowercase, all non-alphanumerics
    removed (so "Amazon's" and "Amazons" — finviz strips apostrophes — match)."""
    return _NONWORD.sub("", (title or "").lower())


def _canon_url(url: str) -> str:
    """Canonical host+path (drop scheme, query and fragment) for URL-based de-dup."""
    u = (url or "").split("#", 1)[0].split("?", 1)[0]
    u = re.sub(r"^https?://", "", u, flags=re.IGNORECASE)
    return u.rstrip("/").lower()


def _abs_url(url: str, base: str = "") -> str:
    """Force an article URL to absolute so the UI's LinkColumn doesn't resolve it
    relative to the app (http://localhost:8501/...). Sources sometimes return a
    scheme-less or path-only URL — e.g. finviz returns its GlobeNewswire links as
    site-relative paths like '/news/361184/...' (finviz's own redirect pages).

    `base` (e.g. 'https://finviz.com') is the host such site-relative '/path' URLs
    belong to. Scheme-less hosts get https://; a path-only URL with no `base` has an
    unknown host and is dropped ("")."""
    u = (url or "").strip()
    if not u:
        return ""
    if u.startswith(("http://", "https://")):
        return u
    if u.startswith("//"):       # protocol-relative
        return "https:" + u
    if u.startswith("/"):        # site-relative path: resolve against base host
        return base.rstrip("/") + u if base else ""
    return "https://" + u        # bare host, e.g. www.globenewswire.com/...


# --------------------------------------------------------------------------- #
# per-source rate-limited getters (built fresh per fetch_news call so they read
# current settings; @retry outermost so a retry re-acquires a throttle slot)
# --------------------------------------------------------------------------- #
def _yfinance_getter():
    calls, period = settings.RATE_LIMITS.get("yfinance", (100, 60))

    @retry(stop=stop_after_attempt(settings.RETRY_MAX_ATTEMPTS),
           wait=wait_fixed(settings.RETRY_WAIT_SECONDS), reraise=True)
    @sleep_and_retry
    @limits(calls=calls, period=period)
    def _get(symbol: str, count: int) -> list[dict]:
        import yfinance as yf
        return yf.Ticker(symbol).get_news(count=count, tab="news") or []

    return _get


def _polygon_getter():
    import requests

    calls, period = settings.RATE_LIMITS.get("polygon", (5, 60))

    @retry(stop=stop_after_attempt(settings.RETRY_MAX_ATTEMPTS),
           wait=wait_fixed(settings.RETRY_WAIT_SECONDS), reraise=True)
    @sleep_and_retry
    @limits(calls=calls, period=period)
    def _get(symbol: str, count: int, api_key: str) -> list[dict]:
        resp = requests.get(
            POLYGON_NEWS_URL,
            params={"ticker": symbol, "limit": count, "order": "desc",
                    "sort": "published_utc", "apiKey": api_key},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("results", []) or []

    return _get


def _finviz_getter():
    calls, period = settings.RATE_LIMITS.get("finviz", (3, 1))

    @retry(stop=stop_after_attempt(settings.RETRY_MAX_ATTEMPTS),
           wait=wait_fixed(settings.RETRY_WAIT_SECONDS), reraise=True)
    @sleep_and_retry
    @limits(calls=calls, period=period)
    def _get(symbol: str) -> pd.DataFrame:
        from finvizfinance.quote import finvizfinance
        df = finvizfinance(symbol).ticker_news()
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()

    return _get


# --------------------------------------------------------------------------- #
# article-body scrape (the "Generate AI news reports" action — NOT used by the
# news table). Fetches a single article page and extracts clean main text + date
# with trafilatura. Throttled like the source getters; fails soft per URL.
# --------------------------------------------------------------------------- #
# A realistic desktop User-Agent: many publishers serve a stub / block obvious bots.
_SCRAPE_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Jina AI Reader — a free reader proxy that renders JS and returns clean article
# text. Used as a FALLBACK when the direct fetch comes back empty (publisher served
# a JS shell / light bot-block). Prefix the article URL with this. No key needed for
# the free tier; an optional JINA_API_KEY raises the rate limit if set.
_JINA_READER_BASE = "https://r.jina.ai/"


def _article_getter():
    """Rate-limited HTTP GET of an article page, returning raw HTML (or '')."""
    import requests

    from core.net import configure_tls
    configure_tls()  # idempotent; route the scrape through the OS trust store

    calls, period = settings.RATE_LIMITS.get("article_scrape", (10, 1))

    @retry(stop=stop_after_attempt(settings.RETRY_MAX_ATTEMPTS),
           wait=wait_fixed(settings.RETRY_WAIT_SECONDS), reraise=True)
    @sleep_and_retry
    @limits(calls=calls, period=period)
    def _get(url: str) -> str:
        resp = requests.get(url, headers={"User-Agent": _SCRAPE_UA},
                            timeout=settings.ARTICLE_SCRAPE_TIMEOUT)
        resp.raise_for_status()
        return resp.text or ""

    return _get


def _jina_getter():
    """Rate-limited GET of the Jina Reader proxy → clean article TEXT (or '').

    Gentler throttle than the direct getter (free Jina tier is ~20/min). An optional
    JINA_API_KEY (in .env) is sent as a bearer token when present — never required."""
    import requests

    from core.net import configure_tls
    configure_tls()

    calls, period = settings.RATE_LIMITS.get("jina_reader", (15, 60))
    key = secrets.get("JINA_API_KEY").strip()  # optional; "" when unset
    headers = {"User-Agent": _SCRAPE_UA}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    # Strip page chrome (nav/ads/footer/related) before extraction — deterministic,
    # keeps the full article. Optional readerlm engine for cleaner-but-riskier output.
    remove = (getattr(settings, "JINA_READER_REMOVE_SELECTOR", "") or "").strip()
    if remove:
        headers["X-Remove-Selector"] = remove
    engine = (getattr(settings, "JINA_READER_ENGINE", "") or "").strip()
    if engine:
        headers["X-Engine"] = engine

    @retry(stop=stop_after_attempt(settings.RETRY_MAX_ATTEMPTS),
           wait=wait_fixed(settings.RETRY_WAIT_SECONDS), reraise=True)
    @sleep_and_retry
    @limits(calls=calls, period=period)
    def _get(url: str) -> str:
        resp = requests.get(_JINA_READER_BASE + url, headers=headers,
                            timeout=settings.ARTICLE_SCRAPE_TIMEOUT)
        resp.raise_for_status()
        return resp.text or ""

    return _get


def build_scrapers() -> dict:
    """Build the article scrapers ONCE so a batch shares their throttles. Pass the
    result to `fetch_article(url, scrapers=…)`. Mirrors the old single-getter pattern."""
    return {"direct": _article_getter(), "jina": _jina_getter()}


# Jina markdown leftovers we strip: standalone image lines, code-fence wrappers
# (```/```markdown), and Jina's "Markdown Content:" label line.
_MD_IMAGE_LINE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$", re.MULTILINE)
_MD_FENCE_LINE = re.compile(r"^\s*```[a-zA-Z]*\s*$", re.MULTILINE)
_MD_CONTENT_LABEL = re.compile(r"^\s*Markdown Content:\s*$", re.MULTILINE)


def _clean_jina(text: str) -> str:
    """Tidy Jina Reader markdown: drop standalone image lines, code fences and the
    'Markdown Content:' label, then collapse blank-line runs. Leaves real text intact."""
    text = _MD_IMAGE_LINE.sub("", text)
    text = _MD_FENCE_LINE.sub("", text)
    text = _MD_CONTENT_LABEL.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract(html: str) -> tuple[str | None, pd.Timestamp | None]:
    """Pull clean main text + published date from raw article HTML via trafilatura."""
    if not html:
        return None, None
    try:
        import logging

        import trafilatura
        # trafilatura logs a per-page "discarding data: None" WARNING when a page has
        # no extractable body — expected here (we fall back to Jina), so quiet it.
        logging.getLogger("trafilatura").setLevel(logging.ERROR)
        text = trafilatura.extract(html, include_comments=False, include_tables=False)
        date = None
        meta = trafilatura.extract_metadata(html)
        if meta is not None and getattr(meta, "date", None):
            date = _to_utc(meta.date)
    except Exception as exc:  # noqa: BLE001
        log.warning("article extract failed: %s", exc)
        return None, None
    text = (text or "").strip()
    return (text or None), date


def fetch_article(url: str, scrapers=None) -> tuple[str | None, pd.Timestamp | None]:
    """Scrape one article URL → (clean_text, published_date) or (None, None).

    Fail-soft chain (each hop swallows its own errors):
      1. direct fetch (`requests` + trafilatura) — fast, fully local;
      2. if `settings.ARTICLE_SCRAPE_USE_JINA`, retry via the Jina Reader proxy
         (renders JS / clears light bot-blocks) — recovers many free pages a plain
         fetch can't, but sends the URL to a third party.
    Both empty → (None, None); the caller then falls back to the news-API summary.
    Pass a shared `scrapers` (from `build_scrapers()`) to reuse one throttle across a
    batch; omitted, it builds its own."""
    if not url:
        return None, None
    scrapers = scrapers or build_scrapers()

    # 1) direct fetch. A block here is EXPECTED (many publishers 403/401 bots) and is
    # just the cue to try Jina next — log at DEBUG, not WARNING, to keep the log clean.
    try:
        html = scrapers["direct"](url)
    except Exception as exc:  # noqa: BLE001 - fail soft per article
        log.debug("direct fetch blocked for %s (%s); trying Jina", url, exc)
        html = ""
    text, date = _extract(html)
    if text:
        return text, date

    # 2) Jina Reader fallback (returns clean markdown directly — no trafilatura needed)
    if settings.ARTICLE_SCRAPE_USE_JINA:
        try:
            jina_text = _clean_jina(scrapers["jina"](url) or "")
        except Exception as exc:  # noqa: BLE001 - fail soft
            log.debug("Jina Reader failed for %s: %s", url, exc)
            jina_text = ""
        if jina_text:
            log.debug("recovered article via Jina Reader: %s", url)
            return jina_text, None

    # Only now is the article truly unretrievable — the one signal worth a WARNING.
    log.warning("could not retrieve article text for %s", url)
    return None, None


# --------------------------------------------------------------------------- #
# per-source normalizers -> list of normalized article dicts
# --------------------------------------------------------------------------- #
def _from_yfinance(get, symbol: str, count: int) -> list[dict]:
    try:
        raw = get(symbol, count)
    except Exception as exc:  # noqa: BLE001 - fail soft per symbol/source
        log.warning("news yfinance failed for %s: %s", symbol, exc)
        return []
    out = []
    for item in raw:
        c = item.get("content") or item  # 1.x nests under "content"; tolerate old flat shape
        title = c.get("title") or item.get("title") or ""
        url = _abs_url(((c.get("clickThroughUrl") or {}).get("url"))
                       or ((c.get("canonicalUrl") or {}).get("url"))
                       or item.get("link") or "")
        publisher = ((c.get("provider") or {}).get("displayName")
                     or item.get("publisher") or "")
        published = _to_utc(c.get("pubDate") or item.get("providerPublishTime"))
        if title and url:
            out.append({"symbol": symbol, "published": published, "title": title.strip(),
                        "url": url, "publisher": publisher, "source": "yfinance",
                        "sentiment": "", "summary": (c.get("summary") or "").strip(),
                        "related_tickers": [], "has_insight": False, "keywords": []})
    return out


def _from_polygon(get, symbol: str, count: int, api_key: str) -> list[dict]:
    try:
        raw = get(symbol, count, api_key)
    except Exception as exc:  # noqa: BLE001
        log.warning("news polygon failed for %s: %s", symbol, exc)
        return []
    out = []
    for a in raw:
        # insights carry per-ticker sentiment; pick this symbol's entry.
        sentiment, has_insight = "", False
        for ins in a.get("insights") or []:
            if str(ins.get("ticker", "")).upper() == symbol.upper():
                sentiment, has_insight = ins.get("sentiment", "") or "", True
                break
        related = [str(t).upper() for t in (a.get("tickers") or [])]
        title, url = a.get("title") or "", _abs_url(a.get("article_url") or "")
        if title and url:
            out.append({"symbol": symbol, "published": _to_utc(a.get("published_utc")),
                        "title": title.strip(), "url": url,
                        "publisher": (a.get("publisher") or {}).get("name", ""),
                        "source": "polygon", "sentiment": sentiment,
                        "summary": (a.get("description") or "").strip(),
                        "related_tickers": related, "has_insight": has_insight,
                        "keywords": [str(k) for k in (a.get("keywords") or [])]})
    return out


def _from_finviz(get, symbol: str) -> list[dict]:
    try:
        df = get(symbol)
    except Exception as exc:  # noqa: BLE001
        log.warning("news finviz failed for %s: %s", symbol, exc)
        return []
    out = []
    for r in df.to_dict("records"):
        # finviz returns some links (e.g. GlobeNewswire) as site-relative paths
        # like '/news/361184/...' — resolve those against the finviz host.
        title = str(r.get("Title", "")).strip()
        url = _abs_url(str(r.get("Link", "") or ""), base="https://finviz.com")
        if title and url:
            out.append({"symbol": symbol, "published": _to_utc(r.get("Date"), assume_et=True),
                        "title": title, "url": url, "publisher": str(r.get("Source", "") or ""),
                        "source": "finviz", "sentiment": "", "summary": "",
                        "related_tickers": [], "has_insight": False, "keywords": []})
    return out


# --------------------------------------------------------------------------- #
# de-dup + assemble
# --------------------------------------------------------------------------- #
def _dedup(articles: list[dict]) -> list[dict]:
    """Collapse the same story (per symbol) from multiple sources into one row.

    Key = normalized title (falls back to canonical URL). The merged row keeps the
    richest fields and a "Sources" union (e.g. "yfinance, finviz")."""
    groups: dict[tuple, list[dict]] = {}
    for a in articles:
        key = (a["symbol"], _norm_title(a["title"]) or _canon_url(a["url"]))
        groups.setdefault(key, []).append(a)

    merged = []
    for items in groups.values():
        items.sort(key=lambda x: _SOURCE_ORDER.get(x["source"], 9))
        sources = sorted({i["source"] for i in items}, key=lambda s: _SOURCE_ORDER.get(s, 9))
        pub = next((i["published"] for i in items if i["published"] is not None), None)
        related = max((i.get("related_tickers") or [] for i in items), key=len, default=[])
        keywords = sorted({k for i in items for k in (i.get("keywords") or [])})
        merged.append({
            "Symbol": items[0]["symbol"],
            "Published": pub,
            "Title": items[0]["title"],
            "Url": next((i["url"] for i in items if i["url"]), items[0]["url"]),
            "Publisher": next((i["publisher"] for i in items if i["publisher"]), ""),
            "Sources": ", ".join(sources),
            "Sentiment": next((i["sentiment"] for i in items if i["sentiment"]), ""),
            # internal signals for classify_relevance (not shown as table columns):
            "summary": next((i.get("summary") for i in items if i.get("summary")), ""),
            "related_tickers": related,
            "has_insight": any(i.get("has_insight") for i in items),
            "keywords": keywords,
        })
    return merged


def fetch_news(
    symbols,
    sources=None,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> pd.DataFrame:
    """Aggregate recent news for `symbols` from `sources` into one de-duplicated,
    newest-first DataFrame: Symbol, Published, Title, Url, Publisher, Sources,
    Sentiment. Missing sources/keys degrade gracefully (logged, skipped).

    `on_progress(symbol, done, total)`, if given, fires before each symbol's
    turn (`done` = symbols already completed) — the sole hook the API layer
    needs to stream progress, since this loop is otherwise blocking (rate-limited
    network calls)."""
    symbols = [str(s).strip().upper() for s in symbols if str(s).strip()]
    sources = tuple(sources) if sources else settings.NEWS_SOURCES
    count = settings.NEWS_ARTICLES_PER_SOURCE
    # Visible columns + internal signal columns (summary/related_tickers/has_insight/
    # keywords) used only by classify_relevance — never shown in the table.
    cols = ["Symbol", "Published", "Title", "Url", "Publisher", "Sources", "Sentiment",
            "summary", "related_tickers", "has_insight", "keywords"]
    if not symbols:
        return pd.DataFrame(columns=cols)

    yf_get = _yfinance_getter() if "yfinance" in sources else None
    fv_get = _finviz_getter() if "finviz" in sources else None
    poly_get, poly_key = None, ""
    if "polygon" in sources:
        try:
            poly_key = secrets.require("POLYGON_API_KEY")
            poly_get = _polygon_getter()
        except Exception as exc:  # noqa: BLE001 - missing key: skip Polygon, keep the rest
            log.warning("news polygon disabled: %s", exc)

    articles: list[dict] = []
    for i, sym in enumerate(symbols):
        if on_progress:
            on_progress(sym, i, len(symbols))
        if yf_get is not None:
            articles += _from_yfinance(yf_get, sym, count)
        if poly_get is not None:
            articles += _from_polygon(poly_get, sym, count, poly_key)
        if fv_get is not None:
            articles += _from_finviz(fv_get, sym)

    rows = _dedup(articles)
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return df

    # Drop stale articles (keep rows with no date), then sort newest-first.
    if settings.NEWS_LOOKBACK_DAYS:
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=settings.NEWS_LOOKBACK_DAYS)
        df = df[df["Published"].isna() | (df["Published"] >= cutoff)]
    return df.sort_values("Published", ascending=False, na_position="last").reset_index(drop=True)


# --------------------------------------------------------------------------- #
# relevance: is an article ABOUT the company, or about its broader environment?
# Heuristic, code-only — no article-body fetch, no LLM. (See the Charts news view,
# which groups each symbol's table into "About <SYMBOL>" vs "Broader context".)
# --------------------------------------------------------------------------- #
# Trailing corporate-form tokens stripped to a matchable company core. Includes
# share-class / listing descriptors (e.g. "... Common Shares", "Ordinary Shares",
# "Common Stock", "Depositary Receipts") that exchanges append to the name.
_NAME_SUFFIXES = {
    "incorporated", "inc", "corporation", "corp", "company", "co", "limited", "ltd",
    "plc", "llc", "lp", "holdings", "holding", "group", "ag", "nv", "sa", "se",
    "adr", "reit", "trust", "class", "a", "b", "c", "&", "the",
    "common", "ordinary", "shares", "share", "stock", "shs", "units", "unit",
    "depositary", "depository", "receipt", "receipts", "sponsored", "registered",
    "new", "cl", "series",
}


def _clean_name(name: str) -> str:
    """Reduce a company name to a matchable core: drop a leading 'The' and trailing
    corporate-form tokens (Inc/Corp/Co/Class A…). "Apple Inc." -> "Apple"."""
    tokens = re.sub(r"[.,]", " ", name or "").split()
    while tokens and tokens[0].lower() == "the":
        tokens.pop(0)
    while tokens and tokens[-1].lower().strip(".") in _NAME_SUFFIXES:
        tokens.pop()
    return " ".join(tokens).strip()


def classify_relevance(df: pd.DataFrame, sym_meta: dict) -> pd.DataFrame:
    """Add a `Relevance` column ∈ {"Company", "Context"}. An article is "Company"
    when it names/centers on the symbol; otherwise "Context" (about its sector /
    peers / the wider market). `sym_meta` = {symbol: {company, sector, industry}}.

    Signals (code-only, from already-retrieved metadata): the cleaned company name or
    the ticker appearing in the title/summary, or Polygon having written a per-ticker
    insight while tagging only a few tickers (a focused piece)."""
    out = df.copy()
    if out.empty:
        out["Relevance"] = pd.Series(dtype="object")
        return out

    name_cores = {s: _clean_name((m or {}).get("company", "")) for s, m in sym_meta.items()}
    rel = []
    for r in out.itertuples(index=False):
        sym = str(r.Symbol)
        text = f"{r.Title or ''} {getattr(r, 'summary', '') or ''}"
        core = name_cores.get(sym, "")
        related = getattr(r, "related_tickers", None) or []
        company = bool(
            (core and re.search(rf"\b{re.escape(core)}\b", text, re.IGNORECASE))
            or (len(sym) >= 2 and re.search(rf"\b{re.escape(sym)}\b", text))  # uppercase ticker token
            or (getattr(r, "has_insight", False) and 0 < len(related) <= 3)
        )
        rel.append("Company" if company else "Context")
    out["Relevance"] = rel
    return out
