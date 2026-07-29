"""
The Filter page: what can be filtered, what a filter set matches, and `.filt`
persistence.

`services/filter_engine.py` and `services/filter_registry.py` are already UI-agnostic, so
this router is a thin HTTP face over them — the block model, the operator table
and the evaluation semantics all stay defined in one place.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from api.dialogs import DialogRequest, ask_path
from config import settings
from core.database import Database
from services import filter_text
from services import filter_engine as E
from services import filter_registry as R
from services import output_runs

router = APIRouter(prefix="/api/filter")


def _analysis_mtime() -> float:
    return settings.ANALYSIS_DB.stat().st_mtime if settings.ANALYSIS_DB.exists() else 0.0


@lru_cache(maxsize=2)
def _analysis(_mtime: float) -> pd.DataFrame:
    """The analysis snapshot, cached on the db's mtime so a fresh run invalidates it."""
    if not settings.ANALYSIS_DB.exists():
        return pd.DataFrame()
    with Database(settings.ANALYSIS_DB) as db:
        if not db.table_exists("analysis"):
            return pd.DataFrame()
        return db.read("analysis")


def _frame() -> pd.DataFrame:
    return _analysis(_analysis_mtime())


def _variants(column: str) -> dict[str, Any]:
    """Which comparison variants exist as real columns for a concrete column."""
    peers = R.peer_columns(column)
    return {
        "vs_sector": "vs_sector" in peers,
        "vs_industry": "vs_industry" in peers,
        "score": bool(R.score_column(column)),
    }


@router.get("/registry")
def registry(types: str = Query(default="")) -> dict[str, Any]:
    """Screen types, and the base metrics filterable for the chosen ones.

    Strict intersection across the selected types (unlike the Output column
    picker's union): a block on a metric that doesn't apply to one of the chosen
    types would silently zero out every row of that type.
    """
    chosen = {t.strip() for t in types.split(",") if t.strip()}
    grouped = R.bases_by_category(chosen) if chosen else {}

    categories: list[dict[str, Any]] = []
    for category, bases in grouped.items():
        entries = []
        for base in bases:
            # Variants ride on the CONCRETE column, so for a growth base they
            # depend on the window — each window carries its own availability.
            windows = [
                {"key": key, "label": label, **_variants(f"{base.key}_{key}")}
                for key, label in R.growth_windows(base.key).items()
            ] if base.growth else []
            entries.append(
                {
                    "key": base.key,
                    "label": base.name,
                    "unit": base.unit,
                    "growth": base.growth,
                    "windows": windows,
                    **({} if base.growth else _variants(base.key)),
                }
            )
        categories.append({"category": category, "bases": entries})

    return {
        "screen_types": [
            {"key": key, "label": meta.get("label", key), "help": meta.get("help", "")}
            for key, meta in R.SCREEN_TYPES.items()
        ],
        "categories": categories,
        "operators": [
            {
                "op": op,
                "needs_value": needs_value,
                "needs_second": needs_second,
                "text_only": text_only,
            }
            for op, (needs_value, needs_second, text_only) in E.OPERATORS.items()
        ],
        "categorical_ops": E.CATEGORICAL_OPS,
        "multi_ops": sorted(E.MULTI_OPS),
        "text_ops": sorted(E.TEXT_OPS),
        "default_block": E.new_block(),
    }


@router.get("/categorical")
def categorical(param: str, window: str = "", compare: str = "value") -> dict[str, Any]:
    """Distinct values when the resolved column is low-cardinality enough to pick from."""
    column = E.resolve_column(param, window or None, compare)
    values = E.categorical_values(_frame(), column)
    return {
        "column": column,
        "values": [None if pd.isna(v) else v for v in values] if values is not None else None,
    }


class FilterSet(BaseModel):
    selected_types: list[str] = []
    blocks: list[dict] = []
    comment: str = ""
    ai_instructions: str = ""
    name: str = ""


def _run(payload: FilterSet) -> pd.DataFrame:
    frame = _frame()
    if frame.empty:
        raise HTTPException(status_code=404, detail="analysis.db is empty — run an analysis first")
    return E.run_filter(frame, set(payload.selected_types), payload.blocks)


@router.post("/count")
def count(payload: FilterSet) -> dict[str, Any]:
    """How many symbols the current filter set matches, for the live readout."""
    result = _run(payload)
    # An enabled-but-incomplete block is SKIPPED, not failed — report that, since
    # it explains a count that looks too high.
    incomplete = sum(
        1
        for b in payload.blocks
        if b.get("enabled", True) and not E.is_complete(b) and not b.get("or_children")
    )
    return {"count": int(len(result)), "incomplete_blocks": incomplete}


@router.post("/run")
def run(payload: FilterSet) -> dict[str, Any]:
    """Evaluate and persist a run; the client opens /output?run=<id>."""
    result = _run(payload)
    if result.empty:
        return {"run_id": None, "count": 0}

    param_cols = _param_columns(payload.blocks)
    run_id = output_runs.save_run(
        result,
        screen_types=list(payload.selected_types),
        param_cols=param_cols,
        filter_name=payload.name or None,
        blocks=payload.blocks,
        comment=payload.comment,
        ai_instructions=payload.ai_instructions,
    )
    return {"run_id": run_id, "count": int(len(result))}


def _param_columns(blocks: list[dict]) -> list[str]:
    """The columns the filter actually used, in block order — the Output table's
    starting column set, so a run opens showing what it filtered on."""
    out: list[str] = []
    for block in blocks:
        for candidate in [block, *block.get("or_children", [])]:
            if not candidate.get("enabled", True):
                continue
            column = E.resolve_column(
                str(candidate.get("param", "")),
                candidate.get("window"),
                candidate.get("compare", "value"),
            )
            if column and column not in out:
                out.append(column)
    return out


@router.post("/describe")
def describe(payload: FilterSet) -> dict[str, Any]:
    """The filter set as readable text — the same wording the Output page shows."""
    return {"blocks": filter_text.describe_blocks(payload.blocks)}


# --------------------------------------------------------------------------- #
# .filt files
# --------------------------------------------------------------------------- #
class SaveRequest(FilterSet):
    fake_path: str = ""


@router.post("/save")
async def save(payload: SaveRequest) -> dict[str, Any]:
    path = await ask_path(
        DialogRequest(
            mode="save",
            initial_dir=str(settings.FILTERS_DIR),
            initial_file=payload.name,
            ext=".filt",
            title="Save filter",
            fake_path=payload.fake_path,
        )
    )
    if path is None:
        return {"cancelled": True}

    written = E.save_filterset_to(
        path,
        list(payload.selected_types),
        payload.blocks,
        comment=payload.comment,
        ai_instructions=payload.ai_instructions,
    )
    return {"cancelled": False, "path": str(written), "name": written.stem}


class LoadRequest(BaseModel):
    path: str = ""
    fake_path: str = ""


@router.post("/load")
async def load(payload: LoadRequest) -> dict[str, Any]:
    path = payload.path
    if not path:
        chosen = await ask_path(
            DialogRequest(
                mode="open",
                initial_dir=str(settings.FILTERS_DIR),
                ext=".filt",
                title="Load filter",
                fake_path=payload.fake_path,
            )
        )
        if chosen is None:
            return {"cancelled": True}
        path = chosen

    try:
        data = E.load_filterset_from(path)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"could not read that filter: {exc}") from exc

    from pathlib import Path

    return {"cancelled": False, "path": path, "name": Path(path).stem, **data}
