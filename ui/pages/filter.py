"""
Filter page (Topic 5) — single unified, type-adaptive screening interface.

Layout (ROADMAP 5.3):
  * ▶ Security Type — checklist of screen types (Common Stock / Standard checked by
    default); multiple selectable, only metrics shared by ALL selected appear.
  * ▶ Filters — Load / Add / Save / Clear row, then the block list. Each block:
    [⏸][+OR][param][window?][value|vs-sector|vs-industry][operator][V/P][value]
    [▲][▼][✕]. OR children are indented one level (fallbacks), drag replaced by ▲▼.
  * Run Filter — computes the result set and persists it as a run file
    (ui/output_runs.py: parquet rows + json metadata, incl. the parameters used to
    seed Output's column selection), then auto-opens the run in its OWN browser
    tab at /output?run=<id>. Several output tabs can be open side by side; past
    runs are listed on the Output page launcher. Empty results don't open a tab
    (and aren't saved) — a message shows here instead.

Metric availability per type comes from `ui/filter_registry.py`; evaluation and
.filt persistence from `ui/filter_engine.py`. The page only orchestrates widgets +
session state; no screening logic lives here.
"""

from __future__ import annotations

import uuid

import pandas as pd
import streamlit as st

from config import settings
from core.database import Database
from ui import file_io as FIO
from ui import filter_engine as E
from ui import filter_registry as R
from ui import output_runs as O
from ui import param_picker as P

# Native file-dialog filter for .filt files (ui/file_io).
_FILT_TYPES = [("Filter files", "*.filt"), ("All files", "*.*")]

# --------------------------------------------------------------------------- #
# data + state
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False)
def _load_analysis(mtime: float) -> pd.DataFrame:
    """analysis.db as a DataFrame, cached on the file's mtime (varies => reloads)."""
    with Database(settings.ANALYSIS_DB) as db:
        return db.read("analysis")


def _analysis_df() -> pd.DataFrame:
    if not settings.ANALYSIS_DB.exists():
        return pd.DataFrame()
    return _load_analysis(settings.ANALYSIS_DB.stat().st_mtime)


@st.cache_data(show_spinner=False)
def _categorical_map(mtime: float) -> dict[str, list]:
    """{column: sorted distinct values} for every low-cardinality column, computed
    once per analysis.db load (the value control offers these as a multi-pick list)."""
    df = _load_analysis(mtime)
    return {c: vals for c in df.columns
            if (vals := E.categorical_values(df, c)) is not None}


def _ensure_state() -> None:
    if "filter_types" not in st.session_state:
        st.session_state["filter_types"] = {R.STANDARD}
    # The Security Type checkboxes are key-driven (sectype:<key>) so a Load can update them;
    # seed each key once from the default selection. (A keyed checkbox ignores its `value=`
    # after first render, which is why Load can't just set filter_types — see _apply_pending.)
    for k in R.SCREEN_TYPES:
        st.session_state.setdefault(f"sectype:{k}", k in st.session_state["filter_types"])
    if "filter_blocks" not in st.session_state:
        st.session_state["filter_blocks"] = [_with_id(E.new_block())]
    # Working name for the filter set, editable in the toolbar. Used as the .filt
    # filename on Save and recorded on each run (Output launcher). Empty = ad-hoc.
    st.session_state.setdefault("filter_name", "untitled")


def _with_id(block: dict) -> dict:
    block.setdefault("_id", uuid.uuid4().hex)
    for child in block.get("or_children", []):
        child.setdefault("_id", uuid.uuid4().hex)
    return block


def _find(blocks: list[dict], bid: str) -> tuple[list[dict], int] | None:
    """Locate a block (or child) by id; returns (containing_list, index)."""
    for i, b in enumerate(blocks):
        if b["_id"] == bid:
            return blocks, i
        for j, c in enumerate(b.get("or_children", [])):
            if c["_id"] == bid:
                return b["or_children"], j
    return None


