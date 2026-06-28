"""
Filter execution + persistence (Topic 5.1 / 5.2).

Turns a **filter set** (the page's AND/OR block model) into a boolean mask over the
analysis.db DataFrame, and reads/writes the `.filt` JSON files.

Block model (one dict per block; the same shape nests one level deep as OR children):

    {
      "enabled": true,
      "param":   "roe",          # base-metric key (filter_registry.BASE_BY_KEY)
      "window":  null,           # growth suffix for growth bases (e.g. "cagr_3y")
      "compare": "value",        # "value" | "vs_sector" | "vs_industry" | "score"
      "op":      ">",            # see OPERATORS
      "vmode":   "V",            # "V" fixed value | "P" another parameter
      "value":   12,             # number / text, or a base-key when vmode == "P"
      "vmode2":  "V", "value2": 30,   # second operand, only for op == "between"
      "or_children": [ { ...block without or_children... } ]
    }

Semantics (ROADMAP 5.1):
  * top-level blocks are ANDed; a disabled block/child is skipped;
  * a block passes when its own condition is true OR any enabled OR-child is true
    (children are fallbacks);
  * NULL fails every operator except `is null` / `is not null`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from config import settings
from ui import filter_registry as R

# Operators -> (needs a value?, needs a second value?, is text-only?). The V/P
# button is hidden for text-only operators and the null operators.
OPERATORS: dict[str, tuple[bool, bool, bool]] = {
    ">": (True, False, False),
    "<": (True, False, False),
    ">=": (True, False, False),
    "<=": (True, False, False),
    "=": (True, False, False),
    "!=": (True, False, False),
    "between": (True, True, False),
    "is null": (False, False, False),
    "is not null": (False, False, False),
    "starts_with": (True, False, True),
    "contains": (True, False, True),
    # Membership over a low-cardinality column; `value` is a LIST of picks (not a
    # scalar). Offered by the page only when the column is detected categorical.
    "is any of": (True, False, False),
    "is none of": (True, False, False),
}
TEXT_OPS = {op for op, (_, _, t) in OPERATORS.items() if t}
NULL_OPS = {"is null", "is not null"}
# Operators whose `value` is a list of picks (the V/P toggle is hidden for them).
MULTI_OPS = {"is any of", "is none of"}
# Operator choices for a categorical column (membership + null tests only).
CATEGORICAL_OPS = ["is any of", "is none of", "is null", "is not null"]


def new_block() -> dict:
    """A fresh default block (first base, '>' , empty value, active)."""
    return {
        "enabled": True, "param": R.BASES[0].key, "window": None, "compare": "value",
        "op": ">", "vmode": "V", "value": "", "vmode2": "V", "value2": "",
        "or_children": [],
    }


# --------------------------------------------------------------------------- #
# Column resolution
# --------------------------------------------------------------------------- #
def resolve_column(param: str, window: str | None, compare: str) -> str:
    """(base, growth-window, compare) -> concrete analysis.db column name.

    compare: "value" (raw), "vs_sector"/"vs_industry" (peer-relative), or "score"
    (the per-metric 0-100 goodness column the analysis layer stores).
    """
    base = R.BASE_BY_KEY.get(param)
    col = f"{param}_{window}" if (base and base.growth and window) else param
    if compare in ("vs_sector", "vs_industry"):
        return f"{col}_{compare}"
    if compare == "score":
        return f"{col}_goodness"
    return col


def categorical_values(df: pd.DataFrame, column: str) -> list | None:
    """Distinct values of `column` when it is low-cardinality enough to multi-pick.

    Returns the sorted distinct non-null values when their count is within the cap
    for the column's dtype, else None (caller keeps the normal value box). Two caps
    so continuous numerics stay range filters: a generous one for text/classification
    columns, a small one for numerics (0-100 scores / 1-99 ranks stay >, <, between;
    only tiny numeric enums like a 0/1 flag become a pick list).
    """
    if column not in df.columns:
        return None
    s = df[column].dropna()
    if s.empty:
        return None
    numeric = pd.api.types.is_numeric_dtype(s)
    cap = (settings.FILTER_CATEGORICAL_MAX_UNIQUE_NUMERIC if numeric
           else settings.FILTER_CATEGORICAL_MAX_UNIQUE)
    uniq = s.unique().tolist()
    if not (1 <= len(uniq) <= cap):
        return None
    return sorted(uniq)


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #
def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _operand(df: pd.DataFrame, block: dict, which: str) -> pd.Series | float | str | None:
    """The right-hand operand: a column (P mode) or a literal (V mode)."""
    vmode = block.get("vmode2" if which == "2" else "vmode", "V")
    raw = block.get("value2" if which == "2" else "value", "")
    if vmode == "P":  # reference another parameter's value column
        col = resolve_column(str(raw), None, "value")
        return _num(df[col]) if col in df.columns else pd.Series(float("nan"), index=df.index)
    return raw


def _condition(df: pd.DataFrame, block: dict) -> pd.Series:
    """Boolean mask for a single block's own condition (ignores its children)."""
    op = block.get("op", ">")
    col = resolve_column(block["param"], block.get("window"), block.get("compare", "value"))
    if col not in df.columns:
        return pd.Series(False, index=df.index)
    series = df[col]

    if op == "is null":
        return series.isna()
    if op == "is not null":
        return series.notna()

    if op in MULTI_OPS:
        raw = block.get("value")
        vals = list(raw) if isinstance(raw, (list, tuple)) else ([raw] if raw not in (None, "") else [])
        if not vals:
            return pd.Series(False, index=df.index)
        if pd.api.types.is_numeric_dtype(series):
            picks = {v for v in (pd.to_numeric(pd.Series(vals), errors="coerce")).tolist() if pd.notna(v)}
            hit = _num(series).isin(picks)
        else:
            picks = {str(v).lower() for v in vals}
            hit = series.astype("string").str.lower().isin(picks)
        hit = hit.fillna(False)
        # "is none of" excludes the picks but, like !=, NULLs still fail.
        return hit if op == "is any of" else (~hit & series.notna())

    if op in TEXT_OPS:
        text = series.astype("string").str.lower()
        needle = str(block.get("value", "")).lower()
        if not needle:
            return pd.Series(False, index=df.index)
        hit = text.str.startswith(needle) if op == "starts_with" else text.str.contains(needle, regex=False)
        return hit.fillna(False)

    if op == "between":
        lo, hi = _operand(df, block, "1"), _operand(df, block, "2")
        col_num = _num(series)
        lo = _num(lo) if isinstance(lo, pd.Series) else pd.to_numeric(lo, errors="coerce")
        hi = _num(hi) if isinstance(hi, pd.Series) else pd.to_numeric(hi, errors="coerce")
        return (col_num >= lo) & (col_num <= hi)

    rhs = _operand(df, block, "1")
    # Text equality (e.g. trend = "up") when the column isn't numeric and the RHS is a literal.
    if op in ("=", "!=") and not isinstance(rhs, pd.Series) and _num(series).isna().all() and str(rhs) != "":
        eq = series.astype("string").str.lower() == str(rhs).lower()
        eq = eq.fillna(False)
        return eq if op == "=" else (~eq & series.notna())

    lhs = _num(series)
    rhs_num = rhs if isinstance(rhs, pd.Series) else pd.to_numeric(rhs, errors="coerce")
    ops = {
        ">": lhs > rhs_num, "<": lhs < rhs_num, ">=": lhs >= rhs_num,
        "<=": lhs <= rhs_num, "=": lhs == rhs_num, "!=": lhs != rhs_num,
    }
    mask = ops[op]
    # A NaN comparison already yields False — that's the "NULL fails" rule, except
    # != which pandas would make True against NaN; force NULLs to fail there too.
    return mask & lhs.notna()


