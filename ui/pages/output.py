"""
Output page (Topic 6) — filter-run results, one browser tab per run.

Two modes, switched on the `?run=<id>` query param:
  * WITHOUT it (sidebar click): a recent-runs launcher — every saved run
    (ui/output_runs.py) listed in a selectable table with name / time / row
    count / types and an "Open ↗" link that opens that run in its own browser
    tab. Rows are multi-selectable (shift-click for a range) with a Delete-
    selected button above. Each tab is its own Streamlit session, so several
    output screens can be open side by side with independent column selections.
  * WITH it: that run's results table (ROADMAP 6.1, table basics) —
    lead columns Symbol ("AAPL (standard)"), Company, Sector, Industry, then
    parameter columns seeded from the run's filter, add/remove via the same
    searchable category-grouped picker as the Filter page. Header click sorts.

Multi-column sort is built as an ordered Sort panel (primary → tie-breakers, each
with a direction toggle), applied with pandas — st.dataframe's canvas headers can't
be shift-clicked, so the panel replaces the "shift-click header" interaction;
priority + direction show on each sorted header.

The results table is multi-row selectable; an Action menu above it acts on the
selection — the normalized price chart (opens the Charts page in a new tab) and the
external-site links (Finviz / Yahoo / TradingView). Each opens in its own tab.

Still to come: .prms save/load of column sets, and the remaining chart actions
(fundamentals, dividends).
"""

from __future__ import annotations

import html
import uuid
from urllib.parse import quote

import pandas as pd
import streamlit as st

from config import settings
from config.param_hints import PARAM_HINTS
from ui import filter_engine as E
from ui import filter_registry as R
from ui import output_runs
from ui import param_picker as P

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
# Action-menu targets (Topic 6.2)
# --------------------------------------------------------------------------- #
# External-site URLs come from settings.EXTERNAL_SITES (Finviz/Yahoo multi-symbol,
# TradingView one tab per symbol). Chart actions open the internal Charts page in a
# new tab — mirroring how /output?run=<id> opens a run — with the selected symbols in
# the query string. Koyfin is intentionally absent until its URL format is confirmed.
def _charts_url(symbols: list[str], view: str) -> str:
    return f"/charts?view={view}&symbols=" + quote(",".join(symbols))


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
    """Recent saved runs as a selectable table: per-row Open ↗ link, multi-row
    selection (click / ctrl-click / shift-click range) and a Delete-selected
    button above the list."""
    runs = output_runs.list_runs()
    if not runs:
        st.info("No results yet — build and run a screen on the **Filter** page first.")
        st.page_link("ui/pages/filter.py", label="Go to Filter", icon="🔎")
        return

    run_ids = [m["run_id"] for m in runs]
    table = pd.DataFrame({
        "Open": [f"/output?run={rid}" for rid in run_ids],
        "Filter": [m.get("filter_name") or "(ad-hoc)" for m in runs],
        "Run at": [str(m.get("created_at", "")).replace("T", " ") for m in runs],
        "Rows": [m.get("row_count", None) for m in runs],
        "Security types": [_type_labels(m.get("screen_types", [])) for m in runs],
    })

    st.caption(f"Recent filter runs (newest {settings.OUTPUT_RUNS_KEEP} kept). "
               "Click **Open ↗** to open a run in its own browser tab. Select rows "
               "(Shift-click for a range) and use **Delete selected** to remove them.")

    # The Delete button sits ABOVE the table, so it reads the selection persisted
    # in session state from the previous render (st.dataframe stores it under its
    # key); selecting a row reruns the script, refreshing this count.
    sel = (st.session_state.get("launcher_select") or {}).get("selection", {})
    selected_ids = [run_ids[i] for i in sel.get("rows", []) if i < len(run_ids)]
    if st.button(f"🗑 Delete selected ({len(selected_ids)})",
                 disabled=not selected_ids):
        n = output_runs.delete_runs(selected_ids)
        st.session_state.pop("launcher_select", None)  # drop now-stale row indices
        st.toast(f"Deleted {n} run{'s' if n != 1 else ''}.")
        st.rerun()

    st.dataframe(
        table, hide_index=True, width="stretch", key="launcher_select",
        on_select="rerun", selection_mode="multi-row",
        column_config={
            "Open": st.column_config.LinkColumn("", display_text="Open ↗", width="small"),
            "Rows": st.column_config.NumberColumn("Rows", width="small"),
        },
    )


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
    st.session_state["output_inactive_columns"] = set()  # all seeded columns start active

st.session_state.setdefault("output_inactive_columns", set())
opts = _column_options(types, result, st.session_state.get("output_columns", []))
st.session_state["output_columns"] = [c for c in st.session_state["output_columns"] if c in opts]
# Keep the inactive set in step with the live column list.
st.session_state["output_inactive_columns"] = {
    c for c in st.session_state["output_inactive_columns"] if c in st.session_state["output_columns"]
}

