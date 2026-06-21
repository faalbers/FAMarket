"""
Output page (Topic 6) — saved-output results, one browser tab per output.

An "output" is either a FILTER run (from the Filter page) or a CUSTOM symbol set
(hand-entered here); both persist the same way (ui/output_runs.py, kind="filter"/
"custom") and open into the same results screen.

Two modes, switched on the `?run=<id>` query param:
  * WITHOUT it (sidebar click): a "Recent outputs" launcher — every saved output
    listed in a selectable table (Name — itself the "↗" open link — Type, Run at,
    Count, and Security types, shown for filter runs only). Rows are multi-selectable
    (shift-click for a range) with a Delete-selected button above. Each tab is its
    own Streamlit session, so several output screens can be open side by side with
    independent column selections. Above the list, a "Custom Symbols" box names a
    hand-typed symbol set and the Go button opens it as a saved output — the same
    results screen a filter run opens, snapshotting those symbols from analysis.db.
  * WITH it: that output's results table (ROADMAP 6.1, table basics) —
    lead columns Symbol ("AAPL (standard)"), Company, Sector, Industry, then
    parameter columns seeded from the run's filter (empty for custom; add via the
    same searchable category-grouped picker as the Filter page). Header click sorts.

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
from config import param_hints
from core.database import Database
from ui import filter_engine as E
from ui import filter_registry as R
from ui import output_runs
from ui import param_picker as P
from ui import selection_io as SEL

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
    """Hover help for a column header, from param_hints (base-level). The header
    already shows the name, so drop the title line; formatting lives in
    config.param_hints (one source of truth for the hint style)."""
    parsed = _parse(col)
    if not parsed or param_hints.get_hint(parsed[0].key) is None:
        return None
    return param_hints.hint_markdown(parsed[0].key, header=False) or None


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
def _charts_url(symbols: list[str], view: str, cols: list[str] | None = None) -> str:
    url = f"/charts?view={view}&symbols=" + quote(",".join(symbols))
    if cols:  # param-driven views default their picker to the Output's SHOWN columns
        url += "&cols=" + quote(",".join(cols))
    return url


def _parse_symbols(raw: str) -> list[str]:
    """Comma/space/newline-separated tickers -> upper-cased list (mirrors
    fetch_control._parse_subset). Charts uppercase on read too, so this matches."""
    parts = [p.strip().upper() for p in raw.replace("\n", ",").replace(" ", ",").split(",")]
    return [p for p in parts if p]


def _render_actions(symbols: list[str], cols: list[str] | None = None) -> None:
    """Render the Action-menu body (charts + external links) for `symbols`. Shared by the
    run-results selection popover and the launcher's quick-actions box — keep it body-only
    (no popover wrapper) so each caller decides how to host it.

    `cols` = the Output's currently SHOWN parameter columns; passed to the param-driven
    chart views (heat map, fundamentals bar/line) so they default to what's visible in the
    table (hidden/deselected columns are excluded). Other views ignore it."""
    n = len(symbols)
    sites = settings.EXTERNAL_SITES
    syms_csv = ",".join(symbols)
    preview = ", ".join(symbols[:10]) + ("…" if n > 10 else "")
    st.caption(f"{n} symbol{'' if n == 1 else 's'}: {preview}")
    st.markdown("**Charts**")
    _ch = st.columns(2)
    _ch[0].link_button("📈 Normalized price chart ↗", _charts_url(symbols, "price"),
                       width="stretch")
    _ch[1].link_button("📊 Fundamentals over time ↗",
                       _charts_url(symbols, "fundamentals_bar", cols), width="stretch")
    _ch2 = st.columns(2)
    _ch2[0].link_button("📉 Fundamentals growth lines ↗",
                        _charts_url(symbols, "fundamentals_line", cols), width="stretch")
    _ch2[1].link_button("🎯 Category scores radar ↗", _charts_url(symbols, "radar"),
                        width="stretch")
    _ch3 = st.columns(2)
    _ch3[0].link_button("🔥 Metrics heat map ↗", _charts_url(symbols, "heatmap", cols),
                        width="stretch")
    _ch3[1].link_button("🏅 Scores heat map ↗", _charts_url(symbols, "scores_heatmap"),
                        width="stretch")
    st.caption("Price / growth-lines / radar / heat maps compare all selected symbols; the "
               "Fundamentals bar picks one symbol + one parameter across periods. The "
               "**Metrics** heat map colors your shown columns by Scoring Rule; the "
               "**Scores** heat map shows the category scores + RS Rank (orange = strong).")
    st.markdown("**Dividends**")
    _dv = st.columns(2)
    _dv[0].link_button("💰 Dividend yield ↗",
                       _charts_url(symbols, "dividend_line"), width="stretch")
    st.caption("Dividend yield per calendar period (annual/quarterly), one line per symbol, "
               "Actual or Normalized to 100. Yield heat-map chart coming next.")
    st.markdown("**Analyze on external site**")
    _ext = st.columns(2)
    _ext[0].link_button("Finviz ↗", sites["finviz"].format(symbols=syms_csv), width="stretch")
    _ext[1].link_button("Yahoo Finance ↗", sites["yahoo"].format(symbols=syms_csv),
                        width="stretch")
    st.caption("TradingView — one tab per symbol:")
    for _i in range(0, n, 4):
        for _cell, _sym in zip(st.columns(4), symbols[_i:_i + 4]):
            _cell.link_button(f"{_sym} ↗", sites["tradingview"].format(symbol=_sym),
                              width="stretch")


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
    """Collapsed, read-only view of what produced this output — the filter blocks for a
    filter run, or the hand-entered symbol list for a custom output."""
    if meta.get("kind") == "custom":
        syms = meta.get("symbols", [])
        with st.expander("Symbols in this output", expanded=False):
            st.caption(f"{len(syms)} hand-entered symbol{'' if len(syms) == 1 else 's'}.")
            st.markdown(", ".join(syms))
        return
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
    """Recent saved outputs as a selectable table: per-row Open ↗ link, multi-row
    selection (click / ctrl-click / shift-click range) and a Delete-selected
    button above the list. Lists both filter runs and custom symbol sets (Type column)."""
    runs = output_runs.list_runs()
    if not runs:
        st.info("No outputs yet — run a screen on the **Filter** page, or use "
                "**Custom Symbols** above.")
        st.page_link("ui/pages/filter.py", label="Go to Filter", icon="🔎")
        return

    run_ids = [m["run_id"] for m in runs]
    kinds = [m.get("kind", "filter") for m in runs]
    names = [m.get("filter_name") or "(ad-hoc)" for m in runs]
    # The Name IS the open link: the cell value is the /output?run=<id> URL with the display
    # text appended as a fragment (ignored by routing); the LinkColumn's display_text regex
    # below pulls that text back out, so each row shows "<name> ↗" and clicks open the output.
    # Security types only mean something for a filter run; custom outputs leave it blank.
    table = pd.DataFrame({
        "Name": [f"/output?run={rid}#{nm} ↗" for rid, nm in zip(run_ids, names)],
        "Type": ["Custom" if k == "custom" else "Filter" for k in kinds],
        "Run at": [str(m.get("created_at", "")).replace("T", " ") for m in runs],
        "Count": [m.get("row_count", None) for m in runs],
        "Security types": [_type_labels(m.get("screen_types", [])) if k == "filter" else ""
                           for m, k in zip(runs, kinds)],
    })

    st.caption(f"Recent outputs (newest {settings.OUTPUT_RUNS_KEEP} kept). "
               "Click a **name ↗** to open that output in its own browser tab. Select rows "
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
            "Name": st.column_config.LinkColumn("Name", display_text=r"#(.*)$"),
            "Type": st.column_config.TextColumn("Type", width="small"),
            "Count": st.column_config.NumberColumn("Count", width="small"),
        },
    )


def _read_analysis_rows(symbols: list[str]) -> pd.DataFrame:
    """analysis.db rows for `symbols`, ordered to the typed order (mirrors the
    charts.py analysis read). Symbols absent from analysis.db simply don't appear."""
    if not symbols or not settings.ANALYSIS_DB.exists():
        return pd.DataFrame()
    with Database(settings.ANALYSIS_DB) as db:
        if not db.table_exists("analysis"):
            return pd.DataFrame()
        ph = ",".join("?" * len(symbols))
        df = db.read("analysis", where=f"symbol IN ({ph})", params=list(symbols))
    if not df.empty:
        order = {s: i for i, s in enumerate(symbols)}
        df = (df.assign(_o=df["symbol"].map(order)).sort_values("_o")
                .drop(columns="_o").reset_index(drop=True))
    return df


