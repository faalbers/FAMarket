"""
Structural validator for FAMarket .filt filter sets.

    python -m scripts.validate_filt filters/my_screen.filt [more.filt ...]
    python -m scripts.validate_filt --all            # every .filt in FILTERS_DIR

Catches the failure modes the filter engine cannot report itself (it silently
returns all-False or skips a block): unknown/typo'd params, params that don't
apply to every selected screen type, missing/invalid growth windows, resolved
columns absent from analysis.db, incomplete blocks, and unit-mangled thresholds
(the $-in-millions / %-as-a-fraction class of bug). Also warns when a known N/A
trap (.claude/docs/screening_system.md §7) has no fallback OR-child.

ERRORS   = the engine would mis-evaluate the filter; fix before saving.
warnings = suspicious but possibly intended; double-check.

Importable for pre-save use (the /make_filters validate + dry-run session):

    from scripts.validate_filt import validate_payload
    errors, warnings = validate_payload(payload)     # payload = the .filt dict shape
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from config import settings
from ui import filter_engine as FE
from ui import filter_registry as R

_COMPARES = ("value", "vs_sector", "vs_industry", "score")
_CMP_OPS = {">", "<", ">=", "<=", "=", "!=", "between"}


def _num(v: object) -> float | None:
    """Best-effort numeric view of a JSON literal (None when not numeric)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace("_", "").strip())
        except ValueError:
            return None
    return None


def _check_block(block: dict[str, Any], types: set[str], cols: frozenset[str],
                 label: str, errors: list[str], warns: list[str],
                 top_level: bool) -> None:
    """Validate one block (or OR-child) in place, appending findings."""
    param = str(block.get("param", ""))
    base = R.BASE_BY_KEY.get(param)
    if base is None:
        errors.append(f"{label}: unknown param '{param}' — the engine returns "
                      "all-False for a missing column")
        return
    if not block.get("enabled", True):
        warns.append(f"{label}: disabled — ignored at run time")
    if not top_level and block.get("or_children"):
        warns.append(f"{label}: nested or_children are ignored by the engine")

    missing = types - base.applies
    if missing:
        errors.append(f"{label}: '{param}' does not apply to selected type(s) "
                      f"{sorted(missing)} — those rows are NULL and always fail")

    window = block.get("window")
    if base.growth:
        wins = R.growth_windows(param)
        if not window:
            errors.append(f"{label}: growth base '{param}' needs a window "
                          f"({', '.join(wins)})")
        elif window not in wins:
            errors.append(f"{label}: window '{window}' not available for "
                          f"'{param}' ({', '.join(wins)})")
    elif window:
        warns.append(f"{label}: window '{window}' is ignored — '{param}' is not "
                     "a growth base")

    compare = str(block.get("compare", "value"))
    if compare not in _COMPARES:
        errors.append(f"{label}: unknown compare '{compare}'")

    op = str(block.get("op", ">"))
    if op not in FE.OPERATORS:
        errors.append(f"{label}: unknown operator '{op}'")
        return

    if op in FE.MULTI_OPS:
        v = block.get("value")
        if not isinstance(v, (list, tuple)) or not v:
            errors.append(f"{label}: '{op}' needs a non-empty LIST of picks in 'value'")
    elif not FE.is_complete(block):
        errors.append(f"{label}: incomplete (missing value/operand) — the engine "
                      "silently SKIPS it")

    # P-mode operands must reference a real param whose value column exists.
    for vk, mk in (("value", "vmode"), ("value2", "vmode2")):
        if block.get(mk, "V") == "P":
            ref = str(block.get(vk, ""))
            if ref not in R.BASE_BY_KEY:
                errors.append(f"{label}: P-mode {vk} '{ref}' is not a known param")
            elif cols and ref not in cols:
                errors.append(f"{label}: P-mode {vk} column '{ref}' not in analysis.db")

    # The block's own resolved column must exist (when analysis.db is available).
    if compare in _COMPARES:
        col = FE.resolve_column(param, window if base.growth else None, compare)
        if cols and col not in cols:
            errors.append(f"{label}: column '{col}' not in analysis.db — the engine "
                          "returns all-False")

    # Unit-scale heuristics (V-mode numeric literals only).
    if op in _CMP_OPS:
        for vk, mk in (("value", "vmode"), ("value2", "vmode2")):
            if vk == "value2" and op != "between":
                continue
            if block.get(mk, "V") != "V":
                continue
            v = _num(block.get(vk))
            if v is None:
                continue
            if base.unit == "%" and compare == "value" and 0 < abs(v) < 1:
                warns.append(f"{label}: {vk}={v:g} looks like a FRACTION — '%' "
                             "params store percent-numbers (12.5 not 0.125)")
            if param == "market_cap" and 0 < v < 1_000_000:
                warns.append(f"{label}: market_cap {vk}={v:g} — unit is RAW "
                             "DOLLARS ($1B = 1_000_000_000)")
        if op == "between":
            lo, hi = _num(block.get("value")), _num(block.get("value2"))
            if lo is not None and hi is not None and lo > hi:
                warns.append(f"{label}: between {lo:g} and {hi:g} — bounds "
                             "reversed, matches nothing")

    # Known N/A traps (screening_system.md §7) — checked on top-level blocks only.
    if top_level and op in _CMP_OPS:
        children = [c for c in block.get("or_children", [])
                    if c.get("enabled", True)]
        child_keys = {(str(c.get("param")), c.get("window")) for c in children}
        child_params = {p for p, _ in child_keys}
        child_ops = {str(c.get("op")) for c in children}
        win = str(window or "")
        if param == "eps" and win.startswith("cagr") \
                and "forward_eps_growth" not in child_params:
            warns.append(f"{label}: eps_{win} has no 'forward_eps_growth' OR-child "
                         "— loss-to-profit names N/A out (§7)")
        if win == "cagr_5y" and (param, "cagr_3y") not in child_keys:
            warns.append(f"{label}: {param}_cagr_5y has no {param}_cagr_3y "
                         "OR-child — short-history names N/A out (§7)")
        if param == "altman_z" and "is null" not in child_ops:
            warns.append(f"{label}: altman_z has no 'is null' OR-child — "
                         "asset-light tech N/A out (§7)")