# -- structural callbacks (mutate the block list, then Streamlit reruns) ----- #
def _cb_add_filter() -> None:
    st.session_state["filter_blocks"].append(_with_id(E.new_block()))


def _cb_clear() -> None:
    st.session_state["filter_blocks"] = [_with_id(E.new_block())]
    st.session_state["filter_name"] = "untitled"


def _cb_add_child(bid: str) -> None:
    found = _find(st.session_state["filter_blocks"], bid)
    if found:
        lst, i = found
        child = _with_id(E.new_block())
        child.pop("or_children", None)
        lst[i].setdefault("or_children", []).append(child)


def _cb_delete(bid: str) -> None:
    found = _find(st.session_state["filter_blocks"], bid)
    if found:
        lst, i = found
        lst.pop(i)
    if not st.session_state["filter_blocks"]:
        st.session_state["filter_blocks"] = [_with_id(E.new_block())]


def _cb_move(bid: str, delta: int) -> None:
    found = _find(st.session_state["filter_blocks"], bid)
    if found:
        lst, i = found
        j = i + delta
        if 0 <= j < len(lst):
            lst[i], lst[j] = lst[j], lst[i]


def _cb_flip_vmode(bid: str, which: str) -> None:
    found = _find(st.session_state["filter_blocks"], bid)
    if found:
        lst, i = found
        key = "vmode2" if which == "2" else "vmode"
        lst[i][key] = "P" if lst[i].get(key, "V") == "V" else "V"


def _cb_set_field(bid: str, field: str, value) -> None:
    """Set one field on a block (the picker handles closing via its nonce)."""
    found = _find(st.session_state["filter_blocks"], bid)
    if found:
        lst, i = found
        lst[i][field] = value


def _cb_toggle_value(bid: str, value: str) -> None:
    """Toggle one value in a categorical block's pick list (multi-select picker)."""
    found = _find(st.session_state["filter_blocks"], bid)
    if not found:
        return
    lst, i = found
    cur = lst[i].get("value")
    cur = list(cur) if isinstance(cur, (list, tuple)) else []
    if value in cur:
        cur.remove(value)
    else:
        cur.append(value)
    lst[i]["value"] = cur


# --------------------------------------------------------------------------- #
# param picker helpers
# --------------------------------------------------------------------------- #
def _param_options(selected: set[str]) -> tuple[list[str], dict[str, str]]:
    """(ordered base keys applicable to the selection, key -> 'Category · Name' label)."""
    grouped = R.bases_by_category(selected)
    keys: list[str] = []
    labels: dict[str, str] = {}
    for cat, items in grouped.items():
        for b in items:
            keys.append(b.key)
            labels[b.key] = f"{cat} · {b.name}"
    return keys, labels


def _param_picker(col, bid: str, field: str, current: str | None,
                  opt_keys: list[str], opt_labels: dict[str, str], keyp: str) -> None:
    """Single-select popover param browser for a filter block field.

    Thin wrapper over the shared `param_picker.render`: clicking a name sets the
    field and closes the popover (the Output page uses the same widget in
    multi-select mode for its column set).
    """
    label = opt_labels.get(current) or (f"⚠️ {current}" if current else "— pick —")
    P.render(
        col,
        opt_keys=opt_keys,
        label=label,
        keyp=keyp,
        category_of=lambda k: R.BASE_BY_KEY[k].category,
        name_of=lambda k: R.BASE_BY_KEY[k].name,
        info_html_of=lambda k: P.hint_html(R.BASE_BY_KEY[k]),
        search_text_of=lambda k: f"{R.BASE_BY_KEY[k].name} {R.BASE_BY_KEY[k].category} {k}".lower(),
        is_selected=lambda k: k == current,
        on_pick=lambda k: _cb_set_field(bid, field, k),
        close_on_pick=True,
    )