def _open_custom_output(name: str, symbols: list[str]) -> None:
    """Snapshot the typed symbols from analysis.db, save as a custom output, and open it
    in a new tab — same flow as the Filter page's Run Filter."""
    df = _read_analysis_rows(symbols)
    if df.empty:
        st.warning("None of those symbols are in analysis.db — run analysis first, "
                   "or check the tickers.")
        return
    found = set(df["symbol"].astype(str))
    missing = [s for s in symbols if s not in found]
    stypes = (sorted(map(str, df["screen_type"].dropna().unique()))
              if "screen_type" in df.columns else [])
    rid = output_runs.save_custom_run(df, name=name, symbols=symbols, screen_types=stypes)
    _url = f"/output?run={rid}"
    st.iframe(f"<script>window.parent.open('{_url}', '_blank');</script>", height=1)
    note = (f" · {len(missing)} not in analysis.db: {', '.join(missing[:10])}"
            if missing else "")
    _n = len(found)
    st.caption(f"Opened {_n} symbol{'' if _n == 1 else 's'} in a new tab{note}. "
               f"No tab? Allow pop-ups for this site, or [open it here]({_url}).")


def _render_custom_symbols() -> None:
    """Name a hand-entered symbol set and open it as a saved Output (kind='custom') — the
    same results screen a filter run opens. Go is active only when both fields are filled;
    symbols parse like the Fetch Dev subset (comma/space/newline)."""
    # A Load (.syms) refills the keyed text inputs HERE, before they render, since a
    # keyed widget ignores its value after first render (pending-widget-state pattern).
    _pend = st.session_state.pop("_pending_custom", None)
    if _pend is not None:
        st.session_state["custom_symbols"] = _pend["symbols"]
        st.session_state["custom_name"] = _pend["name"]

    with st.expander("Custom Symbols", expanded=False):
        name = st.text_input("Output name", key="custom_name", placeholder="My watchlist")
        raw = st.text_input(
            "Symbols (comma / space / newline separated)",
            key="custom_symbols", placeholder="AAPL, MSFT, KO")
        syms = _parse_symbols(raw)
        ready = bool(name.strip()) and bool(syms)
        _cb = st.columns([1, 1, 1])
        if _cb[0].button("Go", type="primary", disabled=not ready, width="stretch"):
            _open_custom_output(name.strip(), syms)
        # Save the typed symbols as a .syms selection (info = Company/Sector/Industry).
        if _cb[1].button("💾 Save", disabled=not syms, width="stretch",
                         help="Save these symbols to a .syms file"):
            p = SEL.save_dialog(kind="symbols", items=SEL.symbol_info(syms),
                                default_name=name.strip())
            if p:
                st.toast(f"Saved {p.name}")
        # Load a .syms: refill the symbols + set the Output name to the file's stem.
        if _cb[2].button("📂 Load", width="stretch",
                         help="Load symbols from a .syms file"):
            data = SEL.load_dialog(kind="symbols")
            if data:
                st.session_state["_pending_custom"] = {
                    "symbols": ", ".join(data["items"].keys()),
                    "name": data["path"].stem,
                }
                st.toast(f"Loaded {data['path'].name}")
                st.rerun()
        st.caption("Name a set of symbols and **Go** to open them as an Output — "
                   "the same results screen as a filter run. **Save / Load** the set "
                   "as a .syms file.")
    st.divider()


