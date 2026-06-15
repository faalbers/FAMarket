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

import streamlit as st

from config import settings
from core.autoshutdown import enable_autoshutdown
from core.logging_config import setup_logging
from core.net import configure_tls
from data_layer import cancel

settings.ensure_runtime_dirs()
configure_tls()
setup_logging()
# Stop the server when the browser tab is closed (local single-user app). Idempotent
# across reruns. Before exiting, gracefully unwind any in-flight fetch (finish the
# current batch, skip analysis) so the databases aren't left mid-write — with CLI
# notices about what it's doing (cancel.stop_for_shutdown prints to stdout).
enable_autoshutdown(grace=4.0, on_shutdown=cancel.stop_for_shutdown)

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
        font-size: 0.82rem; min-height: 1.9rem;
    }
    [data-testid="stMain"] .stSelectbox div[data-baseweb="select"] { min-height: 1.9rem; }
    [data-testid="stMain"] .stButton > button {
        font-size: 0.82rem; padding: 0.1rem 0.3rem; min-height: 1.9rem; min-width: 0;
    }
    [data-testid="stMain"] label p,
    [data-testid="stMain"] .stCheckbox label p,
    [data-testid="stMain"] .stRadio label p { font-size: 0.84rem; }
    [data-testid="stMain"] [data-testid="stHorizontalBlock"] { gap: 0.3rem; }
    [data-testid="stMain"] [data-testid="stHorizontalBlock"] [data-testid="column"] {
        padding: 0 0.1rem;
    }
    [data-testid="stMain"] [data-testid="stCaptionContainer"] p { font-size: 0.72rem; }
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
    /* Output parameter-columns list (inside the paramcolrows container): the ✕ delete
       is a plain click-button sized to match the show/hide checkbox box beside it — a
       small red square (white ✕), no button chrome. !important everywhere to beat
       Streamlit's own button sizing (that was leaving it ~3× too big). */
    [data-testid="stMain"] [class*="st-key-paramcolrows"] .stButton > button {
        width: 1rem !important; min-width: 1rem !important;
        height: 1rem !important; min-height: 1rem !important;
        padding: 0 !important; line-height: 1 !important;
        border: none !important; border-radius: 0.25rem !important;
        background: #cc3311 !important; color: #fff !important;
        font-size: 0.72rem !important;
        display: flex !important; align-items: center !important; justify-content: center !important;
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
        font-size: 0.82rem;
        line-height: 1.3;
    }
    /* Compact vertical option lists app-wide (stacked checkbox / radio rows, e.g. the
       charts sector/industry selector). The tall look is the GAP between stacked rows
       (Streamlit's ~1rem default) plus each row's own height — halve the gap and trim
       the per-row footprint. */
    [data-testid="stMain"] [data-testid="stVerticalBlock"] { gap: 0.45rem !important; }
    [data-testid="stMain"] [role="radiogroup"] { gap: 0.1rem !important; }
    [data-testid="stMain"] .stCheckbox { min-height: 0; }
    [data-testid="stMain"] .stCheckbox label { min-height: 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

# st.navigation lets pages live under ui/pages/ rather than a top-level pages/ dir.
nav = st.navigation(
    [
        st.Page("ui/pages/fetch_control.py", title="Fetch Control", icon="⬇️"),
        st.Page("ui/pages/filter.py", title="Filter", icon="🔎"),
        # url_path pinned: filter runs open in their own tabs at /output?run=<id>,
        # so this URL is a contract (rename-proof) — see ui/output_runs.py.
        st.Page("ui/pages/output.py", title="Output", icon="📊", url_path="output"),
        # url_path pinned: chart actions open in their own tab at /charts?view=…&symbols=…
        # (an Output Action link) — this URL is a contract, like /output above.
        st.Page("ui/pages/charts.py", title="Charts", icon="📈", url_path="charts"),
        st.Page("ui/pages/calibration.py", title="Calibration", icon="🎚️"),
        st.Page("ui/pages/settings_page.py", title="Settings", icon="⚙️"),
    ]
)

nav.run()
