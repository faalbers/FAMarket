"""
Column labelling and the column picker's option list.

Extracted from `ui/pages/output.py` so the API can name any analysis.db column
and offer the same picker options the Streamlit page did. Descriptions still
come from `config/param_hints.py` — the one canonical hint registry — via the
base key each column resolves to.
"""

from __future__ import annotations

from typing import Any

from services import filter_registry as R

# Variant suffixes that ride on a base column: peer-relative + the per-metric
# Score (goodness). Stripped to recover the base, with their label appended.
VARIANT_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("_vs_sector", " vs Sector"),
    ("_vs_industry", " vs Industry"),
    ("_goodness", " Score"),
)


def parse(col: str) -> tuple[R.Base, str | None, str] | None:
    """Concrete column -> (base, growth window | None, variant suffix label)."""
    stem, variant = col, ""
    for suffix, text in VARIANT_SUFFIXES:
        if col.endswith(suffix):
            stem, variant = col[: -len(suffix)], text
            break
    if stem in R.BASE_BY_KEY:
        return R.BASE_BY_KEY[stem], None, variant
    for window in R.GROWTH_WINDOWS:
        if stem.endswith(f"_{window}") and stem[: -(len(window) + 1)] in R.BASE_BY_KEY:
            return R.BASE_BY_KEY[stem[: -(len(window) + 1)]], window, variant
    return None


def label(col: str) -> str:
    """Table-header label, e.g. 'Revenue growth · 3Y CAGR vs Sector'."""
    parsed = parse(col)
    if not parsed:
        return col
    base, window, variant = parsed
    return base.name + (f" · {R.GROWTH_WINDOWS[window]}" if window else "") + variant


def describe(col: str) -> dict[str, Any]:
    """Everything the UI needs to render one column: label, unit, hint key."""
    parsed = parse(col)
    if not parsed:
        return {"key": col, "label": col, "category": "", "unit": "", "hint_key": None}
    base, window, variant = parsed
    return {
        "key": col,
        "label": label(col),
        "category": base.category,
        # A Score column is a 0-100 goodness rank, not the base metric's unit.
        "unit": "" if variant == " Score" else base.unit,
        "hint_key": base.key,
        "window": window,
        "variant": variant.strip(),
    }


def options(types: list[str], extra: list[str] | None = None) -> list[dict[str, Any]]:
    """Picker options for a result containing `types`, grouped-ready and ordered.

    Union across types, not the Filter page's strict intersection: a mixed
    result (stocks + a REIT + an ETF) still offers a Standard-only metric — it
    is simply NULL on rows it doesn't apply to, same as any other "not
    applicable" value. `extra` keeps already-chosen columns in the list so a
    variant the filter used never silently drops out.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for category, bases in R.bases_by_category_any(set(types)).items():
        for base in bases:
            if base.growth:
                for window, window_label in R.growth_windows(base.key).items():
                    key = f"{base.key}_{window}"
                    seen.add(key)
                    out.append(
                        {
                            "key": key,
                            "label": f"{base.name} · {window_label}",
                            "category": category,
                            "unit": base.unit,
                            "hint_key": base.key,
                        }
                    )
            else:
                seen.add(base.key)
                out.append(
                    {
                        "key": base.key,
                        "label": base.name,
                        "category": category,
                        "unit": base.unit,
                        "hint_key": base.key,
                    }
                )

    for col in extra or []:
        if col not in seen:
            described = describe(col)
            out.append({**described, "label": described["label"]})
            seen.add(col)
    return out