def _value_picker(col, bid: str, values: list, current, keyp: str) -> None:
    """Multi-select popover of a categorical column's values (search-narrowed list).

    Reuses the shared param picker in multi-select mode with categories/info off, so
    it looks like the metric picker but lists plain values. Picks are stored as
    strings on the block's `value` list (the engine coerces back per column dtype).
    """
    opts = [str(v) for v in values]
    sel = [str(v) for v in current] if isinstance(current, (list, tuple)) else []
    sel_set = set(sel)
    label = ", ".join(sel) if sel else "— pick —"
    if len(label) > 22:
        label = f"{len(sel)} selected"
    P.render(
        col,
        opt_keys=opts,
        label=label,
        keyp=keyp,
        category_of=lambda k: "",
        name_of=lambda k: k,
        info_html_of=lambda k: "",
        search_text_of=lambda k: k.lower(),
        is_selected=lambda k: k in sel_set,
        on_pick=lambda k: _cb_toggle_value(bid, k),
        close_on_pick=False,
        exclude_selected=False,
        show_info=False,
        show_categories=False,
    )


# --------------------------------------------------------------------------- #
# render one block (or child)
# --------------------------------------------------------------------------- #
def _render_block(block: dict, selected: set[str], opt_keys: list[str], opt_labels: dict[str, str],
                  cat_map: dict[str, list], *, is_child: bool, can_up: bool, can_down: bool) -> None:
    bid = block["_id"]
    pfx = f"flt:{bid}"

    # The param is set via an on_click callback (runs before the rerun), so the
    # block dict is already current; window/operator still come from live widget
    # state so the row's columns match the selection within the same rerun.
    param = block.get("param")
    base = R.BASE_BY_KEY.get(param)
    has_window = bool(base and base.growth)
    window = st.session_state.get(f"{pfx}:win", block.get("window")) if has_window else None
    peers = R.peer_columns(E.resolve_column(param, window, "value"))
    has_compare = bool(peers)

    # Categorical column? -> offer membership ops + a multi-pick value list instead
    # of the numeric/text box. Detection is on the raw value column (categorical
    # bases have no peer variants), so this is independent of the compare mode.
    cat_values = cat_map.get(E.resolve_column(param, window, "value"))
    is_cat = cat_values is not None

    # Operator choices depend on the column kind. Membership ops appear ONLY for
    # categorical columns; everything else keeps the numeric/text operator set.
    op_choices = E.CATEGORICAL_OPS if is_cat else [o for o in E.OPERATORS if o not in E.MULTI_OPS]
    # A keyed selectbox errors if its session_state value isn't in `options` (can
    # happen when the param switches kind), so coerce it to a valid op BEFORE the
    # widget renders — allowed only while the widget isn't yet instantiated.
    opkey = f"{pfx}:op"
    stored_op = st.session_state.get(opkey, block.get("op", op_choices[0]))
    if stored_op not in op_choices:
        stored_op = block.get("op") if block.get("op") in op_choices else op_choices[0]
    st.session_state[opkey] = stored_op
    op = stored_op
    needs_val, needs_two, _ = E.OPERATORS.get(op, (True, False, False))
    show_vp = (not is_cat) and needs_val and op not in E.TEXT_OPS

    # Build only the columns this block actually needs — no empty placeholders. A
    # trailing spacer absorbs the leftover page width so the controls stay close to
    # content-width instead of stretching across the wide layout.
    specs: list[tuple[str, float]] = [("tog", 0.3), ("param", 2.1)]
    if has_window:
        specs.append(("win", 1.25))
    if has_compare:
        specs.append(("cmp", 1.3))
    specs.append(("op", 1.1))
    if show_vp:
        specs.append(("vp", 0.32))
    if needs_val:
        specs.append(("val", 1.4))
    specs.append(("act", 1.9))
    specs.append(("spacer", 2.6))
    cells = st.columns([w for _, w in specs], gap="small")
    C = {name: cells[i] for i, (name, _) in enumerate(specs)}

    # toggle
    block["enabled"] = C["tog"].checkbox("on", value=block.get("enabled", True), key=f"{pfx}:en",
                                         label_visibility="collapsed", help="Enable / disable this filter")

    # param (popover browser with per-entry hover hints; warns if the stored
    # metric isn't valid for the selected types)
    _param_picker(C["param"], bid, "param", param, opt_keys, opt_labels, f"{pfx}:param")
    if param not in opt_keys:
        C["param"].caption("⚠️ not valid for selected type")

    # growth window
    if has_window:
        wins = list(R.GROWTH_WINDOWS)
        wi = wins.index(window) if window in wins else 0
        block["window"] = C["win"].selectbox("window", options=wins, index=wi, key=f"{pfx}:win",
                                              format_func=lambda w: R.GROWTH_WINDOWS[w],
                                              label_visibility="collapsed", help="Growth window")
    else:
        block["window"] = None

    # peer compare (only when the resolved column has _vs_* siblings)
    if has_compare:
        modes = ["value"] + list(peers)
        labels = {"value": "Value", "vs_sector": "vs Sector", "vs_industry": "vs Industry"}
        ci = modes.index(block["compare"]) if block.get("compare") in modes else 0
        block["compare"] = C["cmp"].selectbox("compare", options=modes, index=ci, key=f"{pfx}:cmp",
                                              format_func=lambda m: labels[m], label_visibility="collapsed",
                                              help="Raw value, or distance vs sector/industry median")
    else:
        block["compare"] = "value"

    # operator (options/value pre-seeded above so the keyed widget never errors)
    block["op"] = C["op"].selectbox("operator", options=op_choices, key=opkey,
                                    label_visibility="collapsed")

    # V/P toggle + value
    if show_vp:
        C["vp"].button(block.get("vmode", "V"), key=f"{pfx}:vp", on_click=_cb_flip_vmode, args=(bid, "1"),
                       help="V = fixed value · P = compare to another parameter")
    if needs_val:
        if is_cat:
            _value_picker(C["val"], bid, cat_values, block.get("value"), f"{pfx}:vcat")
        elif block.get("vmode") == "P" and show_vp:
            cur = block.get("value")
            _param_picker(C["val"], bid, "value", cur if cur in opt_keys else None,
                          opt_keys, opt_labels, f"{pfx}:v1p")
        else:
            # A leftover list (param switched away from a categorical column) would
            # render as its repr — blank it so the box starts empty instead.
            cur_text = block.get("value", "")
            if isinstance(cur_text, (list, tuple)):
                cur_text = ""
            block["value"] = C["val"].text_input("value", value=str(cur_text), key=f"{pfx}:v1",
                                                  label_visibility="collapsed",
                                                  placeholder="text…" if op in E.TEXT_OPS else "value")

    # second operand for `between` (its own compact row, value roughly under the first)
    if needs_two:
        b = st.columns([3.5, 0.5, 0.32, 1.4, 3.2], gap="small")
        b[1].markdown("<div style='text-align:right;font-size:0.82rem;line-height:1.9rem'>and</div>",
                      unsafe_allow_html=True)
        b[2].button(block.get("vmode2", "V"), key=f"{pfx}:vp2", on_click=_cb_flip_vmode, args=(bid, "2"),
                    help="V = fixed value · P = parameter")
        if block.get("vmode2") == "P":
            cur2 = block.get("value2")
            _param_picker(b[3], bid, "value2", cur2 if cur2 in opt_keys else None,
                          opt_keys, opt_labels, f"{pfx}:v2p")
        else:
            block["value2"] = b[3].text_input("value2", value=str(block.get("value2", "")), key=f"{pfx}:v2",
                                               label_visibility="collapsed", placeholder="value")

    # actions: +OR (parent only), ▲ ▼, ✕ — full-width so the glyph centers in its cell
    acts = C["act"].columns(4, gap="small")
    if not is_child:
        acts[0].button("➕", key=f"{pfx}:or", on_click=_cb_add_child, args=(bid,),
                       help="Add an OR fallback", width="stretch")
    acts[1].button("▲", key=f"{pfx}:up", on_click=_cb_move, args=(bid, -1), disabled=not can_up,
                   help="Move up", width="stretch")
    acts[2].button("▼", key=f"{pfx}:dn", on_click=_cb_move, args=(bid, 1), disabled=not can_down,
                   help="Move down", width="stretch")
    acts[3].button("✕", key=f"{pfx}:del", on_click=_cb_delete, args=(bid,),
                   help="Delete", width="stretch")