st.subheader(f"Results — {len(result)} match{'es' if len(result) != 1 else ''}")
_render_filter_summary(meta)
# Column selection uses the SAME popover param browser as the Filter page (search
# box, category groups, per-row ▸ info toggles) — just in multi-select mode:
# clicking a metric toggles it in/out of the column set and the popover stays open,
# selected rows primary-styled. Growth metrics appear once per window; the info
# panel is the metric's param_hints, identical to Filter. The collapsible list below
# manages the current set (per-column active checkmark + delete).
def _cb_toggle_column(col: str) -> None:
    cur = st.session_state["output_columns"]
    if col in cur:
        cur.remove(col)
    else:
        cur.append(col)


def _col_info_html(col: str) -> str:
    parsed = _parse(col)
    return P.hint_html(parsed[0]) if parsed else html.escape(col)


_pick_host = st.columns([2, 5], gap="small")[0]
P.render(
    _pick_host,
    opt_keys=list(opts),
    label="➕  Add / remove columns",
    keyp="outcol",
    # opts labels are "Category · Name[ · Window]"; split on " · " to separate the
    # category caption (grouping) from the row's name-only button text.
    category_of=lambda k: opts[k].split(" · ")[0],
    name_of=lambda k: " · ".join(opts[k].split(" · ")[1:]) or opts[k],
    info_html_of=_col_info_html,
    search_text_of=lambda k: f"{opts[k]} {k}".lower(),
    is_selected=lambda k: k in st.session_state["output_columns"],
    on_pick=_cb_toggle_column,
    close_on_pick=False,
)
st.caption("Columns shown after Symbol / Company / Sector / Industry. Starts as the "
           "parameters your filter used. Pick more from the browser (same search + "
           "info toggles as the Filter page); manage them in the list below.")
P.scroll_to_current()


# Current columns: a collapsible list, one entry each with an active checkmark and a
# delete ✕. Unchecking keeps the column in the set but drops it from the table;
# deleting removes it entirely. The active state is read straight from each checkbox
# below (so the grid further down reflects it the same run); delete is an on_click
# callback that reruns. Both therefore update the table immediately.
def _cb_remove_col(col: str) -> None:
    if col in st.session_state["output_columns"]:
        st.session_state["output_columns"].remove(col)
    st.session_state["output_inactive_columns"].discard(col)


# Self-managed collapse (NOT st.expander): an expander re-applies its `expanded`
# argument on every rerun, so a checkbox click inside it would snap it shut. This
# header button toggles a session flag instead, which the checkbox/delete reruns
# leave untouched — so the list only opens/closes when the user clicks the header.
def _cb_toggle_cols() -> None:
    st.session_state["output_cols_open"] = not st.session_state.get("output_cols_open", False)


_cols_list = list(st.session_state["output_columns"])
_inactive = st.session_state["output_inactive_columns"]
_n_active = sum(1 for c in _cols_list if c not in _inactive)
_open = st.session_state.setdefault("output_cols_open", False)
st.button(f"{'▾' if _open else '▸'}  Parameter columns — {_n_active} shown / "
          f"{len(_cols_list)} total", key="output_cols_toggle",
          on_click=_cb_toggle_cols, width="stretch")
if _open:
    if not _cols_list:
        st.caption("No parameter columns yet — add some from the browser above.")
    for _col in _cols_list:
        # ✕ delete sits immediately left of the active checkbox; CSS in app.py
        # (the st-key-rmcol class) makes it a small square that mirrors the box.
        _row = st.columns([1, 24], gap="small", vertical_alignment="center")
        _row[0].button("✕", key=f"rmcol:{_col}", on_click=_cb_remove_col, args=(_col,),
                       help="Remove this column")
        _on = _row[1].checkbox(_short_label(_col), value=_col not in _inactive,
                               key=f"colactive:{_col}",
                               help="Show / hide this column in the table")
        if _on:
            _inactive.discard(_col)
        else:
            _inactive.add(_col)

chosen: list[str] = [c for c in _cols_list if c not in _inactive]

# -- multi-column sort -------------------------------------------------------- #
# st.dataframe's canvas headers can't be shift-clicked for multi-sort (single-column
# native sort only), so the sort is built here as an ordered list of levels — the
# first is primary, each next breaks ties — applied with pandas. Priority + direction
# show on each sorted header (set via column_config labels below). Sort keys are the
# DISPLAYED column labels (lead columns + the visible parameter columns).
_lead_labels = ["Symbol"] + [lbl for src, lbl in
                             (("name", "Company"), ("sector", "Sector"), ("industry", "Industry"))
                             if src in result.columns]
