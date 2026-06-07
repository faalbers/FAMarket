"""
FAMarket — Stock Screening System.
Streamlit entry point. Launch with:

    streamlit run app.py

Wires up runtime directories and logging, then registers the multipage nav.
Page bodies are Phase 3 skeletons under ui/pages/.
"""

from __future__ import annotations

import streamlit as st

from config import settings
from core.logging_config import setup_logging
from core.net import configure_tls

settings.ensure_runtime_dirs()
configure_tls()
setup_logging()

st.set_page_config(page_title="FAMarket — Stock Screener", layout="wide")

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