def validate_payload(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Validate a .filt-shaped dict. Returns (errors, warnings)."""
    errors: list[str] = []
    warns: list[str] = []

    types = {str(t) for t in (payload.get("selected_types") or [])}
    unknown = types - set(R.SCREEN_TYPES)
    if not types:
        errors.append("selected_types is empty — the filter matches nothing")
    if unknown:
        errors.append(f"unknown selected_types: {sorted(unknown)}")

    blocks = payload.get("blocks") or []
    if not blocks:
        errors.append("blocks is empty")
    if not str(payload.get("comment", "")).strip():
        warns.append("comment is empty — fill the usage writeup")
    if not str(payload.get("ai_instructions", "")).strip():
        warns.append("ai_instructions is empty — fill the verbatim source spec")

    cols = R.analysis_columns()
    if not cols:
        warns.append("analysis.db not found — column-existence checks skipped")

    known_types = types & set(R.SCREEN_TYPES)
    for i, b in enumerate(blocks, 1):
        label = f"block {i} ({b.get('param', '?')})"
        _check_block(b, known_types, cols, label, errors, warns, top_level=True)
        for j, c in enumerate(b.get("or_children") or [], 1):
            clabel = f"block {i} OR-child {j} ({c.get('param', '?')})"
            _check_block(c, known_types, cols, clabel, errors, warns,
                         top_level=False)
    return errors, warns


def validate_file(path: Path) -> tuple[list[str], list[str]]:
    """Load and validate one .filt file. Returns (errors, warnings)."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"unreadable: {exc}"], []
    if not isinstance(payload, dict):
        return ["not a JSON object"], []
    return validate_payload(payload)


def main() -> None:
    p = argparse.ArgumentParser(description="Validate .filt filter-set files")
    p.add_argument("paths", nargs="*", help=".filt files to validate")
    p.add_argument("--all", action="store_true",
                   help=f"validate every .filt in {settings.FILTERS_DIR}")
    args = p.parse_args()

    paths = [Path(s) for s in args.paths]
    if args.all:
        paths += sorted(settings.FILTERS_DIR.glob("*.filt"))
    if not paths:
        p.error("pass one or more .filt paths, or --all")

    failed = False
    for path in paths:
        errors, warns = validate_file(path)
        status = "FAIL" if errors else ("warn" if warns else "OK")
        print(f"{path.name}: {status}")
        for e in errors:
            print(f"  ERROR: {e}")
        for w in warns:
            print(f"  warn : {w}")
        failed = failed or bool(errors)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