_sortable = _lead_labels + [_short_label(c) for c in chosen]

st.session_state.setdefault("output_sort", [])  # [{_id, col, asc}], order = priority
# Drop levels whose column is no longer shown (column removed or hidden).
st.session_state["output_sort"] = [s for s in st.session_state["output_sort"] if s["col"] in _sortable]


def _cb_sort_add(col: str) -> None:
    if col not in {s["col"] for s in st.session_state["output_sort"]}:
        st.session_state["output_sort"].append({"_id": uuid.uuid4().hex, "col": col, "asc": True})


def _cb_sort_remove(sid: str) -> None:
    st.session_state["output_sort"] = [s for s in st.session_state["output_sort"] if s["_id"] != sid]


def _cb_sort_flip(sid: str) -> None:
    for s in st.session_state["output_sort"]:
        if s["_id"] == sid:
            s["asc"] = not s["asc"]


def _cb_sort_move(sid: str, delta: int) -> None:
    lst = st.session_state["output_sort"]
    i = next((k for k, s in enumerate(lst) if s["_id"] == sid), None)
    if i is None:
        return
    j = i + delta
    if 0 <= j < len(lst):
        lst[i], lst[j] = lst[j], lst[i]


def _cb_toggle_sort() -> None:
    st.session_state["output_sort_open"] = not st.session_state.get("output_sort_open", False)


_spec = st.session_state["output_sort"]
_sort_summary = " → ".join(f"{s['col']} {'▲' if s['asc'] else '▼'}" for s in _spec) or "none"
_sort_open = st.session_state.setdefault("output_sort_open", False)
st.button(f"{'▾' if _sort_open else '▸'}  Sort — {_sort_summary}", key="output_sort_toggle",
          on_click=_cb_toggle_sort, width="stretch")
if _sort_open:
    if not _spec:
        st.caption("No sort yet. Click a column below to add it — the first is the "
                   "primary sort, each next one breaks ties within the previous.")
    for _i, _s in enumerate(_spec):
        _r = st.columns([0.5, 5, 1.6, 0.8, 0.8, 0.8, 4], gap="small", vertical_alignment="center")
        _r[0].markdown(f"<div style='text-align:center;font-weight:600'>{_i + 1}</div>",
                       unsafe_allow_html=True)
        _r[1].markdown(f"**{_s['col']}**")
        _r[2].button("▲ Asc" if _s["asc"] else "▼ Desc", key=f"sortdir:{_s['_id']}",
                     on_click=_cb_sort_flip, args=(_s["_id"],), width="stretch",
                     help="Toggle ascending / descending")
        _r[3].button("▲", key=f"sortup:{_s['_id']}", on_click=_cb_sort_move, args=(_s["_id"], -1),
                     disabled=_i == 0, help="Higher priority", width="stretch")
        _r[4].button("▼", key=f"sortdn:{_s['_id']}", on_click=_cb_sort_move, args=(_s["_id"], 1),
                     disabled=_i == len(_spec) - 1, help="Lower priority", width="stretch")
        _r[5].button("✕", key=f"sortrm:{_s['_id']}", on_click=_cb_sort_remove, args=(_s["_id"],),
                     help="Remove this sort level", width="stretch")
    # Quick-add: one button per column not yet in the sort — click to append it as
    # the next level (ascending; flip with its Asc/Desc toggle).
    _unsorted = [c for c in _sortable if c not in {s["col"] for s in _spec}]
    if _unsorted:
        st.caption("Add a sort column:")
        _per = 4
        for _j in range(0, len(_unsorted), _per):
            _qr = st.columns(_per, gap="small")
            for _cell, _c in zip(_qr, _unsorted[_j:_j + _per]):
                _cell.button(f"＋ {_c}", key=f"sortadd:{_c}", on_click=_cb_sort_add, args=(_c,),
                             width="stretch", help=f"Add {_c} as a sort level")
    else:
        st.caption("All shown columns are already in the sort.")

# Priority (1-based) + direction arrow shown on each sorted column's header.
_ann = {s["col"]: (i + 1, s["asc"]) for i, s in enumerate(_spec)}


def _hdr(label: str) -> str:
    if label in _ann:
        pr, asc = _ann[label]
        return f"{label} {'▲' if asc else '▼'}{pr}"
    return label


# -- build the display frame -------------------------------------------------- #
view = pd.DataFrame(index=result.index)
view["Symbol"] = result["symbol"].astype(str) + " (" + result["screen_type"].astype(str) + ")"
for src, label in (("name", "Company"), ("sector", "Sector"), ("industry", "Industry")):
    if src in result.columns:
        view[label] = result[src]

