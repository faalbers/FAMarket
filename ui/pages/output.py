"""
Output page (Topic 6) — results of the last Run Filter.

Piece 1 (ROADMAP 6.1, table basics):
  * Lead columns: Symbol ("AAPL (standard)"), Company, Sector, Industry.
  * Parameter columns: start as the parameters the filter used, then add/remove via
    the same searchable category-grouped picker as the Filter page.
  * Header click sorts (Streamlit-native, single column for now).

Still to come: .prms save/load of column sets, explicit multi-column sort, row
multi-select + the Action menu (charts / external sites).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from config.param_hints import PARAM_HINTS
from ui import filter_registry as R

# --------------------------------------------------------------------------- #
# Column labelling — concrete analysis.db column -> human label
# --------------------------------------------------------------------------- #
_PEER_SUFFIXES = (("_vs_sector", " vs Sector"), ("_vs_industry", " vs Industry"))


def _parse(col: str) -> tuple[R.Base, str | None, str] | None:
    """Concrete column -> (base, growth window | None, peer suffix label | '')."""
    stem, peer = col, ""
    for sfx, txt in _PEER_SUFFIXES:
        if col.endswith(sfx):
            stem, peer = col[: -len(sfx)], txt
            break
    if stem in R.BASE_BY_KEY:
        return R.BASE_BY_KEY[stem], None, peer
    for w in R.GROWTH_WINDOWS:
        if stem.endswith(f"_{w}") and stem[: -(len(w) + 1)] in R.BASE_BY_KEY:
            return R.BASE_BY_KEY[stem[: -(len(w) + 1)]], w, peer
    return None


def _short_label(col: str) -> str:
    """Table-header label, e.g. 'Revenue growth · 3Y CAGR vs Sector'."""
    parsed = _parse(col)
    if not parsed:
        return col
    base, win, peer = parsed
    return base.name + (f" · {R.GROWTH_WINDOWS[win]}" if win else "") + peer


def _help(col: str) -> str | None:
    """Hover help for a column header, from param_hints (base-level)."""
    parsed = _parse(col)
    if not parsed:
        return None
    hint = PARAM_HINTS.get(parsed[0].key)
    return hint.get("what_it_is") if hint else None


def _column_options(types: set[str], result: pd.DataFrame, current: list[str]) -> dict[str, str]:
    """Picker options: every applicable base (growth bases expand per window),
    in category order — plus any already-selected column (e.g. a vs-sector
    variant the filter used) so it never silently drops out."""
    opts: dict[str, str] = {}
    for cat, items in R.bases_by_category(types).items():
        for b in items:
            if b.growth:
                for w, wlabel in R.GROWTH_WINDOWS.items():
                    opts[f"{b.key}_{w}"] = f"{cat} · {b.name} · {wlabel}"
            else:
                opts[b.key] = f"{cat} · {b.name}"
    for col in current:
        if col not in opts and col in result.columns:
            parsed = _parse(col)
            opts[col] = f"{parsed[0].category} · {_short_label(col)}" if parsed else col
    return opts


# --------------------------------------------------------------------------- #
# page
# --------------------------------------------------------------------------- #
st.title("Output")

result: pd.DataFrame | None = st.session_state.get("filter_results")
if result is None:
    st.info("No results yet — build and run a screen on the **Filter** page first.")
    st.page_link("ui/pages/filter.py", label="Go to Filter", icon="🔎")
    st.stop()

if result.empty:
    st.subheader("Results — 0 matches")
    st.info("No symbols matched. Loosen a filter or check the selected types on the **Filter** page.")
    st.stop()

types = set(st.session_state.get("filter_results_types") or [])

# A fresh Run Filter resets the column selection to that run's parameters.
run_id = st.session_state.get("filter_results_id")
if st.session_state.get("output_seen_run") != run_id:
    st.session_state["output_seen_run"] = run_id
    st.session_state["output_columns"] = [
        c for c in st.session_state.get("filter_param_cols", []) if c in result.columns
    ]

opts = _column_options(types, result, st.session_state.get("output_columns", []))
st.session_state["output_columns"] = [c for c in st.session_state["output_columns"] if c in opts]

st.subheader(f"Results — {len(result)} match{'es' if len(result) != 1 else ''}")
chosen: list[str] = st.multiselect(
    "Parameter columns", options=list(opts), key="output_columns",
    format_func=lambda c: opts.get(c, c),
    help="Columns shown after Symbol / Company / Sector / Industry. "
         "Starts as the parameters your filter used.",
)

# -- build the display frame -------------------------------------------------- #
view = pd.DataFrame(index=result.index)
view["Symbol"] = result["symbol"].astype(str) + " (" + result["screen_type"].astype(str) + ")"
for src, label in (("name", "Company"), ("sector", "Sector"), ("industry", "Industry")):
    if src in result.columns:
        view[label] = result[src]

col_config: dict = {}
for col in chosen:
    label = _short_label(col)
    series = result[col]
    if pd.api.types.is_float_dtype(series):
        col_config[label] = st.column_config.NumberColumn(label, format="%.2f", help=_help(col))
    else:
        col_config[label] = st.column_config.Column(label, help=_help(col))
    view[label] = series

st.dataframe(view, hide_index=True, width="stretch", column_config=col_config)
st.caption("Click a column header to sort. Column-set save/load (.prms), multi-column "
           "sort and the Action menu are coming next.")
