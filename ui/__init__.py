"""
UI Layer (Phase 3) — local Streamlit app, launched with `streamlit run app.py`.

Pages (see ui/pages/):
  * Fetch Control — Group 1 (Polygon, EDGAR) then Group 2 (yfinance, FMP,
    E*Trade, FRED); Group 2 locked until Group 1 completes; live log output;
    each API runnable independently.
  * Filter — block-based AND/OR builder, security-type selector, .filt load/save.
  * Output — results table, multi-column sort, multi-select, grouped Action menu.
  * Calibration — in-UI peak-detection tuning tool (saves PEAK_* to config).
  * Settings — edits config/settings.py (grouped collapsible sections).
"""