col_config: dict = {}
for label in _lead_labels:
    if label in _ann:  # lead columns get a header label only when sorted
        col_config[label] = st.column_config.Column(_hdr(label))
for col in chosen:
    label = _short_label(col)
    series = result[col]
    disp = _hdr(label)
    if pd.api.types.is_float_dtype(series):
        col_config[label] = st.column_config.NumberColumn(disp, format="%.2f", help=_help(col))
    else:
        col_config[label] = st.column_config.Column(disp, help=_help(col))
    view[label] = series

if _spec:
    view = view.sort_values(by=[s["col"] for s in _spec], ascending=[s["asc"] for s in _spec],
                            kind="stable", na_position="last")

# -- selected symbols (read the grid's multi-row selection) ------------------- #
# st.dataframe persists its selection under its key; the Action bar sits ABOVE the
# table and reads the prior render's selection — same pattern as the launcher's
# Delete-selected button. Selection rows are positional into the DISPLAYED (sorted)
# order, so map them through view.index back to the symbol.
_display_symbols = result.loc[view.index, "symbol"].astype(str).tolist()
_grid_sel = (st.session_state.get("results_grid") or {}).get("selection", {})
_selected = [_display_symbols[i] for i in _grid_sel.get("rows", []) if i < len(_display_symbols)]

# Native header-click sort is browser-only and can't be disabled. The ONLY thing that
# clears it is a real REMOUNT of the grid's DOM subtree (re-opening the Sort panel does
# this as a side effect, which is why that worked). Changing the dataframe's own key
# does NOT remount the glide component — but changing a WRAPPING container's key does.
# So Reset bumps a counter that re-keys the container the grid lives in, forcing a fresh
# mount that drops an accidental header sort back to the Sort-panel order. The column
# set is folded into the same key so adding/removing a column remounts (re-auto-sizes) too.
def _cb_reset_grid() -> None:
    st.session_state["output_grid_bump"] = st.session_state.get("output_grid_bump", 0) + 1


# -- Action bar (above the table): act on the selected rows ------------------- #
# Reads `_selected` (the prior render's grid selection, computed above). The Action
# menu is disabled until at least one row is selected. One action at a time, each a
# link that opens in a new browser tab (ROADMAP 6.2). Chart actions land here next.
_n_sel = len(_selected)
_sites = settings.EXTERNAL_SITES
_syms_csv = ",".join(_selected)
_act = st.columns([1.6, 5], vertical_alignment="center")
with _act[0].popover(f"⚙ Action · {_n_sel}" if _n_sel else "⚙ Action", disabled=_n_sel == 0):
    _preview = ", ".join(_selected[:10]) + ("…" if _n_sel > 10 else "")
    st.caption(f"{_n_sel} symbol{'' if _n_sel == 1 else 's'} selected: {_preview}")
    st.markdown("**Charts**")
    st.link_button("📈 Normalized price chart ↗", _charts_url(_selected, "price"),
                   width="stretch")
    st.caption("Fundamentals & dividend charts are coming next.")
    st.markdown("**Analyze on external site**")
    _ext = st.columns(2)
    _ext[0].link_button("Finviz ↗", _sites["finviz"].format(symbols=_syms_csv), width="stretch")
    _ext[1].link_button("Yahoo Finance ↗", _sites["yahoo"].format(symbols=_syms_csv),
                        width="stretch")
    st.caption("TradingView — one tab per symbol:")
    for _i in range(0, _n_sel, 4):
        for _cell, _sym in zip(st.columns(4), _selected[_i:_i + 4]):
            _cell.link_button(f"{_sym} ↗", _sites["tradingview"].format(symbol=_sym),
                              width="stretch")
_act[1].caption("Select rows in the table — click, **Shift-click** for a range, "
                "**Ctrl/Cmd-click** to add — then open **Action**.")

_bump = st.session_state.setdefault("output_grid_bump", 0)
_bar = st.columns([5, 1.4], vertical_alignment="bottom")
_bar[1].button("↺ Reset to sort order", key="grid_reset", on_click=_cb_reset_grid,
               width="stretch", help="Undo an accidental header-click sort — restore "
                                     "the Sort-panel order")

_wrap = st.container(key=f"results_grid_wrap:{'|'.join(chosen)}:{_bump}")
_wrap.dataframe(view, hide_index=True, width="stretch", column_config=col_config,
                key="results_grid", on_select="rerun", selection_mode="multi-row")
st.caption("Use the **Sort** panel above for multi-column sort — priority number + "
           "arrow show on each sorted header. A single header click still does a quick "
           "one-off sort; **↺ Reset to sort order** restores the panel order. Column-set "
           "save/load (.prms) and the remaining chart actions are coming next.")