# --------------------------------------------------------------------------- #
# page
# --------------------------------------------------------------------------- #
st.title("Filter")
_ensure_state()
df = _analysis_df()

if df.empty:
    st.info("No analysis data yet. Run a fetch on the **Fetch Control** page first.")
    st.stop()

# A Load applies its saved security types HERE — before the checkboxes are instantiated,
# since a widget's session_state key can't be modified after the widget renders in the same
# run. The Load handler stashes the types under _pending_sectypes and reruns.
_pending_types = st.session_state.pop("_pending_sectypes", None)
if _pending_types is not None:
    for k in R.SCREEN_TYPES:
        st.session_state[f"sectype:{k}"] = k in set(_pending_types)

# -- Security Type ---------------------------------------------------------- #
with st.expander("Security Type", expanded=True):
    cols = st.columns(3)
    selected: set[str] = set()
    for i, (key, meta) in enumerate(R.SCREEN_TYPES.items()):
        # Key-driven (no value=): the checkbox reads its own sectype:<key> state, which
        # _ensure_state seeds and Load overwrites via _pending_sectypes above.
        if cols[i % 3].checkbox(meta["label"], key=f"sectype:{key}", help=meta["help"]):
            selected.add(key)
    st.session_state["filter_types"] = selected

if not selected:
    st.warning("Select at least one security type to choose parameters.")
    st.stop()

