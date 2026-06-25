"""
AI news report — per-symbol markdown of scraped article text.

Unlike `news_report.py` (a PDF of headlines), this report **scrapes the full
article body** behind each symbol's company-specific ("About") links and writes one
plain-markdown file per symbol — `<symbol>_ai_news_report.md` in
`settings.AI_NEWS_REPORTS_DIR` — designed to be read by an AI (Claude). Every
article block labels its fields (Title / Date / Source / Article text) so the
reading AI can never mistake the headline for body text.

It reuses the already-classified news frame the Charts news view holds (same input
as `news_report.build_pdf`): no second news-API round-trip. The only network calls
are the per-article page scrapes via `data_layer.news.fetch_article`, which fails
soft (paywalled / bot-blocked pages fall back to the news-API summary).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from config import settings
from core.logging_config import get_logger
from data_layer import news as _news

log = get_logger("reporting.ai_news_report")

_SLUG = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(symbol: str) -> str:
    """Filesystem-safe symbol for the filename (e.g. 'BRK.B' -> 'BRK.B')."""
    s = _SLUG.sub("-", (symbol or "symbol").strip()).strip("-_.")
    return (s or "symbol")[:40]


def _date_str(value) -> str:
    """Format an article date as 'YYYY-MM-DD', or 'unknown' when missing."""
    ts = pd.Timestamp(value) if value is not None and not pd.isna(value) else None
    return "unknown" if ts is None else ts.strftime("%Y-%m-%d")


def _article_block(n: int, *, title: str, date, publisher: str, url: str,
                   text: str | None, summary: str) -> str:
    """One labeled article section. Falls back to the news-API summary when the full
    text couldn't be scraped, keeping the same labels so the AI still gets it."""
    src = " · ".join(p for p in (publisher.strip(), url.strip()) if p) or "—"
    head = [
        f"## Article {n}",
        f"**Title:** {title.strip() or '(untitled)'}",
        f"**Date:** {_date_str(date)}",
        f"**Source:** {src}",
        "",
    ]
    if text:
        head += ["**Article text:**", text.strip()]
    else:
        body = summary.strip() or "(no text available)"
        head += ["**Article text (summary only — full text could not be retrieved):**",
                 body]
    return "\n".join(head)


def _build_markdown(symbol: str, company: str, about: pd.DataFrame, scrapers) -> str:
    """Scrape each About row and render the symbol's full markdown document."""
    header = f"{symbol} ({company})" if company else symbol
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# AI News Report — {header}"]

    if about.empty:
        lines += [f"_Generated {now}_", "", "No company-specific articles found "
                  "for this symbol."]
        return "\n".join(lines) + "\n"

    lines += [
        f"_Generated {now} · {len(about)} company-specific article(s)_",
        "_For each article below: the labeled Title, the Date, then the full "
        "Article text._",
    ]

    for i, r in enumerate(about.itertuples(index=False), start=1):
        url = getattr(r, "Url", "") or ""
        text, scraped_date = _news.fetch_article(url, scrapers=scrapers)
        date = scraped_date if scraped_date is not None else getattr(r, "Published", None)
        lines += ["", "---", "", _article_block(
            i,
            title=getattr(r, "Title", "") or "",
            date=date,
            publisher=getattr(r, "Publisher", "") or "",
            url=url,
            text=text,
            summary=getattr(r, "summary", "") or "",
        )]
    return "\n".join(lines) + "\n"


def generate_reports(df: pd.DataFrame, *, order=None, sym_meta=None) -> list[Path]:
    """Scrape each symbol's About articles and write `<symbol>_ai_news_report.md`.

    df       — classified news frame (Symbol, Published, Title, Url, Publisher,
               summary, Relevance, …) — the same frame the Charts news view holds.
    order    — symbols in selection order (default: order of appearance in df).
    sym_meta — {symbol: {company, sector, industry}} for the document heading.

    Returns the written file paths. Overwrites any existing file per symbol.
    """
    df = df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    sym_meta = sym_meta or {}
    if order:
        symbols = list(dict.fromkeys(order))
    elif not df.empty:
        symbols = list(dict.fromkeys(df["Symbol"].tolist()))
    else:
        symbols = []

    out_dir = settings.AI_NEWS_REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    scrapers = _news.build_scrapers()  # one shared throttle (direct + Jina) per batch

    has_rel = (not df.empty) and "Relevance" in df.columns
    written: list[Path] = []
    for sym in symbols:
        sub = df[df["Symbol"] == sym] if not df.empty else df
        about = sub[sub["Relevance"] == "Company"] if has_rel else sub
        company = ((sym_meta.get(sym) or {}).get("company") or "").strip()
        md = _build_markdown(sym, company, about, scrapers)
        path = out_dir / f"{_slug(sym)}_ai_news_report.md"
        path.write_text(md, encoding="utf-8")
        written.append(path)
        log.info("Wrote AI news report %s (%d about-article(s))", path.name, len(about))
    return written
