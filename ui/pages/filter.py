"""
Filter page (Topic 5). Phase 3 skeleton.

Single unified, type-adaptive interface:
  * Security Type selector (collapsible, Common Stock default; multi-select shows
    only shared parameters).
  * Block-based AND/OR builder. Row: [⏸][+][-][Parameter ▾][Operator ▾][V/P][Value].
    Top-level blocks ANDed; one level of OR children as fallbacks.
  * Operators: >, <, <=, >=, =, !=, between (inclusive), is null, is not null,
    starts_with, contains. NULL fails a filter (except the null operators).
  * Load / Add / Save / Clear of .filt files (settings.FILTERS_DIR).
  * Run Filter -> navigates to the Output page.
"""

import streamlit as st

st.title("Filter")
st.info("Phase 3 — block-based filter builder not implemented yet.")
