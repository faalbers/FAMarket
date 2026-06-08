"""
Hover-hint definitions for every screening parameter (Topics 4.2 & 5).

UI shows SHORT parameter names; the full meaning lives here and is shown as a
tooltip on hover (~0.5-1s delay) via Streamlit's `help=` argument. Editable by
hand or via Claude Code without touching any UI code.

Each hint is a structured dict:
  - name         : short label shown in the UI
  - category     : group it belongs to (Valuation/Technical/Income/...)
  - unit         : unit of the value AS STORED in analysis.db (see below)
  - what_it_is   : plain-language definition
  - how_to_use   : what good/bad values look like and the action they imply
  - vs_peers     : whether/why to compare against sector/industry median

`unit` convention — every param declares one so the UI can render and compare
correctly (append "%", align columns, format axes). Controlled vocabulary:
  - "%"  percentage. Stored AS A PERCENT NUMBER, not a fraction: 12.5 means
         12.5%, not 0.125. metrics.py multiplies yfinance's decimal margins/
         ratios (ROE, margins, yields, growth) by 100 on write, matching
         ROADMAP's "... x 100" yield formulas.
  - "x"  a multiple / ratio (P/E, P/S, EV/EBITDA, D/E) — unitless count of times.
  - "$"  currency per share or absolute (price, intrinsic value, EPS).
  - "yr" a count of years (consecutive dividend-growth years).
  - ""   unitless index or text classification (RSI 0-100, trend, crossover).

The UI renders titles bold with indented body text; use a list for body when
multiple points need explaining. This file starts with a few representative
entries — fill in the rest (with `unit`) as each metric is implemented.
"""

from __future__ import annotations

# param_key -> {"name": short label, "category": group, + 3 hint sections}
PARAM_HINTS: dict[str, dict] = {
    "pe": {
        "name": "P/E",
        "category": "Valuation",
        "unit": "x",
        "what_it_is": "Price divided by trailing 12-month earnings per share.",
        "how_to_use": [
            "Lower can mean cheaper, but very low may signal trouble.",
            "Negative means no earnings — read alongside growth and margins.",
        ],
        "vs_peers": "Yes — P/E is only meaningful relative to sector/industry.",
    },
    "rsi_14": {
        "name": "RSI(14)",
        "category": "Technical",
        "unit": "",
        "what_it_is": "14-day Relative Strength Index, momentum on a 0-100 scale.",
        "how_to_use": [
            ">70 often overbought, <30 often oversold.",
            "For long-term screening, use as confirmation, not a trigger.",
        ],
        "vs_peers": "No — RSI is self-relative, not a peer comparison.",
    },
    "div_yield_ttm": {
        "name": "Yield TTM",
        "category": "Income",
        "unit": "%",
        "what_it_is": "Sum of dividends paid in the last 365 days / current price.",
        "how_to_use": [
            "Higher pays more income; unusually high can signal a falling price.",
            "Pair with payout ratio and consecutive growth years for safety.",
        ],
        "vs_peers": "Yes — compare within sector; norms differ a lot by industry.",
    },
    "rs_rank": {
        "name": "RS Rank",
        "category": "Technical",
        "unit": "",
        "what_it_is": "IBD-style 0-99 rank of weighted trailing return vs the whole universe.",
        "how_to_use": [
            "Higher means stronger price performance than most other symbols.",
            "80+ is market-leading; NULL when under ~1 year of price history.",
        ],
        "vs_peers": "No — it is already a rank against the entire universe.",
    },
    "overall_score": {
        "name": "Overall",
        "category": "Score",
        "unit": "",
        "what_it_is": "0-100 blend of the five category scores (Quality/Growth/Momentum/Value/Income).",
        "how_to_use": [
            "Higher is a stronger all-round profile on current weights.",
            "Sort to triage, then read the category scores to see why.",
        ],
        "vs_peers": "No — it is already a cross-universe percentile blend.",
    },
    "value_score": {
        "name": "Value",
        "category": "Score",
        "unit": "",
        "what_it_is": "0-100 percentile of valuation metrics, ranked within the industry (then sector).",
        "how_to_use": ["Higher means cheaper than industry/sector peers on the weighted multiples."],
        "vs_peers": "No — it is already peer-relative (industry, then sector).",
    },
    "quality_score": {
        "name": "Quality",
        "category": "Score",
        "unit": "",
        "what_it_is": "0-100 percentile of profitability and balance-sheet strength, within the industry (then sector).",
        "how_to_use": ["Higher means more profitable / financially sound than industry/sector peers."],
        "vs_peers": "No — it is already peer-relative (industry, then sector).",
    },
    "growth_score": {
        "name": "Growth",
        "category": "Score",
        "unit": "",
        "what_it_is": "0-100 percentile of revenue/EPS/FCF growth across the universe.",
        "how_to_use": ["Higher means faster, steadier growth than most of the universe."],
        "vs_peers": "No — it is already a cross-universe percentile.",
    },
    "momentum_score": {
        "name": "Momentum",
        "category": "Score",
        "unit": "",
        "what_it_is": "0-100 percentile of price trend (RS rank, MA distance, 52-week) across the universe.",
        "how_to_use": ["Higher means stronger recent price action than most of the universe."],
        "vs_peers": "No — it is already a cross-universe percentile.",
    },
    "income_score": {
        "name": "Income",
        "category": "Score",
        "unit": "",
        "what_it_is": "0-100 percentile of dividend yield, growth and safety across the universe.",
        "how_to_use": ["Higher means a stronger, better-covered income profile; NaN for non-payers."],
        "vs_peers": "No — it is already a cross-universe percentile.",
    },
}


def get_hint(param_key: str) -> dict | None:
    """Return the hint dict for a parameter, or None if undefined yet."""
    return PARAM_HINTS.get(param_key)