opt_keys, opt_labels = _param_options(selected)
cat_map = _categorical_map(settings.ANALYSIS_DB.stat().st_mtime)

# -- Filters ---------------------------------------------------------------- #
with st.expander("Filters", expanded=True):
    bar = st.columns([1, 1, 1, 1, 3], vertical_alignment="bottom")
    # Save / Load / Add go through the app's native OS file dialog (ui/file_io),
    # starting in the filters library. Save writes straight there; Load replaces the
    # current blocks; Add appends. The dialog runs out-of-process and blocks until the
    # user picks or cancels — a None return means cancel, so nothing changes.
    if bar[0].button("💾 Save", width="stretch",
                     help="Save these conditions to a .filt file"):
        typed = (st.session_state.get("filter_name") or "untitled").strip() or "untitled"
        path = FIO.ask_save_path(
            initialdir=settings.FILTERS_DIR, default_name=f"{typed}.filt",
            defaultextension=".filt", filetypes=_FILT_TYPES, title="Save filter")
        if path:
            E.save_filterset_to(path, list(selected), st.session_state["filter_blocks"])
            st.session_state["filter_name"] = path.stem  # reflect saved name in the box
            st.toast(f"Saved {path.name}")
            st.rerun()
    if bar[1].button("📂 Load", width="stretch",
                     help="Replace the current filter with a saved .filt file"):
        path = FIO.ask_open_path(initialdir=settings.FILTERS_DIR,
                                 filetypes=_FILT_TYPES, title="Load filter")
        if path:
            try:
                data = E.load_filterset_from(path)
            except (OSError, ValueError) as exc:
                st.error(f"Couldn't read {path.name}: {exc}")
            else:
                st.session_state["filter_blocks"] = (
                    [_with_id(b) for b in data["blocks"]] or [_with_id(E.new_block())])
                if data["selected_types"]:
                    # Applied at the top of the next run, before the checkboxes render.
                    st.session_state["_pending_sectypes"] = list(data["selected_types"])
                st.session_state["filter_name"] = path.stem
                st.toast(f"Loaded {path.name}")
                st.rerun()
    if bar[2].button("➕ Add", width="stretch",
                     help="Append blocks from a saved .filt file"):
        path = FIO.ask_open_path(initialdir=settings.FILTERS_DIR,
                                 filetypes=_FILT_TYPES, title="Add filter")
        if path:
            try:
                data = E.load_filterset_from(path)
            except (OSError, ValueError) as exc:
                st.error(f"Couldn't read {path.name}: {exc}")
            else:
                st.session_state["filter_blocks"].extend(
                    _with_id(b) for b in data["blocks"])
                # Keep the current Filter name across the rerun: its text_input isn't
                # rendered on this run (we rerun above it), and Streamlit drops a
                # widget's value when the widget doesn't render unless it's re-pinned.
                st.session_state["filter_name"] = st.session_state.get("filter_name", "")
                st.toast(f"Added blocks from {path.name}")
                st.rerun()
    bar[3].button("🧹 Clear", on_click=_cb_clear, width="stretch", help="Remove all blocks")
    bar[4].text_input(
        "Filter name", key="filter_name", placeholder="untitled",
        help="Working name for this filter set. Pre-fills the Save dialog's filename "
             "and is recorded on each run. Leave blank for an ad-hoc run.")

    st.divider()

    blocks = st.session_state["filter_blocks"]
    for bi, block in enumerate(blocks):
        _render_block(block, selected, opt_keys, opt_labels, cat_map, is_child=False,
                      can_up=bi > 0, can_down=bi < len(blocks) - 1)
        children = block.get("or_children", [])
        if children:
            cwrap = st.container(border=True)
            with cwrap:
                st.caption("OR — fallbacks")
                for ci, child in enumerate(children):
                    _render_block(child, selected, opt_keys, opt_labels, cat_map, is_child=True,
                                  can_up=ci > 0, can_down=ci < len(children) - 1)
        st.markdown("<hr style='margin:0.3rem 0;border:none;border-top:1px dashed rgba(128,128,128,.3)'>",
                    unsafe_allow_html=True)

    st.button("＋ Add Filter", on_click=_cb_add_filter)

