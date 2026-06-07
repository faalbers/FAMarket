"""
Technical indicators (Topic 4.2). All from adj_close; current-state, weekly recalc.

  * MAs 50/150/200 + price-vs-MA %
  * rsi_14
  * MACD 12/26/9: macd_line, macd_signal, macd_hist, macd_crossover (text,
    persists 5 trading days), macd_hist_trend (growing/shrinking/flat)
  * Bollinger 20/2: bb_upper/middle/lower, bb_width, bb_pct, bb_position (text),
    bb_squeeze (bool)
  * Volume: vol_20d_avg, vol_ratio, vol_trend (text)
  * atr_pct
  * pct_from_52w_high, pct_from_52w_low
  * rs_rank: 0-99 weighted percentile vs universe (40/20/20/20 over last 4 quarters),
    computed LAST, NULL if < settings.RS_RANK_MIN_HISTORY_DAYS
  * trend: peak detection only (scipy.signal.find_peaks on prices and inverted
    prices) -> strong_uptrend / weak_uptrend / sideways / weak_downtrend /
    strong_downtrend. Calibrated via the in-UI tool (settings.PEAK_*).

SKELETON — Phase 2.
"""

from __future__ import annotations
