"""
Hover-hint definitions for every screening parameter (Topics 4.2 & 5).

UI shows SHORT parameter names; the full meaning lives here and is shown as a
tooltip on hover (~0.5-1s delay) via Streamlit's `help=` argument. Editable by
hand or via Claude Code without touching any UI code.

Each hint is a structured 3-section dict:
  - what_it_is   : plain-language definition
  - how_to_use   : what good/bad values look like and the action they imply
  - vs_peers     : whether/why to compare against sector/industry median

The UI renders titles bold with indented body text; use a list for body when
multiple points need explaining. This file starts with a few representative
entries — fill in the rest as each metric is implemented in the Analysis layer.
"""

from __future__ import annotations

# param_key -> {"name": short label, "category": group, + 3 hint sections}
PARAM_HINTS: dict[str, dict] = {
    "pe": {
        "name": "P/E",
        "category": "Valuation",
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
        "what_it_is": "Sum of dividends paid in the last 365 days / current price.",
        "how_to_use": [
            "Higher pays more income; unusually high can signal a falling price.",
            "Pair with payout ratio and consecutive growth years for safety.",
        ],
        "vs_peers": "Yes — compare within sector; norms differ a lot by industry.",
    },
}


def get_hint(param_key: str) -> dict | None:
    """Return the hint dict for a parameter, or None if undefined yet."""
    return PARAM_HINTS.get(param_key)
