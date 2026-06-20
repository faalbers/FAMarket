"""
Rule-specific hints — the plain-language "why this rule" text for the Scoring Rules
page's per-parameter info box.

DELIBERATELY SEPARATE from `config/param_hints.py` (which stays the one canonical hint
registry for Filter / Output / chart tooltips). This registry holds only the *rule
RATIONALE* (shape + anchor + why); the metric's NAME and definition always come from
`param_hints` so the info box is metric-specific even when several metrics share one
rule rationale (e.g. all the valuation multiples are "lower-better, vs peers").

When an insight here is useful beyond rules, it is ALSO backported into `param_hints.py`
under `how_to_use` / `vs_peers` — but the two registries stay separate (see the plan).

`rule_hint_markdown(metric, rule)` composes: the metric's own name + definition
(param_hints) → the rule rationale (here, family-shared via aliases) → a live one-liner
describing the metric's CURRENT rule. So switching metrics always updates the whole box.
"""

from __future__ import annotations

from config import param_hints

# metric -> the RULE RATIONALE only (no metric name/definition — that comes from
# param_hints). Dyslexia-friendly: short sentences, bold anchors, white space. Family
# members share one entry via _ALIASES below, so the text must stay metric-AGNOSTIC.
RULE_HINTS: dict[str, str] = {
    # -- Valuation: plain multiples (pe family) -------------------------------
    "pe": "**Lower is better** (cheaper), judged **vs peers** — what's 'cheap' depends "
          "on the sector. A negative reading is a *loss*, not a bargain, so it's dropped.",
    "peg": "**Lower is better**, but anchored on an **absolute 1.0 line** (Lynch's fair "
           "value), *not* peers — PEG already bakes growth in, so it compares on its own "
           "scale. Below 1 = cheap for its growth. Negative is meaningless, so it's dropped.",
    "margin_of_safety": "**Higher is better**, anchored on an **absolute 0**: above 0 = "
                        "trading below fair value (good), below 0 = overvalued.\n\n"
                        "Negatives are **kept** — being overvalued is real information.",
    # -- Profitability (roe family) ------------------------------------------
    "roe": "**Higher is better**, judged **vs peers** — margins especially differ by "
           "industry (a 10% net margin is great for a grocer, poor for software).\n\n"
           "Negatives are **kept** — losing money is genuinely bad, not 'not applicable'.",
    # -- Balance sheet -------------------------------------------------------
    "debt_to_equity": "**Lower is better** (safer), judged **vs peers** — capital-heavy "
                      "sectors carry more debt.\n\n"
                      "*Could* become a sweet-spot later (a little debt is efficient).",
    "current_ratio": "A **sweet-spot**: too **low** (<1) = can't cover what's due (risky); "
                     "the **healthy middle** = good; too **high** = idle cash earning "
                     "nothing.\n\nSo **both extremes read weak** — the band is the strong zone.",
    "altman_z": "**Higher is better**, anchored on an **absolute ~3.0**: above 3 = safe "
                "zone, 1.8–3 = grey, below 1.8 = distress.",
    # -- Growth (revenue_cagr_3y family) -------------------------------------
    "revenue_cagr_3y": "**Higher is better**, judged **across the whole universe** (not "
                       "peers) — show me the fastest growers, period. Shrinking (negative) "
                       "is real, bad info, so it's kept.\n\n"
                       "Note: the *steadiness* metrics (growth volatility / variability) "
                       "flip to **lower-is-better**; growth-trend R² stays higher-better.",
    # -- Income --------------------------------------------------------------
    "div_yield_ttm": "A **sweet-spot** on an **absolute** band (income is an absolute goal "
                     "— what counts is the actual yield, not 'high for the sector').\n\n"
                     "Too low = little income. **Very high is a yield-trap** — the price "
                     "crashed because a cut is expected. REITs/BDCs/CEFs legitimately run "
                     "higher (handled by a per-type override).",
    "div_payout_ratio": "A **sweet-spot**: too **low** = stingy; **~30–60%** = healthy & "
                        "sustainable; **over ~100%** = paying more than it earns.\n\n"
                        "**REITs** legally pay ~90%+, so they get a higher band (per-type "
                        "override).",
    "div_coverage": "**Higher is better**, with an **absolute floor at 1** — below 1 it "
                    "pays out more than it earns.",
    # -- Momentum / technical ------------------------------------------------
    "rs_rank": "**Higher is better**, used **as-is** (it's already a 0–99 rank).\n\n"
               "Deep-pass idea: a **peer** anchor would mean 'strongest in its own sector'.",
    "price_vs_ma_50": "**Higher is better**, anchored on an **absolute 0%**: above the "
                      "moving average (uptrend) = good, below = weak.",
    "pct_from_52w_high": "**Higher is better** (closer to the year's high = stronger "
                         "momentum), anchored at **0** (the high).",
    "rsi_14": "**Strategy-dependent!** Drafted as a **sweet-spot ~40–70** (healthy "
              "strength, not overheated). Over 70 = overbought, under 30 = oversold.\n\n"
              "A momentum trader might prefer higher-better — tune it to your style.",
    "bb_pct": "Like RSI, an overbought/oversold gauge — **strategy-dependent**. Drafted "
              "as a **sweet-spot** (the healthy middle of the band).",
    "macd_hist": "**Higher is better**, anchored at **0** (positive = bullish). More a "
                 "*timing* tool than a quality tool — so weight it lightly.",
}