def is_complete(block: dict) -> bool:
    """Whether a block has enough filled in to evaluate.

    An enabled-but-incomplete block (e.g. an operator that needs a value the user
    hasn't typed yet) is *skipped* rather than failing every row — so adding a fresh
    block doesn't wipe the result set.
    """
    op = block.get("op", ">")
    if op in MULTI_OPS:  # value is a list of picks; ready once at least one is chosen
        v = block.get("value")
        return isinstance(v, (list, tuple)) and len(v) > 0
    needs_val, needs_two, _ = OPERATORS.get(op, (True, False, False))
    if not needs_val:
        return True

    def filled(vmode_key: str, val_key: str) -> bool:
        if block.get(vmode_key, "V") == "P":
            # must reference a real parameter; an unset/leftover value (e.g. text
            # typed while in V mode) means the block isn't ready to evaluate
            return str(block.get(val_key, "")) in R.BASE_BY_KEY
        return str(block.get(val_key, "")).strip() != ""

    if not filled("vmode", "value"):
        return False
    if needs_two and not filled("vmode2", "value2"):
        return False
    return True


def _block_mask(df: pd.DataFrame, block: dict) -> pd.Series | None:
    """A block passes when its own condition OR any enabled OR-child passes.

    Incomplete conditions (parent or child) are ignored. Returns None when the block
    contributes nothing (so the caller can skip it instead of ANDing in all-False).
    """
    masks: list[pd.Series] = []
    if is_complete(block):
        masks.append(_condition(df, block))
    for child in block.get("or_children", []):
        if child.get("enabled", True) and is_complete(child):
            masks.append(_condition(df, child))
    if not masks:
        return None
    combined = masks[0]
    for m in masks[1:]:
        combined = combined | m
    return combined


