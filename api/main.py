"""
FastAPI application for the React UI.

Boot order mirrors `app.py` (the Streamlit entry point): runtime dirs, then
`core.net.configure_tls()` BEFORE anything can make a network call (this machine
sits behind TLS interception — see CLAUDE.md), then logging.

The built Vite frontend is served from this same process, so one command runs
everything (`python scripts/serve_ui.py`). The SPA fallback is registered after
every /api route so client-side deep links (`/output?run=…`, `/charts?view=…`)
survive a cold load.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from core.logging_config import setup_logging
from core.net import configure_tls

from . import dialogs, lifecycle
from .routers import (
    charts,
    columns,
    fetch,
    filters,
    indices,
    meta,
    news,
    reports,
    runs,
    scoring,
    selections,
    settings_router,
    utilities,
)

settings.ensure_runtime_dirs()
configure_tls()
setup_logging(settings.APP_LOG_FILE)

app = FastAPI(title="FAMarket API")

# The Vite dev server runs on its own port; nothing here ever leaves localhost.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

app.include_router(meta.router)
app.include_router(fetch.router)
app.include_router(runs.router)
app.include_router(charts.router)
app.include_router(filters.router)
app.include_router(columns.router)
app.include_router(news.router)
app.include_router(indices.router)
app.include_router(scoring.router)
app.include_router(settings_router.router)
app.include_router(utilities.router)
app.include_router(selections.router)
app.include_router(reports.router)
app.include_router(dialogs.router)
app.include_router(lifecycle.router)

DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"

if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        """Real files served as-is; everything else gets index.html."""
        if path.startswith("api/"):
            raise HTTPException(status_code=404)
        candidate = (DIST / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(DIST):
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")
