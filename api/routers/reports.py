"""
PDF report generation for the Output screen.

The client sends what it is showing — selected symbols, visible columns with
their header labels, and the sort order — and the server rebuilds that exact
display frame from the run's parquet before handing it to `reporting`. The
report modules stay unchanged: they render a frame, they never query.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

import reporting
from reporting import store
from services import filter_text
from services import output_runs

router = APIRouter(prefix="/api/reports")


class ColumnSpec(BaseModel):
    key: str
    label: str


class OutputReportRequest(BaseModel):
    run_id: str
    symbols: list[str]  # the selected rows, in display order
    columns: list[ColumnSpec]  # visible columns, in display order
    sort_summary: str = ""


@router.post("/output")
def output_report(req: OutputReportRequest) -> dict[str, Any]:
    loaded = output_runs.load_run(req.run_id)
    if loaded is None:
        raise HTTPException(status_code=404, detail=f"run {req.run_id} not found")
    frame, meta = loaded

    if not req.symbols:
        raise HTTPException(status_code=400, detail="no rows selected")

    order = {s: i for i, s in enumerate(req.symbols)}
    rows = frame[frame["symbol"].isin(order)]
    positions = [order.get(str(s), len(order)) for s in rows["symbol"]]
    rows = rows.iloc[pd.Series(positions).sort_values().index]

    keys = [c.key for c in req.columns if c.key in rows.columns]
    labels = {c.key: c.label for c in req.columns}
    table = pd.DataFrame({labels[k]: rows[k].to_numpy() for k in keys})

    name = meta.get("filter_name") or "output"
    pdf = reporting.generate(
        "output",
        table=table,
        comment=meta.get("comment") or "",
        ai_instructions=meta.get("ai_instructions") or "",
        output_name=name,
        type_labels=filter_text.type_labels(meta.get("screen_types") or []),
        sort_summary=req.sort_summary,
        created_at=str(meta.get("created_at") or ""),
    )
    path = store.save(pdf, name=f"Output-Report-{name}")
    return {"filename": path.name, "bytes": len(pdf)}


@router.get("/{filename}")
def download(filename: str) -> Response:
    """Serve a generated report back for download."""
    matches = [p for p in store.list_reports() if p.name == filename]
    if not matches:
        raise HTTPException(status_code=404, detail=f"report {filename} not found")
    return Response(
        content=matches[0].read_bytes(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