# Metrics that SHARE one rule rationale (the text is metric-agnostic, so this is safe;
# the metric's own name/definition is always shown from param_hints).
_ALIASES = {
    "forward_pe": "pe", "pb": "pe", "ps": "pe", "p_fcf": "pe", "ev_ebitda": "pe",
    "ev_revenue": "pe",
    "roa": "roe", "roic": "roe", "gross_margin": "roe", "operating_margin": "roe",
    "net_margin": "roe", "fcf_margin": "roe",
    "debt_to_ebitda": "debt_to_equity", "interest_coverage": "debt_to_equity",
    "quick_ratio": "current_ratio", "cash_ratio": "current_ratio",
    "revenue_cagr_1y": "revenue_cagr_3y", "revenue_cagr_5y": "revenue_cagr_3y",
    "eps_cagr_1y": "revenue_cagr_3y", "eps_cagr_3y": "revenue_cagr_3y",
    "eps_cagr_5y": "revenue_cagr_3y", "fcf_cagr_3y": "revenue_cagr_3y",
    "book_value_cagr_3y": "revenue_cagr_3y", "revenue_yoy_q": "revenue_cagr_3y",
    "eps_yoy_q": "revenue_cagr_3y",
    "price_vs_ma_150": "price_vs_ma_50", "price_vs_ma_200": "price_vs_ma_50",
    "div_growth_5y": "div_payout_ratio", "div_consecutive_years": "div_payout_ratio",
    "div_consistency": "div_payout_ratio",
}

_SHAPE_WORDS = {"higher_better": "higher is better", "lower_better": "lower is better",
                "sweet_spot": "a sweet-spot (middle is best)"}


def get_rule_hint(metric: str) -> str | None:
    """The rule rationale for a metric (following family aliases), or None."""
    if metric in RULE_HINTS:
        return RULE_HINTS[metric]
    alias = _ALIASES.get(metric)
    return RULE_HINTS.get(alias) if alias else None


def _current_rule_line(rule: dict | None) -> str:
    """A one-line summary of the CURRENT rule, e.g. 'Current rule: a sweet-spot, ideal
    30–60, absolute.'"""
    if not rule:
        return ""
    shape = _SHAPE_WORDS.get(rule.get("shape", ""), rule.get("shape", ""))
    bits = [shape]
    if rule.get("shape") == "sweet_spot" and rule.get("lo") is not None:
        bits.append(f"ideal {rule['lo']:g}–{rule['hi']:g}")
    anchor = rule.get("anchor")
    if anchor == "absolute" and rule.get("value") is not None:
        bits.append(f"line at {rule['value']:g}")
    elif anchor:
        bits.append(f"{anchor}-anchored")
    return "**Current rule:** " + ", ".join(b for b in bits if b) + "."


def rule_hint_markdown(metric: str, rule: dict | None = None) -> str:
    """The info-box markdown, ALWAYS metric-specific:
      1. the metric's own name + definition (from the canonical param_hints),
      2. the rule rationale (family-shared, metric-agnostic),
      3. a live one-liner describing the metric's CURRENT rule.
    Falls back to param_hints `how_to_use` when no bespoke rationale exists."""
    parts: list[str] = []
    # 1) metric-specific header + definition — this is what makes the box update per metric
    defn = param_hints.hint_markdown(metric, header=True, sections=("what_it_is",))
    if defn:
        parts.append(defn)
    # 2) rule rationale (or fall back to the canonical how-to-use)
    rationale = get_rule_hint(metric)
    if rationale:
        parts.append(rationale)
    else:
        how = param_hints.hint_markdown(metric, header=False, sections=("how_to_use",))
        if how:
            parts.append(how)
    # 3) the live current-rule line
    line = _current_rule_line(rule)
    if line:
        parts.append(line)
    return "\n\n".join(parts)
