"""
Saved outputs ("runs") — the Output page's data source.

A run is an immutable parquet + json sidecar pair written by
`ui/output_runs.py`; this router is a thin HTTP face over that module. Row data
is serialised with pandas' own `to_json(orient="split")`: columnar, so column
names aren't repeated per row, and fast enough that the whole frame can ship in
one response — which is what keeps client-side sorting and column toggling
instant (no refetch per interaction).
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from config import settings
from core.database import Database
from services import filter_text
from ui import output_runs

router = APIRouter(prefix="/api")

# Columns every Output table shows regardless of the chosen parameter columns.
IDENTITY_COLUMNS = ["symbol", "name", "sector", "industry", "screen_type", "security_type"]


def _meta_view(meta: dict) -> dict[str, Any]:
    """Sidecar plus the derived bits the UI would otherwise recompute."""
    kind = meta.get("kind", "filter")
    return {
        **meta,
        "kind": kind,
        "type_labels": filter_text.type_labels(meta.get("screen_types") or []),
        "blocks_described": filter_text.describe_blocks(meta.get("blocks") or [])
        if kind == "filter"
        else [],
    }


@router.get("/runs")
def list_runs() -> dict[str, Any]:
    """Every saved output, newest first — sidecars only, no row data."""
    return {"runs": [_meta_view(m) for m in output_runs.list_runs()], "keep": settings.OUTPUT_RUNS_KEEP}


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> Response:
    """Metadata + the full result frame for one run."""
    loaded = output_runs.load_run(run_id)
    if loaded is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    frame, meta = loaded

    table_json = frame.to_json(orient="split", index=False, date_format="iso") or "null"
    payload = f'{{"meta":{json.dumps(_meta_view(meta), default=str)},"table":{table_json}}}'
    return Response(content=payload, media_type="application/json")


class DeleteRunsRequest(BaseModel):
    run_ids: list[str]


@router.post("/runs/delete")
def delete_runs(req: DeleteRunsRequest) -> dict[str, int]:
    return {"deleted": output_runs.delete_runs(req.run_ids)}


class CustomRunRequest(BaseModel):
    name: str
    symbols: list[str]


@router.post("/runs/custom")
def create_custom_run(req: CustomRunRequest) -> dict[str, Any]:
    """Snapshot hand-entered symbols from analysis.db as a run-like output."""
    symbols = [s.strip().upper() for s in req.symbols if s.strip()]
    if not symbols:
        raise HTTPException(status_code=400, detail="no symbols given")

    frame = _read_analysis_rows(symbols)
    if frame.empty:
        raise HTTPException(status_code=404, detail="none of those symbols are in analysis.db")

    type_col = frame["screen_type"] if "screen_type" in frame.columns else pd.Series(dtype=str)
    types = sorted({str(t) for t in type_col.dropna()})
    run_id = output_runs.save_custom_run(
        frame, name=req.name.strip() or "Custom", symbols=symbols, screen_types=types
    )
    missing = [s for s in symbols if s not in set(frame["symbol"])]
    return {"run_id": run_id, "row_count": int(len(frame)), "missing": missing}


def _read_analysis_rows(symbols: list[str]) -> pd.DataFrame:
    """Analysis rows for the given symbols, in the order the caller asked for."""
    with Database(settings.ANALYSIS_DB) as db:
        if not db.table_exists("analysis"):
            return pd.DataFrame()
        placeholders = ",".join("?" * len(symbols))
        frame = db.read("analysis", where=f"symbol IN ({placeholders})", params=symbols)

    if frame.empty:
        return frame
    order = {s: i for i, s in enumerate(symbols)}
    positions = [order.get(str(s), len(order)) for s in frame["symbol"]]
    return frame.iloc[pd.Series(positions).sort_values().index].reset_index(drop=True)


@router.get("/symbols")
def search_symbols(q: str = "", limit: int = Query(default=50, le=500)) -> list[dict[str, Any]]:
    """Symbol/company search for the pickers.

    Deliberately raw SQL with a LIMIT — `read()` would pull every one of
    analysis.db's 240-odd columns for ~38k rows just to show four fields.
    Exact-prefix symbol matches rank above company-name matches.
    """
    with Database(settings.ANALYSIS_DB) as db:
        if not db.table_exists("analysis"):
            return []
        term = q.strip().upper()
        if term:
            sql = (
                "SELECT symbol, name, sector, industry FROM analysis "
                "WHERE UPPER(symbol) LIKE ? OR UPPER(name) LIKE ? "
                "ORDER BY CASE WHEN UPPER(symbol) LIKE ? THEN 0 ELSE 1 END, symbol LIMIT ?"
            )
            params: list[Any] = [f"%{term}%", f"%{term}%", f"{term}%", limit]
        else:
            sql = "SELECT symbol, name, sector, industry FROM analysis ORDER BY symbol LIMIT ?"
            params = [limit]
        frame = pd.DataFrame(db.query(sql, params=params))

    if frame.empty:
        return []
    return [
        {k: (None if pd.isna(v) else v) for k, v in row.items()}
        for row in frame.to_dict(orient="records")
    ]
