"""
Saved selections — `.syms` symbol sets and `.prms` column sets.

Wraps `services/selection_io.py` (the one selection store) and pops the native
file dialog through `api/dialogs.py`. The filename the user types IS the
selection's name, and the dialog opens in `settings.SELECTIONS_DIR`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.dialogs import DialogRequest, ask_path
from config import settings
from config.param_hints import PARAM_HINTS
from services import selection_io as SEL

router = APIRouter(prefix="/api/selections")

Kind = Literal["symbols", "params"]


class SaveRequest(BaseModel):
    kind: Kind
    items: list[str]
    default_name: str = ""
    # Test hook, forwarded to the dialog child so headless checks skip tkinter.
    fake_path: str = ""


class LoadRequest(BaseModel):
    kind: Kind
    fake_path: str = ""


def _info_for(kind: str, items: list[str]) -> dict[str, dict]:
    """Descriptive per-item snapshot stored alongside the keys."""
    if kind == "symbols":
        return SEL.symbol_info(items)
    return {k: dict(PARAM_HINTS.get(k, {})) for k in items}


@router.post("/save")
async def save(req: SaveRequest) -> dict[str, Any]:
    path = await ask_path(
        DialogRequest(
            mode="save",
            initial_dir=str(settings.SELECTIONS_DIR),
            initial_file=req.default_name,
            ext=SEL.suffix(req.kind),
            title=f"Save {SEL.label(req.kind)}",
            fake_path=req.fake_path,
        )
    )
    if path is None:
        return {"cancelled": True}

    written = SEL.save_selection(path, kind=req.kind, items=_info_for(req.kind, req.items))
    return {"cancelled": False, "path": str(written), "name": written.stem, "count": len(req.items)}


@router.post("/load")
async def load(req: LoadRequest) -> dict[str, Any]:
    path = await ask_path(
        DialogRequest(
            mode="open",
            initial_dir=str(settings.SELECTIONS_DIR),
            ext=SEL.suffix(req.kind),
            title=f"Load {SEL.label(req.kind)}",
            fake_path=req.fake_path,
        )
    )
    if path is None:
        return {"cancelled": True}

    try:
        data = SEL.load_selection(path)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"could not read that file: {exc}") from exc

    return {
        "cancelled": False,
        "path": path,
        "name": Path(path).stem,
        "kind": data.get("kind"),
        "items": list(data.get("items", {}).keys()),
        "info": data.get("items", {}),
    }
