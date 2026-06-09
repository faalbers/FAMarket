"""Write edited values back into config/settings.py (Settings-page persistence).

The Settings UI and hand-edits share one file (ROADMAP Topic 3.4). To round-trip a
UI edit we rewrite ONLY the value expression of the target constant, located via
the AST and replaced by its character span — so every comment, blank line and
layout choice in settings.py survives (a line's trailing comment sits outside the
value node's span). Nested dict/tuple leaves are addressed with dotted paths:
``OVERALL_SCORE_WEIGHTS.quality``, ``CATEGORY_METRIC_WEIGHTS.value.pe``,
``RATE_LIMITS.yfinance``.

Safety, in order:
  1. Locate every target's value node; abort if any path is unknown.
  2. Apply span replacements right-to-left so earlier offsets stay valid.
  3. Re-compile AND exec the new source in a throwaway namespace, asserting every
     target now holds its new value — BEFORE touching disk.
  4. Take a versioned backup of settings.py (same rotating scheme as the DBs).
  5. Write the file, then update the live `settings` module in place so the change
     takes effect this session; the file write is what persists it across restarts.
"""

from __future__ import annotations

import ast
from pathlib import Path

from config import settings
from core.backup import backup_file

SETTINGS_PATH = Path(settings.__file__)


class SettingsWriteError(RuntimeError):
    """Raised when an edit can't be located or the rewrite fails verification."""


def _line_offsets(source: str) -> list[int]:
    """Absolute char offset of the start of each line (1-based line -> index)."""
    offsets, total = [], 0
    for line in source.splitlines(keepends=True):
        offsets.append(total)
        total += len(line)
    offsets.append(total)
    return offsets


def _span(offsets: list[int], node: ast.AST) -> tuple[int, int]:
    start = offsets[node.lineno - 1] + node.col_offset
    end = offsets[node.end_lineno - 1] + node.end_col_offset
    return start, end


def _top_value_node(tree: ast.Module, name: str) -> ast.AST | None:
    """The value expression assigned to top-level constant `name` (handles the
    ``A, B = 1, 2`` tuple-assignment form too)."""
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return node.value
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    return node.value
                if isinstance(tgt, ast.Tuple) and isinstance(node.value, ast.Tuple):
                    for elt, val in zip(tgt.elts, node.value.elts):
                        if isinstance(elt, ast.Name) and elt.id == name:
                            return val
    return None


def _descend(node: ast.AST, key: str) -> ast.AST | None:
    """The value node for `key` inside a dict-literal node, else None."""
    if not isinstance(node, ast.Dict):
        return None
    for k, v in zip(node.keys, node.values):
        if isinstance(k, ast.Constant) and k.value == key:
            return v
    return None


def _value_node(tree: ast.Module, path: str) -> ast.AST:
    parts = path.split(".")
    node = _top_value_node(tree, parts[0])
    if node is None:
        raise SettingsWriteError(f"Setting not found: {parts[0]}")
    for key in parts[1:]:
        child = _descend(node, key)
        if child is None:
            raise SettingsWriteError(f"Key '{key}' not found in {path}")
        node = child
    return node


def _format(value: object) -> str:
    """Source text for a new value. Strings use double quotes; tuples stay tuples."""
    if isinstance(value, bool):
        return repr(value)  # before int check — bool is an int subclass
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, tuple):
        inner = ", ".join(_format(v) for v in value)
        return f"({inner},)" if len(value) == 1 else f"({inner})"
    return repr(value)


def _verify(new_source: str, updates: dict[str, object]) -> None:
    """Compile + exec the rewritten source and confirm every target took effect."""
    ns: dict = {"__file__": str(SETTINGS_PATH)}
    try:
        exec(compile(new_source, str(SETTINGS_PATH), "exec"), ns)  # noqa: S102
    except SyntaxError as exc:
        raise SettingsWriteError(f"Rewrite produced invalid Python: {exc}") from exc
    for path, want in updates.items():
        parts = path.split(".")
        cur = ns.get(parts[0])
        for key in parts[1:]:
            cur = cur[key]
        if isinstance(want, tuple):
            cur = tuple(cur)
        if cur != want:
            raise SettingsWriteError(
                f"Verification failed for {path}: expected {want!r}, file has {cur!r}"
            )


def _apply_live(updates: dict[str, object]) -> None:
    """Mutate the imported `settings` module so the change is live this session."""
    for path, value in updates.items():
        parts = path.split(".")
        if len(parts) == 1:
            setattr(settings, parts[0], value)
            continue
        container = getattr(settings, parts[0])
        for key in parts[1:-1]:
            container = container[key]
        container[parts[-1]] = value


def update_settings(updates: dict[str, object]) -> None:
    """Persist `{dotted_path: new_value}` into settings.py (see module docstring)."""
    if not updates:
        return
    source = SETTINGS_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    offsets = _line_offsets(source)

    spans = []
    for path, value in updates.items():
        start, end = _span(offsets, _value_node(tree, path))
        spans.append((start, end, _format(value)))

    spans.sort(key=lambda s: s[0], reverse=True)  # right-to-left keeps offsets valid
    new_source = source
    for start, end, text in spans:
        new_source = new_source[:start] + text + new_source[end:]

    _verify(new_source, updates)

    if SETTINGS_PATH.exists():
        backup_file(SETTINGS_PATH)  # dated backup, same scheme as the databases
    SETTINGS_PATH.write_text(new_source, encoding="utf-8")
    _apply_live(updates)
