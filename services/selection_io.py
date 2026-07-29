"""Selection persistence — save/load a chosen SET of items plus per-item info.

One JSON shape serves two kinds, both stored in the single `settings.SELECTIONS_DIR`
folder (the suffix tells them apart):

  * **symbols** (`.syms`) — a symbol set; per-symbol info = Company / Sector / Industry.
  * **params**  (`.prms`) — an output column set; per-param info = its `param_hints` entry.

File shape (a dict keyed by item; JSON preserves insertion order, so the saved order
is the loaded order)::

    {
      "version": 1,
      "kind": "symbols",
      "saved_at": "2026-06-21T...",
      "items": { "AAPL": {"company": ..., "sector": ..., "industry": ...}, ... }
    }

The item KEYS drive behaviour on load; the info is descriptive metadata (a snapshot —
live data is still re-derived from analysis.db / param_hints). The filename the user
types in the dialog IS the selection's name; there is no separate name field.

This module only reads and writes; CHOOSING the path is the caller's job. The API
pops a native OS dialog out-of-process (`api/dialogs.py`) and hands the path in,
which keeps this importable from a plain script with no UI at all.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from config import settings

# kind -> (file suffix, human label for the dialog title)
_KINDS: dict[str, tuple[str, str]] = {
    "symbols": (".syms", "symbol set"),
    "params": (".prms", "parameter set"),
}


def suffix(kind: str) -> str:
    """File suffix for a selection kind — the caller's dialog uses it as the filter."""
    try:
        return _KINDS[kind][0]
    except KeyError:
        raise ValueError(f"unknown selection kind: {kind!r}") from None


def label(kind: str) -> str:
    """Human name for a selection kind, for dialog titles."""
    try:
        return _KINDS[kind][1]
    except KeyError:
        raise ValueError(f"unknown selection kind: {kind!r}") from None


def symbol_info(symbols: list[str]) -> dict[str, dict]:
    """The canonical per-symbol info for a `.syms` selection:
    ``{symbol: {company, sector, industry}}`` looked up from analysis.db (blanks for
    symbols not present), in the given order. Shared by every symbol Save in the app."""
    import pandas as pd  # local imports keep this module light for non-symbol callers
    from core.database import Database

    blank = {"company": "", "sector": "", "industry": ""}
    info = {s: dict(blank) for s in symbols}
    if symbols and settings.ANALYSIS_DB.exists():
        with Database(settings.ANALYSIS_DB) as db:
            if db.table_exists("analysis"):
                ph = ",".join("?" * len(symbols))
                df = db.read("analysis", where=f"symbol IN ({ph})", params=list(symbols))
                for _, r in df.iterrows():
                    info[str(r["symbol"])] = {
                        "company": str(r["name"]) if "name" in df.columns and pd.notna(r.get("name")) else "",
                        "sector": str(r["sector"]) if "sector" in df.columns and pd.notna(r.get("sector")) else "",
                        "industry": str(r["industry"]) if "industry" in df.columns and pd.notna(r.get("industry")) else "",
                    }
    return info


def save_selection(path: Path | str, *, kind: str, items: Mapping[str, dict]) -> Path:
    """Write a selection to `path` as JSON. `items` is an ordered map item-key -> info."""
    if kind not in _KINDS:
        raise ValueError(f"unknown selection kind: {kind!r}")
    path = Path(path)
    payload = {
        "version": 1,
        "kind": kind,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "items": {str(k): (dict(v) if isinstance(v, Mapping) else {}) for k, v in items.items()},
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_selection(path: Path | str) -> dict:
    """Read a selection file -> {"kind": str, "items": dict[str, dict]}.

    Tolerant of hand-edited files: a bare list of keys (or a dict with non-dict values)
    normalises to ``{key: {}}`` so the keys still load.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):          # bare list of keys
        kind, raw = None, data
    elif isinstance(data, dict):
        kind, raw = data.get("kind"), data.get("items", {})
    else:
        kind, raw = None, {}
    if isinstance(raw, list):
        items = {str(k): {} for k in raw}
    elif isinstance(raw, dict):
        items = {str(k): (dict(v) if isinstance(v, Mapping) else {}) for k, v in raw.items()}
    else:
        items = {}
    return {"kind": kind, "items": items}