def evaluate(df: pd.DataFrame, filterset: list[dict]) -> pd.Series:
    """AND together every enabled, complete top-level block. None -> all rows pass."""
    mask = pd.Series(True, index=df.index)
    for block in filterset:
        if not block.get("enabled", True):
            continue
        bm = _block_mask(df, block)
        if bm is not None:
            mask = mask & bm
    return mask


def run_filter(df: pd.DataFrame, selected_types: set[str], filterset: list[dict]) -> pd.DataFrame:
    """Restrict to the selected screen types, then apply the filter blocks.

    Adds a `screen_type` column (from the sector/industry classifier) used both for
    the type restriction and for the Output label.
    """
    if df.empty:
        return df
    out = df.copy()
    # Prefer the canonical column written by the analysis layer; fall back to runtime
    # classification for an older analysis.db that predates it.
    if "screen_type" not in out.columns or out["screen_type"].isna().any():
        out["screen_type"] = [
            R.classify(st, sec, ind)
            for st, sec, ind in zip(out.get("security_type"), out.get("sector"), out.get("industry"))
        ]
    if selected_types:
        out = out[out["screen_type"].isin(selected_types)]
    if out.empty:
        return out
    return out[evaluate(out, filterset)]


# --------------------------------------------------------------------------- #
# .filt persistence (plain JSON, hand-editable — ROADMAP 5.2)
# Files are chosen via the native OS dialog (ui/file_io), so these take a full path
# rather than a name in a fixed folder.
# --------------------------------------------------------------------------- #
def _clean_blocks(blocks: list[dict]) -> list[dict]:
    """Copy of the block list without the UI-only `_id` fields (added on load by the
    Filter page for widget tracking; never part of the saved file)."""
    out = []
    for b in blocks:
        nb = {k: v for k, v in b.items() if k != "_id"}
        if "or_children" in nb:
            nb["or_children"] = [
                {k: v for k, v in c.items() if k != "_id"} for c in nb["or_children"]
            ]
        out.append(nb)
    return out


def save_filterset_to(path: Path | str, selected_types: list[str],
                      filterset: list[dict], comment: str = "",
                      ai_instructions: str = "") -> Path:
    """Write a filter set to `path` as .filt JSON (overwrites).

    `comment` is a free-text note (what the filter does / how to tweak / how to sort)
    shown on the Filter + Output pages; the make_filters skill auto-fills it.

    `ai_instructions` is the filter's source spec / provenance (the verbatim plain-English
    instructions the make_filters skill built it from, or a manual-build note) — shown
    read-only & collapsed under Comment on the Filter + Output pages, distinct from `comment`.
    """
    path = Path(path)
    payload = {
        "version": 1,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "selected_types": list(selected_types),
        "comment": comment or "",
        "ai_instructions": ai_instructions or "",
        "blocks": _clean_blocks(filterset),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_filterset_from(path: Path | str) -> dict:
    """Read a .filt file -> {"selected_types", "comment", "ai_instructions", "blocks"}.

    `comment` and `ai_instructions` default to "" for older files written before those
    fields existed.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        "selected_types": data.get("selected_types", []),
        "comment": data.get("comment", ""),
        "ai_instructions": data.get("ai_instructions", ""),
        "blocks": data.get("blocks", []),
    }
