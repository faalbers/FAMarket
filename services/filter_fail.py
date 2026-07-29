"""
Filter Fail diagnostic — why each symbol passed or failed a saved `.filt`.

Reports per block, with the symbol's ACTUAL value beside the threshold it
needed, which is what makes it useful for calibrating a filter. Extracted from
`ui/pages/charts.py`; the output is plain text, so the API can serve it as-is.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from config import settings
from core.database import Database
from ui.filter_engine import _block_mask, is_complete, resolve_column

SEPARATOR = "─" * 32


def load_rows(symbols: list[str]) -> pd.DataFrame:
    if not symbols or not settings.ANALYSIS_DB.exists():
        return pd.DataFrame()
    with Database(settings.ANALYSIS_DB) as db:
        if not db.table_exists("analysis"):
            return pd.DataFrame()
        placeholders = ",".join("?" * len(symbols))
        return db.read("analysis", where=f"symbol IN ({placeholders})", params=symbols)


def _fmt(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        if pd.isna(value):
            return "N/A"
    except (TypeError, ValueError):
        pass
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def _threshold(block: dict) -> str:
    op = block.get("op", "?")
    first, second = block.get("value", ""), block.get("value2", "")
    if op == "between":
        return f"between {first} and {second}"
    if op in ("is null", "is not null"):
        return op
    if op in ("is any of", "is none of"):
        items = list(first) if isinstance(first, (list, tuple)) else ([first] if first else [])
        shown = ", ".join(str(x) for x in items[:5])
        return f"{op} [{shown}{'…' if len(items) > 5 else ''}]"
    return f"{op} {first}"


def block_label(block: dict) -> str:
    """Readable string for one block, including any OR children."""

    def one(b: dict) -> str:
        column = resolve_column(b.get("param", "?"), b.get("window"), b.get("compare", "value"))
        return f"{column} {_threshold(b)}"

    parts: list[str] = []
    if is_complete(block):
        parts.append(one(block))
    for child in block.get("or_children", []):
        if child.get("enabled", True) and is_complete(child):
            parts.append(one(child))
    return "  OR  ".join(parts) if parts else "(no conditions)"


def build_report(symbols: list[str], filter_path: str | Path) -> str:
    """The full diagnostic as plain text."""
    path = Path(filter_path)
    try:
        filterset = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"Could not read {path.name}: {exc}"

    frame = load_rows(symbols)
    selected_types: list[str] = filterset.get("selected_types", [])
    blocks: list[dict] = filterset.get("blocks", [])

    lines: list[str] = [
        "FILTER FAIL REPORT",
        "=" * 40,
        f"Filter:  {path.name}",
        f"Symbols: {len(symbols)} selected",
        "",
    ]

    in_db = set(frame["symbol"].tolist()) if not frame.empty and "symbol" in frame.columns else set()
    not_in_db = [s for s in symbols if s not in in_db]

    if frame.empty:
        lines.append("No analysis data found for selected symbols.")
        if not_in_db:
            lines.append(f"  Symbols not in DB: {', '.join(not_in_db)}")
        return "\n".join(lines)

    if "screen_type" not in frame.columns:
        frame = frame.copy()
        frame["screen_type"] = (
            frame["security_type"] if "security_type" in frame.columns else "standard"
        )

    lines += ["SCOPE", SEPARATOR]
    if selected_types:
        lines.append(f"  Filter targets: {', '.join(selected_types)}")
        in_scope = frame["screen_type"].isin(selected_types)
        scoped = frame[in_scope].copy()
        out_of_scope = frame[~in_scope]
        lines.append(f"  In scope:  {len(scoped)}")
        if not out_of_scope.empty:
            for _, row in out_of_scope.iterrows():
                lines.append(f"    ✗  {row['symbol']:<8}  screen_type = {row['screen_type']}")
    else:
        scoped = frame.copy()
        lines.append("  Filter targets: (all types)")
    if not_in_db:
        lines.append(f"  Not in DB: {', '.join(not_in_db)}")
    lines.append("")

    if scoped.empty:
        lines.append("No selected symbol is in the filter's security-type scope.")
        return "\n".join(lines)

    lines += ["BLOCK BREAKDOWN", SEPARATOR]
    for i, block in enumerate(blocks, start=1):
        if not block.get("enabled", True):
            continue
        label = block_label(block)
        has_own = is_complete(block)
        has_child = any(
            c.get("enabled", True) and is_complete(c) for c in block.get("or_children", [])
        )
        if not has_own and not has_child:
            lines.append(f"[{i}] {label}  [incomplete — skipped]")
            continue

        mask = _block_mask(scoped, block)
        if mask is None:
            lines.append(f"[{i}] {label}  [no conditions]")
            continue

        failed = scoped.loc[~mask, "symbol"].tolist()
        passed = scoped.loc[mask, "symbol"].tolist()
        lines.append(f"[{i}] {label}")

        column = resolve_column(
            block.get("param", "?"), block.get("window"), block.get("compare", "value")
        )
        threshold = _threshold(block)
        has_column = column in scoped.columns

        if not failed:
            lines.append("    All pass")
        else:
            for symbol in failed:
                row = scoped[scoped["symbol"] == symbol]
                actual = (
                    _fmt(row.iloc[0][column])
                    if not row.empty and has_column
                    else ("(column not in DB)" if not has_column else "N/A")
                )
                lines.append(f"    ✗  {symbol:<8}  {column} = {actual}   (needs {threshold})")
            if passed:
                if len(passed) <= 8:
                    shown = []
                    for symbol in passed:
                        row = scoped[scoped["symbol"] == symbol]
                        shown.append(
                            f"{symbol} ({_fmt(row.iloc[0][column])})"
                            if not row.empty and has_column
                            else symbol
                        )
                    lines.append(f"    ✓  {',  '.join(shown)}")
                else:
                    lines.append(f"    ✓  {len(passed)} symbols pass")
        lines.append("")

    return "\n".join(lines)


def list_filters() -> list[dict[str, Any]]:
    """Saved `.filt` files, newest first — so the UI can offer a list as well as a dialog."""
    directory = settings.FILTERS_DIR
    if not directory.exists():
        return []
    files = sorted(directory.glob("*.filt"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.stem, "path": str(p)} for p in files]
