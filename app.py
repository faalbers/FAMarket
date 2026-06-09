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
enable_autoshutdown(on_shutdown=cancel.stop_for_shutdown)

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
    </style>
    """,
    unsafe_allow_html=True,
)

# st.navigation lets pages live under ui/pages/ rather than a top-level pages/ dir.
nav = st.navigation(
    [
        st.Page("ui/pages/fetch_control.py", title="Fetch Control", icon="⬇️"),
        st.Page("ui/pages/filter.py", title="Filter", icon="🔎"),
        st.Page("ui/pages/output.py", title="Output", icon="📊"),
        st.Page("ui/pages/calibration.py", title="Calibration", icon="🎚️"),
        st.Page("ui/pages/settings_page.py", title="Settings", icon="⚙️"),
    ]
)

nav.run()
