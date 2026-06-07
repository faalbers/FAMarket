"""
Fetch Control panel (Topic 8 — FETCH CONTROL PANEL). Phase 3 skeleton.

  Group 1 — Symbol Discovery (sequential, first): Polygon, SEC EDGAR.
  Group 2 — Data Fetch (locked until Group 1 done): yfinance, FMP, E*Trade, FRED.
  Live log output during the run; each API can be re-run independently.
  Triggers a rotating backup (core.backup.backup_all) before the run starts.
"""

import streamlit as st

st.title("Fetch Control")
st.info("Phase 1/3 — fetch orchestration UI not implemented yet.")
