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

Files are chosen through the app's native dialog (`ui/file_io`), so this only works on
the user's own desktop (see file_io's note). Pages import this as
`from ui import selection_io as SEL`.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from config import settings
from ui import file_io as FIO

# kind -> (file suffix, human label for the dialog title)
_KINDS: dict[str, tuple[str, str]] = {
    "symbols": (".syms", "symbol set"),
    "params": (".prms", "parameter set"),
}


def _suffix(kind: str) -> str:
    try:
        return _KINDS[kind][0]
    except KeyError:
        raise ValueError(f"unknown selection kind: {kind!r}") from None


def _filetypes(kind: str) -> list[tuple[str, str]]:
    suffix, label = _KINDS[kind]
    return [(f"{label.title()} files", f"*{suffix}"), ("All files", "*.*")]


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


# --------------------------------------------------------------------------- #
# Native-dialog wrappers (the user types the filename — no pre-naming)
# --------------------------------------------------------------------------- #
def save_dialog(*, kind: str, items: Mapping[str, dict], default_name: str = "") -> Path | None:
    """Pop a Save-As dialog in SELECTIONS_DIR, write the selection, return the path
    (or None if cancelled). `default_name` pre-fills the dialog's filename (the suffix
    is appended automatically)."""
    suffix = _suffix(kind)
    path = FIO.ask_save_path(
        initialdir=settings.SELECTIONS_DIR,
        default_name=default_name,
        defaultextension=suffix,
        filetypes=_filetypes(kind),
        title=f"Save {_KINDS[kind][1]}",
    )
    if path is None:
        return None
    return save_selection(path, kind=kind, items=items)


def load_dialog(*, kind: str) -> dict | None:
    """Pop an Open dialog in SELECTIONS_DIR and return
    {"kind", "items", "path"} for the chosen file, or None if cancelled."""
    path = FIO.ask_open_path(
        initialdir=settings.SELECTIONS_DIR,
        filetypes=_filetypes(kind),
        title=f"Load {_KINDS[kind][1]}",
    )
    if path is None:
        return None
    out = load_selection(path)
    out["path"] = path
    return out
