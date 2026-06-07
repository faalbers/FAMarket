"""
Fundamental metrics (Topic 4.1). Raw numeric values only -> analysis.db.

Categories:
  * Valuation:      P/E, Fwd P/E, PEG, P/B, P/S, P/FCF, EV/EBITDA, EV/Revenue
  * Profitability:  ROE, ROA, ROIC, gross/op/net margin, FCF margin, EPS
  * Growth:         Revenue, EPS, FCF, Book Value (1y/3y/5y CAGR + YoY quarterly),
                    each with polyfit-residuals volatility %, R², CV; gated by
                    available history (NULL where insufficient).
  * Income:         dividend yield (TTM/annual/quarterly), growth rate, payout
                    ratio, consecutive growth years, consistency, coverage ratio.
                    Yield from yfinance history() Dividends column (no extra API).
  * Financial Health: D/E, current, quick, interest coverage, Debt/EBITDA,
                    cash ratio, Altman Z-Score.

SKELETON — Phase 2.
"""

from __future__ import annotations
