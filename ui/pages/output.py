"""
Output page (Topic 6) — filter-run results, one browser tab per run.

Two modes, switched on the `?run=<id>` query param:
  * WITHOUT it (sidebar click): a recent-runs launcher — every saved run
    (ui/output_runs.py) listed with name / time / row count / types and an
    "Open ↗" link that opens that run in its own browser tab. Each tab is its
    own Streamlit session, so several output screens can be open side by side
    with independent column selections.
  * WITH it: that run's results table (ROADMAP 6.1, table basics) —
    lead columns Symbol ("AAPL (standard)"), Company, Sector, Industry, then
    parameter columns seeded from the run's filter, add/remove via the same
    searchable category-grouped picker as the Filter page. Header click sorts.

Still to come: .prms save/load of column sets, explicit multi-column sort, row
multi-select + the Action menu (charts / external sites).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from config import settings
from config.param_hints import PARAM_HINTS
from ui import filter_engine as E
from ui import filter_registry as R
from ui import output_runs

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
# run loading + launcher
# --------------------------------------------------------------------------- #
@st.cache_data(max_entries=8, show_spinner=False)
def _load_run_cached(run_id: str) -> tuple[pd.DataFrame, dict] | None:
    """Run files are immutable once written, so caching on the id is safe."""
    return output_runs.load_run(run_id)


def _type_labels(keys: list[str]) -> str:
    return ", ".join(R.SCREEN_TYPES.get(k, {}).get("label", k) for k in keys)


def _describe_block(b: dict) -> str:
    """One filter block as readable text, e.g. '**ROE** (3Y CAGR, vs Sector) > 15'."""
    base = R.BASE_BY_KEY.get(b.get("param"))
    name = base.name if base else str(b.get("param") or "—")
    quals = []
    if b.get("window"):
        quals.append(R.GROWTH_WINDOWS.get(b["window"], b["window"]))
    compare = b.get("compare", "value")
    if compare == "vs_sector":
        quals.append("vs Sector")
    elif compare == "vs_industry":
        quals.append("vs Industry")
    op = b.get("op", ">")
    parts = [f"**{name}**" + (f" ({', '.join(quals)})" if quals else ""), op]

    def operand(val_key: str, vmode_key: str) -> str:
        v = b.get(val_key, "")
        if b.get(vmode_key, "V") == "P":
            pb = R.BASE_BY_KEY.get(str(v))
            return f"*{pb.name if pb else v}*"  # compared to another parameter
        return f"\"{v}\"" if op in E.TEXT_OPS else str(v)

    needs_val, needs_two, _ = E.OPERATORS.get(op, (True, False, False))
    if needs_val:
        parts.append(operand("value", "vmode"))
    if needs_two:
        parts.append(f"and {operand('value2', 'vmode2')}")
    return " ".join(parts)


def _render_filter_summary(meta: dict) -> None:
    """Collapsed, read-only view of the filter that produced this run."""
    with st.expander("Filter used for this run", expanded=False):
        st.caption(f"Security types: {_type_labels(meta.get('screen_types', []))}")
        enabled = [b for b in (meta.get("blocks") or []) if b.get("enabled", True)]
        if not enabled:
            st.caption("No filter conditions — every symbol of the selected types.")
            return
        lines = []
        for b in enabled:
            note = "" if E.is_complete(b) else " *(incomplete — ignored)*"
            lines.append(f"- {_describe_block(b)}{note}")
            for c in b.get("or_children", []):
                if c.get("enabled", True):
                    cnote = "" if E.is_complete(c) else " *(incomplete — ignored)*"
                    lines.append(f"    - **OR** {_describe_block(c)}{cnote}")
        st.markdown("\n".join(lines))
        if len(enabled) > 1:
            st.caption("All top-level conditions must match (AND); indented lines "
                       "are OR fallbacks for the condition above them.")


def _render_launcher() -> None:
    """Recent saved runs, each with an Open ↗ link into its own browser tab."""
    runs = output_runs.list_runs()
    if not runs:
        st.info("No results yet — build and run a screen on the **Filter** page first.")
        st.page_link("ui/pages/filter.py", label="Go to Filter", icon="🔎")
        return
    st.caption(f"Recent filter runs (newest {settings.OUTPUT_RUNS_KEEP} kept). "
               "Each **Open ↗** opens that run in its own browser tab.")
    hdr = st.columns([1, 2.5, 1.6, 0.9, 3.5])
    for col, label in zip(hdr, ("", "Filter", "Run at", "Rows", "Security types")):
        if label:
            col.markdown(f"**{label}**")
    for m in runs:
        row = st.columns([1, 2.5, 1.6, 0.9, 3.5], vertical_alignment="center")
        row[0].link_button("Open ↗", f"/output?run={m['run_id']}")
        row[1].write(m.get("filter_name") or "*(ad-hoc)*")
        row[2].write(str(m.get("created_at", "")).replace("T", " "))
        row[3].write(str(m.get("row_count", "?")))
        row[4].write(_type_labels(m.get("screen_types", [])))


# --------------------------------------------------------------------------- #
# page
# --------------------------------------------------------------------------- #
st.title("Output")

run_id = st.query_params.get("run")
if not run_id:
    _render_launcher()
    st.stop()

_loaded = _load_run_cached(run_id)
if _loaded is None:
    st.warning(f"Run not found — it may have been pruned (the newest "
               f"{settings.OUTPUT_RUNS_KEEP} runs are kept). Pick another below.")
    _render_launcher()
    st.stop()

result, meta = _loaded
st.caption(f"{meta.get('filter_name') or 'Ad-hoc filter'} · run at "
           f"{str(meta.get('created_at', '')).replace('T', ' ')} · "
           f"{_type_labels(meta.get('screen_types', []))}")

if result.empty:
    st.subheader("Results — 0 matches")
    st.info("No symbols matched. Loosen a filter or check the selected types on the **Filter** page.")
    st.stop()

types = set(meta.get("screen_types") or [])

# Seed the column selection from the run's filter parameters the first time this
# session shows this run. Keyed on the URL run id — browser back/forward swaps
# query params WITHOUT a reload, so one session can legitimately see two runs.
if st.session_state.get("output_seen_run") != run_id:
    st.session_state["output_seen_run"] = run_id
    st.session_state["output_columns"] = [
        c for c in meta.get("param_cols", []) if c in result.columns
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
_render_filter_summary(meta)
st.caption("Click a column header to sort. Column-set save/load (.prms), multi-column "
           "sort and the Action menu are coming next.")
