"""
Analysis Layer (Phase 2).

Runs automatically after the fetch completes. Reads the Phase 1 databases,
computes every metric, and FULLY REBUILDS analysis.db each run (clean slate via
Database.replace — no delta tracking). Only processes symbols where
is_active=True AND is_validated=True.

What lands in analysis.db (raw numeric values — the filter does the comparisons,
not pre-computed booleans):
  * metrics.py          — valuation, profitability, growth, income, health
  * technical.py        — MAs, RSI, MACD, Bollinger, ATR, volume, 52w, RS rank,
                          peak-detection trend (scipy.signal.find_peaks)
  * peers.py            — _vs_sector / _vs_industry % differences (no medians stored)
  * intrinsic_value.py  — Graham, Lynch, simple DCF, margin_of_safety
  * scoring.py          — category scores (0-100) + weighted Overall Score

All price calcs use adj_close of the last completed trading session (never
intraday). Growth metrics carry CAGR + polyfit-residuals volatility % + R² + CV,
gated by available history (1y / 3y / 5y).
"""
