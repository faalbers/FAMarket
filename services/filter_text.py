"""
Readable descriptions of filter blocks and screen types.

Extracted from `ui/pages/output.py` so both the Streamlit page and the API can
describe a saved run's filter without re-implementing the wording. Output is
light markdown (`**bold**`, `*italic*`) — the caller decides how to render it.
"""

from __future__ import annotations

from services import filter_engine as E
from services import filter_registry as R


def type_labels(keys: list[str]) -> str:
    """Screen-type keys -> their human labels, comma-joined."""
    return ", ".join(R.SCREEN_TYPES.get(k, {}).get("label", k) for k in keys)


def describe_block(b: dict) -> str:
    """One filter block as readable text, e.g. `**ROE** (3Y CAGR, vs Sector) > 15`."""
    param = str(b.get("param") or "")
    base = R.BASE_BY_KEY.get(param)
    name = base.name if base else (param or "—")

    quals: list[str] = []
    window = str(b.get("window") or "")
    if window:
        quals.append(R.GROWTH_WINDOWS.get(window, window))
    compare = b.get("compare", "value")
    if compare == "vs_sector":
        quals.append("vs Sector")
    elif compare == "vs_industry":
        quals.append("vs Industry")
    elif compare == "score":
        quals.append("Score")

    op = b.get("op", ">")
    parts = [f"**{name}**" + (f" ({', '.join(quals)})" if quals else ""), op]

    def operand(val_key: str, vmode_key: str) -> str:
        v = b.get(val_key, "")
        if b.get(vmode_key, "V") == "P":
            pb = R.BASE_BY_KEY.get(str(v))
            return f"*{pb.name if pb else v}*"  # compared against another parameter
        return f'"{v}"' if op in E.TEXT_OPS else str(v)

    needs_val, needs_two, _ = E.OPERATORS.get(op, (True, False, False))
    if needs_val:
        parts.append(operand("value", "vmode"))
    if needs_two:
        parts.append(f"and {operand('value2', 'vmode2')}")
    return " ".join(parts)


def describe_blocks(blocks: list[dict]) -> list[dict]:
    """Enabled blocks as `{text, incomplete, children:[…]}`, ready to render.

    Incomplete blocks are reported rather than hidden: the engine SKIPS them, so
    a run can legitimately return more rows than the block list suggests.
    """
    out: list[dict] = []
    for b in blocks or []:
        if not b.get("enabled", True):
            continue
        children = [
            {"text": describe_block(c), "incomplete": not E.is_complete(c)}
            for c in b.get("or_children", [])
            if c.get("enabled", True)
        ]
        out.append(
            {
                "text": describe_block(b),
                "incomplete": not E.is_complete(b),
                "children": children,
            }
        )
    return out
