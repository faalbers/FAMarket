"""
FAMarket — Stock Screening System.
Streamlit entry point. Launch with:

    streamlit run app.py
    streamlit run app.py -- --subset AAPL,MSFT   # prefill the Fetch Control subset

Anything after `--` is handed to the script's argv (Streamlit convention); the
Fetch Control page reads --subset to prefill its dev-subset field.

Wires up runtime directories and logging, then registers the multipage nav.
Page bodies are Phase 3 skeletons under ui/pages/.
"""

from __future__ import annotations

from urllib.parse import urlparse

import streamlit as st

from config import settings
from core.autoshutdown import enable_autoshutdown
from core.logging_config import setup_logging
from core.net import configure_tls
from data_layer import run_state

settings.ensure_runtime_dirs()
configure_tls()
# The app logs to its OWN file (app.log), never the run log (famarket.log). The
# detached fetch process owns + rolls famarket.log; if the app held it open too, the
# roll's unlink() would fail on Windows (WinError 32 — file in use by this app).
setup_logging(settings.APP_LOG_FILE)
# Stop the server when the browser tab is closed (local single-user app). Idempotent
# across reruns. The fetch now runs as its OWN detached process, so closing the app
# no longer touches it — the shutdown hook just prints a terminal notice saying
# whether a background fetch is still running (it will keep going to completion).
enable_autoshutdown(grace=4.0, on_shutdown=run_state.announce_on_shutdown)

st.set_page_config(page_title="FAMarket — Stock Screener", layout="wide")