# Scroll a freshly opened param-picker popover to its current (primary-styled)
# entry — shared with the Output page's column picker.
P.scroll_to_current()

# -- Run -------------------------------------------------------------------- #
def _used_columns(blocks: list[dict]) -> list[str]:
    """Concrete analysis.db columns the enabled blocks reference, in block order.
    Seeds the Output page's parameter-column selection (ROADMAP 6.1)."""
    cols: list[str] = []

    def add(b: dict) -> None:
        col = E.resolve_column(b["param"], b.get("window"), b.get("compare", "value"))
        if col not in cols:
            cols.append(col)

    for b in blocks:
        if not b.get("enabled", True):
            continue
        add(b)
        for c in b.get("or_children", []):
            if c.get("enabled", True):
                add(c)
    return cols


st.divider()
# Run Filter persists the result as a run file and AUTO-OPENS it in its own
# browser tab (/output?run=<id>) — several output tabs can be open side by side.
# The window.open script is rendered ONLY on the click rerun, so later reruns
# never re-open the tab. Browsers may block this first popup (it arrives after a
# server round-trip, outside the click gesture) — the caption carries a plain
# fallback link, and allowing pop-ups for the site once fixes it for good.
# Empty results: message here instead — no tab, no run file.
if st.button("▶ Run Filter", type="primary"):
    result = E.run_filter(df, selected, st.session_state["filter_blocks"])
    if result.empty:
        st.warning("No symbols matched — nothing opened. Loosen a filter or check "
                   "the selected security types.")
    else:
        rid = O.save_run(
            result,
            screen_types=list(selected),
            param_cols=_used_columns(st.session_state["filter_blocks"]),
            filter_name=(st.session_state.get("filter_name") or "").strip() or None,
            blocks=st.session_state["filter_blocks"],
        )
        _url = f"/output?run={rid}"
        st.iframe(f"<script>window.parent.open('{_url}', '_blank');</script>", height=1)
        _n = len(result)
        st.caption(f"Results opened in a new tab — {_n} match{'es' if _n != 1 else ''}. "
                   f"No tab? Allow pop-ups for this site, or [open it here]({_url}). "
                   "Past runs are listed on the Output page.")
