"""
On-demand news aggregation and the two news reports.

Deliberately NOT part of any fetch run: `data_layer/news.py` is not a
BaseFetcher and writes no database. Every call here hits the network, so
responses are cached briefly in-process — the Polygon free tier allows only
5 requests a minute.
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from typing import Any, AsyncIterator, Callable

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import reporting
from config import settings
from data_layer import news as news_source
from reporting import store
from services import selection_io as SEL

router = APIRouter(prefix="/api/news")

CACHE_SECONDS = 900
_cache: dict[tuple[str, ...], tuple[float, pd.DataFrame, dict[str, dict]]] = {}


def _fetch(
    symbols: list[str],
    on_progress: Callable[[str, int, int], None] | None = None,
) -> tuple[pd.DataFrame, dict[str, dict]]:
    """Classified headlines for `symbols`, cached for CACHE_SECONDS.

    `on_progress` only fires on a cache miss — a cached result returns
    instantly, so there is nothing to report progress on."""
    key = tuple(symbols)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1], hit[2]

    frame = news_source.fetch_news(symbols, settings.NEWS_SOURCES, on_progress=on_progress)
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


def _groups_payload(wanted: list[str], frame: pd.DataFrame, meta: dict[str, dict]) -> dict[str, Any]:
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


@router.get("/stream")
async def news_stream(symbols: str = Query(default="")) -> StreamingResponse:
    """Server-sent events: a `progress` frame per symbol, then one `result` frame
    with the full headlines payload (same shape the old plain GET returned).

    The fetch itself is synchronous and rate-limited (Polygon: 5 req/min), so it
    runs on a worker thread; progress crosses to this async generator over a
    plain thread-safe queue — there's no cross-process run to poll here, unlike
    the fetch-run stream, so nothing needs to touch disk.
    """
    wanted = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not wanted:
        raise HTTPException(status_code=400, detail="no symbols given")

    async def events() -> AsyncIterator[str]:
        q: queue.Queue = queue.Queue()

        def on_progress(symbol: str, done: int, total: int) -> None:
            q.put({"type": "progress", "symbol": symbol, "done": done, "total": total})

        result: dict[str, Any] = {}

        def run() -> None:
            try:
                result["frame"], result["meta"] = _fetch(wanted, on_progress=on_progress)
            except Exception as exc:  # noqa: BLE001 - surface to the stream, not a 500
                result["error"] = str(exc)
            finally:
                q.put(None)

        threading.Thread(target=run, daemon=True).start()

        loop = asyncio.get_event_loop()
        while True:
            item = await loop.run_in_executor(None, q.get)
            if item is None:
                break
            yield f"data: {json.dumps(item)}\n\n"

        if "error" in result:
            yield f"data: {json.dumps({'type': 'error', 'detail': result['error']})}\n\n"
            return
        payload = {"type": "result", **_groups_payload(wanted, result["frame"], result["meta"])}
        yield f"data: {json.dumps(payload, default=str)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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


@router.get("/ai-reports/stream")
async def ai_reports_stream(symbols: str = Query(default="")) -> StreamingResponse:
    """SSE variant of the AI-reports action: a `progress` frame per article
    scraped, then a `result` frame with the written file list.

    Scraping is the slow part (direct fetch + Jina fallback, both
    rate-limited) — see `reporting.ai_news_report.generate_reports` for why
    progress is reported per article rather than per symbol. Same thread+queue
    bridge as `/stream` above.
    """
    wanted = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not wanted:
        raise HTTPException(status_code=400, detail="no symbols given")

    async def events() -> AsyncIterator[str]:
        q: queue.Queue = queue.Queue()

        def on_progress(symbol: str, done: int, total: int) -> None:
            q.put({"type": "progress", "symbol": symbol, "done": done, "total": total})

        result: dict[str, Any] = {}

        def run() -> None:
            try:
                frame, meta = _fetch(wanted, on_progress=on_progress)
                if frame.empty:
                    result["error"] = "no news found for those symbols"
                    return
                from reporting import ai_news_report

                written = ai_news_report.generate_reports(
                    frame, order=wanted, sym_meta=meta, on_progress=on_progress
                )
                result["files"] = [str(p) for p in written] if written else []
            except Exception as exc:  # noqa: BLE001 - surface to the stream, not a 500
                result["error"] = str(exc)
            finally:
                q.put(None)

        threading.Thread(target=run, daemon=True).start()

        loop = asyncio.get_event_loop()
        while True:
            item = await loop.run_in_executor(None, q.get)
            if item is None:
                break
            yield f"data: {json.dumps(item)}\n\n"

        if "error" in result:
            yield f"data: {json.dumps({'type': 'error', 'detail': result['error']})}\n\n"
            return
        payload = {
            "type": "result",
            "files": result["files"],
            "directory": str(settings.AI_NEWS_REPORTS_DIR),
        }
        yield f"data: {json.dumps(payload)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