# Shared compact look — smaller widget fonts and tighter spacing across every page
# (tuned on the Filter page, then promoted here). app.py reruns on each navigation,
# so injecting once here styles all pages.
st.markdown(
    """
    <style>
    [data-testid="stMain"] .stSelectbox div[data-baseweb="select"] > div,
    [data-testid="stMain"] .stTextInput input,
    [data-testid="stMain"] .stNumberInput input {
        min-height: 1.55rem;
    }
    [data-testid="stMain"] .stSelectbox div[data-baseweb="select"] { min-height: 1.55rem; }
    [data-testid="stMain"] .stButton > button {
        padding: 0.05rem 0.3rem; min-height: 1.55rem; min-width: 0;
    }
    [data-testid="stMain"] [data-testid="stHorizontalBlock"] { gap: 0.3rem; }
    [data-testid="stMain"] [data-testid="stHorizontalBlock"] [data-testid="column"] {
        padding: 0 0.1rem;
    }
    /* Param-picker inline info panels (.fam-hi): each option row in the picker
       has a ▸ toggle that expands the param's info in-flow below the row
       (collapsed by default), so it never covers the list. Theme-neutral
       colors: inherits text color, translucent grey background. */
    .fam-hi {
        background: rgba(128, 128, 128, 0.12);
        border-left: 3px solid rgba(128, 128, 128, 0.45);
        border-radius: 0.35rem;
        padding: 0.45rem 0.6rem;
        font-size: 0.75rem; line-height: 1.4;
    }
    .fam-hi ul { margin: 0.3rem 0 0 1.1rem; padding: 0; }
    .fam-hi .fam-h-s { margin-top: 0.3rem; }
    /* Param-picker popovers (Filter / Output / Charts): shrink the open panel to its
       content — only as wide as the longest parameter name — instead of matching the
       stretched trigger button (BaseWeb pins the panel's min-width to the trigger when
       width="stretch"). The popover body is portaled OUTSIDE stMain, so it can't be
       reached by an st-key wrapper; scope it with :has() to the body that holds the
       picker's search box (placeholder "🔍 search…"), which is unique to these pickers
       and absent from the app's other popovers (Action, Load/Add/Save, Reset/Revert). */
    [data-testid="stPopoverBody"]:has(input[placeholder*="search"]) {
        min-width: max-content !important;
        width: max-content !important;
    }
    /* Fundamentals chart Period radio (wrapped in st.container(key="fundperiod")):
       shrink it to its content and push it to the right edge of its column so it sits
       right next to the (content-width, resizable) parameter picker beside it. */
    [data-testid="stMain"] [class*="st-key-fundperiod"] [data-testid="stRadio"] {
        width: fit-content;
        margin-left: auto;
    }
    /* Growth-line Scale toggles (segmented_control key="fundline_mode" on the Fundamentals
       chart, key="divline_mode" on the Dividend yield chart; Actual / Normalized): show the
       selection through BACKGROUND only. By default the active segment turns the theme
       primary (red) text+border; here both segments keep the same text + border and only the
       filled background differs. Verified selectors against the frontend JS: active button =
       stBaseButton-segmented_controlActive, inactive = stBaseButton-segmented_control.
       Descendant selector (st-key sits on an outer container; see CLAUDE.md / the rmcol note
       above). */
    [data-testid="stMain"] [class*="st-key-fundline_mode"] button[data-testid="stBaseButton-segmented_control"],
    [data-testid="stMain"] [class*="st-key-fundline_mode"] button[data-testid="stBaseButton-segmented_controlActive"],
    [data-testid="stMain"] [class*="st-key-divline_mode"] button[data-testid="stBaseButton-segmented_control"],
    [data-testid="stMain"] [class*="st-key-divline_mode"] button[data-testid="stBaseButton-segmented_controlActive"] {
        color: inherit !important;
        border-color: rgba(49, 51, 63, 0.2) !important;
    }
    [data-testid="stMain"] [class*="st-key-fundline_mode"] button[data-testid="stBaseButton-segmented_control"],
    [data-testid="stMain"] [class*="st-key-fundline_mode"] button[data-testid="stBaseButton-segmented_control"]:is(:hover, :active, :focus, :focus-visible),
    [data-testid="stMain"] [class*="st-key-divline_mode"] button[data-testid="stBaseButton-segmented_control"],
    [data-testid="stMain"] [class*="st-key-divline_mode"] button[data-testid="stBaseButton-segmented_control"]:is(:hover, :active, :focus, :focus-visible) {
        background: transparent !important;
    }
    [data-testid="stMain"] [class*="st-key-fundline_mode"] button[data-testid="stBaseButton-segmented_controlActive"],
    [data-testid="stMain"] [class*="st-key-fundline_mode"] button[data-testid="stBaseButton-segmented_controlActive"]:is(:hover, :active, :focus, :focus-visible),
    [data-testid="stMain"] [class*="st-key-divline_mode"] button[data-testid="stBaseButton-segmented_controlActive"],
    [data-testid="stMain"] [class*="st-key-divline_mode"] button[data-testid="stBaseButton-segmented_controlActive"]:is(:hover, :active, :focus, :focus-visible) {
        background: rgba(46, 160, 67, 0.30) !important;
    }
    /* Output parameter-columns list: the ✕ delete (key="rmcol:<col>") is a plain
       click-button shrunk to a small red square (white ✕) that hugs the glyph, no
       button chrome. Target it by its OWN key class (st-key-rmcol…) with a DESCENDANT
       selector — in Streamlit 1.58 the st-key-<key> class sits on an outer container
       and the <button> is nested below .stButton, so `> button` (direct child) misses
       it; ` button` (descendant) is required. !important beats Streamlit's own sizing. */
    [data-testid="stMain"] [class*="st-key-rmcol"] button {
        width: auto !important; min-width: 0 !important;
        height: auto !important; min-height: 0 !important;
        padding: 0.05rem 0.2rem !important; line-height: 1 !important;
        border: none !important; border-radius: 0.25rem !important;
        background: #cc3311 !important; color: #fff !important;
        font-size: 0.9rem !important;
        display: flex !important; align-items: center !important; justify-content: center !important;
    }
    /* Parameter Reference page (wrapped in st.container(key="paramref")): roomier,
       dyslexia-friendlier reading — larger body text and generous line spacing. Uses a
       DESCENDANT selector (the key class sits on an outer container; see the rmcol note
       above and CLAUDE.md). Card names (#### → h4) get a touch more size + top space. */
    [data-testid="stMain"] [class*="st-key-paramref"] p,
    [data-testid="stMain"] [class*="st-key-paramref"] li {
        font-size: 0.95rem !important; line-height: 1.7 !important;
    }
    [data-testid="stMain"] [class*="st-key-paramref"] h4 {
        font-size: 1.15rem !important; margin: 0.1rem 0 0.2rem 0 !important;
    }
    [data-testid="stMain"] [class*="st-key-paramref"] li { margin-bottom: 0.25rem !important; }
    /* Roomier gaps between the stacked parts of each card (name → info → bullets → peers)
       — overrides the app-wide tight 0.3rem gap, scoped to this page by the extra class. */
    [data-testid="stMain"] [class*="st-key-paramref"] [data-testid="stVerticalBlock"] {
        gap: 0.7rem !important;
    }
    /* Dropdown option rows (selectbox / multiselect popover menus). These render in a
       BaseWeb popover portaled OUTSIDE stMain, so they aren't covered by the rules
       above — target them globally. Halve the tall default vertical padding so the
       option lists are more compact. */
    ul[data-baseweb="menu"] li[role="option"],
    ul[role="listbox"] li[role="option"] {
        padding-top: 0.15rem !important;
        padding-bottom: 0.15rem !important;
        min-height: 0 !important;
        line-height: 1.3;
    }
    /* Compact vertical option lists app-wide (stacked checkbox / radio rows, e.g. the
       charts sector/industry selector). The tall look is the GAP between stacked rows
       (Streamlit's ~1rem default) plus each row's own height — halve the gap and trim
       the per-row footprint. */
    [data-testid="stMain"] [data-testid="stVerticalBlock"] { gap: 0.3rem !important; }
    [data-testid="stMain"] [role="radiogroup"] { gap: 0.1rem !important; }
    [data-testid="stMain"] .stCheckbox { min-height: 0; }
    [data-testid="stMain"] .stCheckbox label { min-height: 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

# st.navigation lets pages live under ui/pages/ rather than a top-level pages/ dir.
pages = [
    st.Page("ui/pages/fetch_control.py", title="Fetch Control", icon="⬇️"),
    st.Page("ui/pages/filter.py", title="Filter", icon="🔎"),
    # url_path pinned: filter runs open in their own tabs at /output?run=<id>,
    # so this URL is a contract (rename-proof) — see ui/output_runs.py.
    st.Page("ui/pages/output.py", title="Output", icon="📊", url_path="output"),
    st.Page("ui/pages/sector_index.py", title="Sector Indices", icon="📈"),
    st.Page("ui/pages/scoring_rules_page.py", title="Scoring Rules", icon="🎚️"),
    st.Page("ui/pages/param_reference.py", title="Parameters", icon="📖"),
    st.Page("ui/pages/settings_page.py", title="Settings", icon="⚙️"),
]

# Charts is never a sidebar destination — it's only ever opened in its own tab by an
# Output action link (/charts?view=…&symbols=…; the url_path is a contract, like
# /output above). st.navigation (1.58) has no per-page "hidden" flag, so we keep it
# OFF the menu by registering it ONLY on the request that is actually viewing it —
# recognised by the /charts path or the action link's `view` query param.
_on_charts = urlparse(st.context.url or "").path.rstrip("/").rsplit("/", 1)[-1] == "charts"
if _on_charts or "view" in st.query_params:
    pages.append(st.Page("ui/pages/charts.py", title="Charts", icon="📈", url_path="charts"))

nav = st.navigation(pages)
nav.run()
