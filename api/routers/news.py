"""
On-demand news aggregation and the two news reports.

Deliberately NOT part of any fetch run: `data_layer/news.py` is not a
BaseFetcher and writes no database. Every call here hits the network, so
responses are cached briefly in-process — the Polygon free tier allows only
5 requests a minute.
"""

from __future__ import annotations

import time
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import reporting
from config import settings
from data_layer import news as news_source
from reporting import store
from ui import selection_io as SEL

router = APIRouter(prefix="/api/news")

CACHE_SECONDS = 900
_cache: dict[tuple[str, ...], tuple[float, pd.DataFrame, dict[str, dict]]] = {}


def _fetch(symbols: list[str]) -> tuple[pd.DataFrame, dict[str, dict]]:
    """Classified headlines for `symbols`, cached for CACHE_SECONDS."""
    key = tuple(symbols)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1], hit[2]

    frame = news_source.fetch_news(symbols, settings.NEWS_SOURCES)
    meta = SEL.symbol_info(symbols)
    if not frame.empty:
        frame = news_source.classify_relevance(frame, meta)
    _cache[key] = (time.time(), frame, meta)
    return frame, meta


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    shown = [
        c
        for c in ("Symbol", "Published", "Title", "Url", "Publisher", "Sources", "Sentiment", "Relevance")
        if c in frame.columns
    ]
    if not shown:
        return []
    return [
        {k: (None if pd.isna(v) else (str(v) if k == "Published" else v)) for k, v in row.items()}
        for row in frame[shown].to_dict(orient="records")
    ]


@router.get("")
def headlines(symbols: str = Query(default="")) -> dict[str, Any]:
    """Per-symbol headlines, split into company-specific and broader context."""
    wanted = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not wanted:
        raise HTTPException(status_code=400, detail="no symbols given")

    frame, meta = _fetch(wanted)
    if frame.empty:
        return {"symbols": wanted, "groups": [], "sources": list(settings.NEWS_SOURCES)}

    records = _records(frame)
    groups = []
    for symbol in wanted:
        rows = [r for r in records if r.get("Symbol") == symbol]
        groups.append(
            {
                "symbol": symbol,
                "company": (meta.get(symbol) or {}).get("company", ""),
                "company_news": [r for r in rows if r.get("Relevance") == "Company"],
                "context_news": [r for r in rows if r.get("Relevance") != "Company"],
            }
        )
    return {"symbols": wanted, "groups": groups, "sources": list(settings.NEWS_SOURCES)}


class ReportRequest(BaseModel):
    symbols: list[str]


@router.post("/pdf")
def news_pdf(req: ReportRequest) -> dict[str, Any]:
    """Headlines as a PDF, one page per symbol."""
    wanted = [s.strip().upper() for s in req.symbols if s.strip()]
    frame, meta = _fetch(wanted)
    if frame.empty:
        raise HTTPException(status_code=404, detail="no news found for those symbols")

    pdf = reporting.generate(
        "news",
        df=frame,
        sources=list(settings.NEWS_SOURCES),
        order=wanted,
        sym_meta=meta,
        title="News report",
    )
    path = store.save(pdf, name="News-Report")
    return {"filename": path.name, "bytes": len(pdf)}


@router.post("/ai-reports")
def ai_reports(req: ReportRequest) -> dict[str, Any]:
    """Scrape article bodies into per-symbol markdown for the summary skill.

    This is slow — it fetches every article — so the caller should expect a
    long-running request.
    """
    wanted = [s.strip().upper() for s in req.symbols if s.strip()]
    frame, meta = _fetch(wanted)
    if frame.empty:
        raise HTTPException(status_code=404, detail="no news found for those symbols")

    from reporting import ai_news_report

    written = ai_news_report.generate_reports(frame, order=wanted, sym_meta=meta)
    return {
        "files": [str(p) for p in written] if written else [],
        "directory": str(settings.AI_NEWS_REPORTS_DIR),
    }