# --------------------------------------------------------------------------- #
# page
# --------------------------------------------------------------------------- #
st.title("Output")

run_id = st.query_params.get("run")
if not run_id:
    _render_custom_symbols()
    _render_launcher()
    st.stop()

_loaded = _load_run_cached(run_id)
if _loaded is None:
    st.warning(f"Run not found — it may have been pruned (the newest "
               f"{settings.OUTPUT_RUNS_KEEP} runs are kept). Pick another below.")
    _render_launcher()
    st.stop()

result, meta = _loaded
_created = str(meta.get("created_at", "")).replace("T", " ")
if meta.get("kind") == "custom":
    _nsym = len(meta.get("symbols", []))
    st.caption(f"{meta.get('filter_name') or 'Custom symbols'} · created {_created} · "
               f"Custom ({_nsym} symbol{'' if _nsym == 1 else 's'})")
else:
    st.caption(f"{meta.get('filter_name') or 'Ad-hoc filter'} · run at {_created} · "
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


def _param_info(col: str) -> dict:
    """A param column's param_hints entry (resolved to its base key) for a .prms save."""
    parsed = _parse(col)
    if parsed and (hint := param_hints.PARAM_HINTS.get(parsed[0].key)):
        return dict(hint)
    return {}


# The ➕ Add / remove columns picker now lives INSIDE the Parameter-columns list (at
# its top), built below once the list is open.
#
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
    # Add / remove columns picker at the TOP of the list (same searchable, category-
    # grouped popover browser as the Filter page; the list is a self-managed collapse,
    # NOT st.expander, so a popover inside it is allowed).
    P.render(
        st.container(),
        opt_keys=list(opts),
        label="➕  Add columns",
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
    P.scroll_to_current()

    # Save the SELECTED (shown) columns as a .prms; Add appends a saved set to the
    # current columns (info = each param's param_hints). No Swap — Add only.
    def _add_prms(keys: list[str]) -> None:
        valid = [k for k in keys if k in opts]
        cur = st.session_state["output_columns"]
        cur.extend(k for k in valid if k not in cur)
        for k in valid:  # re-seed the keyed show/hide checkboxes so they pick up value=
            st.session_state.pop(f"colactive:{k}", None)

    _selected_cols = [c for c in _cols_list if c not in _inactive]
    _pb = st.columns(2)
    if _pb[0].button("💾 Save Selection", key="prms_save", width="stretch",
                     disabled=not _selected_cols, help="Save the shown columns to a .prms file"):
        p = SEL.save_dialog(kind="params",
                            items={c: _param_info(c) for c in _selected_cols},
                            default_name=(meta.get("filter_name") or "").strip())
        if p:
            st.toast(f"Saved {p.name}")
    if _pb[1].button("📂 Add Selection", key="prms_add", width="stretch",
                     help="Append a saved .prms set to the current columns"):
        data = SEL.load_dialog(kind="params")
        if data:
            _add_prms(list(data["items"].keys()))
            st.toast(f"Added {data['path'].name}")
            st.rerun()

    if not _cols_list:
        st.caption("No parameter columns yet — add some with **➕ Add columns** above.")
    # The per-row ✕ delete buttons are styled in app.py into a small red square that
    # hugs the glyph — targeted by each button's OWN key class (st-key-rmcol…) with a
    # DESCENDANT selector (the <button> is nested below the keyed wrapper in Streamlit
    # 1.58). The narrow first column keeps the ✕ tight against the checkbox.
    _rows_box = st.container(key="paramcolrows")
    for _col in _cols_list:
        # ✕ delete = plain click-button; the checkbox on the right carries the column
        # name and toggles show/hide (checked = shown in the table).
        _row = _rows_box.columns([1, 30], gap="small", vertical_alignment="center")
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

# Apply a pending programmatic selection (from "Add Selection") HERE — before the Action
# bar reads the selection and before the grid renders — so BOTH reflect it this run. The
# Add handler stashes the rows + reruns; setting the grid's key now keeps it in
# _new_session_state (so value_changed pushes it to the grid) and the bump forces a fresh
# mount that accepts it even after a manual clear-all.
_pending_rows = st.session_state.pop("_pending_grid_rows", None)
if _pending_rows is not None:
    st.session_state["results_grid"] = {"selection": {"rows": _pending_rows}}
    st.session_state["output_grid_bump"] = st.session_state.get("output_grid_bump", 0) + 1

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
# link that opens in a new browser tab (ROADMAP 6.2). Body lives in _render_actions,
# shared with the launcher's quick-actions box.
_n_sel = len(_selected)
_act = st.columns([1.6, 5], vertical_alignment="center")
with _act[0].popover(f"⚙ Action · {_n_sel}" if _n_sel else "⚙ Action", disabled=_n_sel == 0):
    _render_actions(_selected, chosen)
_act[1].caption("Select rows in the table — click, **Shift-click** for a range, "
                "**Ctrl/Cmd-click** to add — then open **Action**.")

_bar = st.columns([5, 1.4], vertical_alignment="bottom")
_bar[1].button("↺ Reset to sort order", key="grid_reset", on_click=_cb_reset_grid,
               width="stretch", help="Undo an accidental header-click sort — restore "
                                     "the Sort-panel order")

# -- Selection Save / Add (Output-screen UI, just above the table) ------------ #
# Save the current row selection as a .syms; Add a .syms's symbols to the selection
# (only those present in this table). Add can't set the grid selection here — the
# Action bar above already read `_selected` this run — so it stashes the target rows in
# `_pending_grid_rows` and reruns; the top of the page applies them before _selected and
# the grid (see that block for why that placement is required).
_sel_bar = st.columns([1.5, 1.5, 4], vertical_alignment="center")
if _sel_bar[0].button("💾 Save selection", disabled=not _selected, width="stretch",
                      help="Save the selected symbols to a .syms file"):
    p = SEL.save_dialog(kind="symbols", items=SEL.symbol_info(_selected),
                        default_name=(meta.get("filter_name") or "").strip())
    if p:
        st.toast(f"Saved {p.name}")
if _sel_bar[1].button("📂 Add Selection", width="stretch",
                      help="Load a .syms and add its symbols to the selection "
                           "(only those present in this table)"):
    data = SEL.load_dialog(kind="symbols")
    if data:
        loaded = set(data["items"].keys())
        present = set(_display_symbols)
        new_rows = [i for i, s in enumerate(_display_symbols) if s in loaded]
        cur_rows = list(_grid_sel.get("rows", []))
        st.session_state["_pending_grid_rows"] = sorted(set(cur_rows) | set(new_rows))
        _added = len(set(new_rows) - set(cur_rows))
        _missing = len(loaded - present)
        st.toast(f"Added {_added} to selection"
                 + (f" · {_missing} not in this table" if _missing else ""))
        st.rerun()
_sel_bar[2].caption("Save the selected rows as a .syms, or Add one to bring its "
                    "symbols into the current selection (only those in this table).")

_bump = st.session_state.setdefault("output_grid_bump", 0)
_wrap = st.container(key=f"results_grid_wrap:{'|'.join(chosen)}:{_bump}")
_wrap.dataframe(view, hide_index=True, width="stretch", column_config=col_config,
                key="results_grid", on_select="rerun", selection_mode="multi-row")
st.caption("Use the **Sort** panel above for multi-column sort — priority number + "
           "arrow show on each sorted header. A single header click still does a quick "
           "one-off sort; **↺ Reset to sort order** restores the panel order.")
