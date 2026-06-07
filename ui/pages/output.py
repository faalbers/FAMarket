"""
Output page (Topic 6). Phase 3 skeleton.

  * Results table: Symbol (e.g. "AAPL (stock)"), Company, Sector/Industry, then
    selected parameter columns. Add/remove columns via the searchable dropdown;
    save/load column sets as .prms (settings.COLUMN_SETS_DIR).
  * Multi-column sort (click = primary, Shift+click = secondary, ...).
  * Multi-select rows; Action menu (opens a new browser tab per action), grouped:
    Normalized Charts / Fundamentals / Dividends / Analyze on external site.
  * Plotly charts; color-blind safe palette (settings.CHART_COLORWAY); normalized
    price chart starts all symbols at 100%, inline end labels, gaps as breaks.
"""

import streamlit as st

st.title("Output")
st.info("Phase 3 — results table and action menu not implemented yet.")
