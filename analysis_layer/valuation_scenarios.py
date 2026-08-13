"""
Valuation scenarios (bear / base / bull) — analysis_layer/valuation_scenarios.py.

Runs intrinsic_value.py's own Lynch/DCF/DDM formulas a second and third time
against a bear and a bull growth input instead of duplicating their math —
"change the assumptions, not the answer" (dev_docs/famarket_valuation_discussion.md).
Graham is base-only: it's pure trailing (EPS × book value/share), nothing
forward to flex, so it never contributes to fair_value_bear/fair_value_bull.
Discount rate and terminal growth stay CONSTANT across all three scenarios —
no data-driven signal exists for flexing WACC per stock, and no professional
standard was found for it either (2026-08 research); growth is the sole
scenario lever for this pass.

  * intrinsic_value_{lynch,dcf,ddm}_{bear,bull} — per-method scenario values.
    Bear/bull growth comes from that method's own trend-growth column ∓ its
    residual volatility (metrics._trend_stats: eps/fcf/div_growth_vol),
    scaled by settings.VALUATION_SCENARIO_VOL_MULTIPLIER — an uncertainty-
    scaled stand-in for the manual scenario-rebuild professionals do by hand,
    not a named industry technique (see settings.py comment). A steady grower
    gets a narrow spread; a noisy one gets a wide one, automatically.
    The bear growth may be NEGATIVE (floored at
    VALUATION_SCENARIO_BEAR_GROWTH_FLOOR), which the DCF/DDM handle as a
    starting rate that fades up toward terminal. Lynch cannot: fair P/E ≈
    growth% is undefined for a shrinking company, so its bear value goes NaN
    and the coherent-method-set rule below then drops Lynch from that
    symbol's bear AND bull blend. Intended, not a bug.
  * fair_value_bear / fair_value_bull — median of whichever methods are
    applicable to the symbol's type (same `_applicable_models` gate as the
    base `fair_value`), Graham excluded from both, and further restricted to
    methods that produce a POSITIVE value in base AND bear AND bull alike.
    BOTH ARE NULL when no applicable method can build a scenario at all (no
    usable trend/vol history) — deliberately, rather than echoing the base
    value back as a zero-width "range" that would read as certainty where
    there is no estimate. A NULL here means "no range available", which is a
    different and more honest statement than "bear equals bull".
    Without that last restriction a method can drop out of only one side —
    e.g. Lynch requires positive growth, so a stock whose bear growth floors
    to 0 loses Lynch from the bear median but keeps it in base/bull — which
    lets a low Lynch base value pull fair_value below fair_value_bear
    (discovered on a full-universe run, 2026-08-12: ~39% of computed triples
    had base sit outside [bear, bull] before this fix). Restricting to the
    same method set across all three scenarios makes bear ≤ base ≤ bull hold
    by construction: each method's own value is monotonic in its growth input
    (higher growth -> higher value, bounded by the method's own cap), and the
    median of coordinate-wise-larger values is itself weakly larger.
  * margin_of_safety_bear / margin_of_safety_bull — same formula as the
    existing `margin_of_safety`, against the bear/bull fair values.
  * valuation_guardrail_flag — True when either: the discount rate needed
    `_cost_of_equity`'s minimum-spread floor (the raw CAPM/WACC rate was
    already too close to — or below — terminal growth before that floor
    silently corrected it), or the assumed terminal ROIC−WACC spread
    (`roic_vs_wacc`) is wide enough that the Gordon terminal value is
    implicitly assuming an implausibly persistent excess return forever. A
    warning flag, not a hard block — matches this codebase's existing
    compute-and-reconcile pattern (log/flag divergence, still compute).
  * bear_flag_cash_conversion / bear_flag_moat_narrowing /
    bear_flag_interest_coverage / bear_flag_earnings_quality — visible, NOT
    applied, red flags from data FAMarket already computes (`ocf_to_ni_3y/5y`,
    `roic_vs_wacc`, `interest_coverage`, `beneish_m_score`). No professional
    framework exists for turning qualitative red flags into numeric bear-case
    deltas (2026-08 research found none) — so this ships them as reviewable
    data next to the valuation range instead of an invented, unvalidated
    formula that silently reshapes fair_value_bear.
  * bear_flag_count — how many of the 4 flags above are True (0–4).

Same currency-mismatch skip as intrinsic_value.py: when metrics fell back to
yfinance for valuation (`valuation_basis != "computed"`), every output here is
NaN/False, same as the base intrinsic values.
"""

from __future__ import annotations

import statistics

import pandas as pd

from config import settings
from analysis_layer import _periods as P
from analysis_layer import intrinsic_value as IV

_SCENARIO_MODEL_COLUMN = {
    "lynch": "intrinsic_value_lynch",
    "dcf": "intrinsic_value_dcf",
    "ddm": "intrinsic_value_ddm",
}


def _num(x) -> float:
    return float(x) if x is not None and pd.notna(x) else float("nan")


def _scenario_growth(m: dict, growth_key: str) -> tuple[float, float]:
    """(bear, bull) growth%, from `growth_key`'s trend ∓ its own residual
    volatility column (`{base}_vol`, e.g. eps_growth_trend -> eps_growth_vol).

    Bear is floored at `VALUATION_SCENARIO_BEAR_GROWTH_FLOOR`, which is
    NEGATIVE: since the DCF/DDM fade any starting rate up or down toward
    terminal growth, a negative start means "shrinks for a while, then
    stabilises" — a real bear story, not a shrinking-forever model. Bull is
    left unbounded here; each method applies its own ceiling
    (`DCF_FADE_START_CAP`, `LYNCH_GROWTH_CAP`) to whatever it's passed, so
    bounding twice would be redundant.

    NaN trend or vol (insufficient history, non-positive values) -> NaN
    scenario, which the downstream method handles the same way it already
    handles a NaN base-case trend.
    """
    trend = _num(m.get(growth_key))
    vol = _num(m.get(f"{growth_key.removesuffix('_trend')}_vol"))
    if pd.isna(trend) or pd.isna(vol):
        return float("nan"), float("nan")
    spread = vol * settings.VALUATION_SCENARIO_VOL_MULTIPLIER
    return max(trend - spread, settings.VALUATION_SCENARIO_BEAR_GROWTH_FLOOR), trend + spread


def _guardrail_flag(quote: pd.Series | None, risk_free: float, wacc: float, m: dict) -> bool:
    raw_discount = IV._raw_discount(quote, risk_free, wacc)
    gt = settings.DCF_TERMINAL_GROWTH
    floored = pd.notna(raw_discount) and raw_discount - gt < settings.DCF_MIN_DISCOUNT_SPREAD

    roic_gap = _num(m.get("roic_vs_wacc"))
    spread_too_wide = pd.notna(roic_gap) and roic_gap > settings.GUARDRAIL_ROIC_WACC_SPREAD_MAX

    return bool(floored or spread_too_wide)


def _qualitative_flags(m: dict) -> dict:
    cash_conv = _num(m.get("ocf_to_ni_3y"))
    if pd.isna(cash_conv):
        cash_conv = _num(m.get("ocf_to_ni_5y"))
    cash_flag = bool(pd.notna(cash_conv) and cash_conv < settings.BEAR_FLAG_CASH_CONVERSION_MIN)

    gap = _num(m.get("roic_vs_wacc"))
    moat_flag = bool(pd.notna(gap) and gap < settings.BEAR_FLAG_MOAT_GAP_MIN)

    coverage = _num(m.get("interest_coverage"))
    coverage_flag = bool(pd.notna(coverage) and coverage < settings.BEAR_FLAG_INTEREST_COVERAGE_MIN)

    beneish = _num(m.get("beneish_m_score"))
    beneish_flag = bool(pd.notna(beneish) and beneish > settings.BEAR_FLAG_BENEISH_THRESHOLD)

    return {
        "bear_flag_cash_conversion": cash_flag,
        "bear_flag_moat_narrowing": moat_flag,
        "bear_flag_interest_coverage": coverage_flag,
        "bear_flag_earnings_quality": beneish_flag,
        "bear_flag_count": cash_flag + moat_flag + coverage_flag + beneish_flag,
    }


def compute(
    symbol: str,
    fin: P.SymbolPeriods,
    quote: pd.Series | None,
    price: float,
    m: dict,
    risk_free: float,
    iv: dict,
    screen_type: str | None = None,
    regulated: bool = False,
) -> dict:
    """Bear/base/bull scenario columns for one symbol. Mirrors
    `intrinsic_value.compute()`'s signature plus `iv` — that function's OWN
    output dict, needed to check which methods have a valid base value (see
    module docstring for why the fair_value_bear/bull method set has to match
    it). Call this right after `intrinsic_value.compute()` and pass the SAME
    `m` (already has `wacc`/`roic_vs_wacc`/etc. from `metrics.compute()`).
    """
    out: dict = {
        "intrinsic_value_lynch_bear": float("nan"), "intrinsic_value_lynch_bull": float("nan"),
        "intrinsic_value_dcf_bear": float("nan"), "intrinsic_value_dcf_bull": float("nan"),
        "intrinsic_value_ddm_bear": float("nan"), "intrinsic_value_ddm_bull": float("nan"),
        "fair_value_bear": float("nan"), "fair_value_bull": float("nan"),
        "margin_of_safety_bear": float("nan"), "margin_of_safety_bull": float("nan"),
        "valuation_guardrail_flag": False,
        "bear_flag_cash_conversion": False,
        "bear_flag_moat_narrowing": False,
        "bear_flag_interest_coverage": False,
        "bear_flag_earnings_quality": False,
        "bear_flag_count": 0,
    }
    if m.get("valuation_basis") != "computed":  # currency mismatch -> not comparable
        return out

    out.update(_qualitative_flags(m))

    eps = _num(m.get("eps_ttm"))
    shares = IV._shares(quote, fin)
    wacc_pct = _num(m.get("wacc"))
    wacc = wacc_pct / 100 if pd.notna(wacc_pct) else float("nan")

    out["valuation_guardrail_flag"] = _guardrail_flag(quote, risk_free, wacc, m)

    # A NaN scenario growth means the scenario is UNCOMPUTABLE for this method
    # (no usable trend/vol history), so its value stays NaN. The models must not
    # be called with it: their `growth` parameter falls back to the base trend
    # when handed NaN, which would silently return the base value and publish it
    # as both "bear" and "bull" — a zero-width range that reads as certainty
    # where there is in fact no estimate at all. NULL says that honestly.
    # (Verified 2026-08-13: this fallback accounted for 79% of all zero-width
    # ranges universe-wide, dwarfing the growth-cap clipping the fade addressed.)
    bear_floor = settings.VALUATION_SCENARIO_BEAR_GROWTH_FLOOR / 100

    eps_bear, eps_bull = _scenario_growth(m, "eps_growth_trend")
    if pd.notna(eps_bear):
        out["intrinsic_value_lynch_bear"] = IV._lynch(eps, m, growth=eps_bear)
    if pd.notna(eps_bull):
        out["intrinsic_value_lynch_bull"] = IV._lynch(eps, m, growth=eps_bull)

    # The bear calls pass the negative floor through: _dcf/_ddm bound their own
    # start rate at 0 by default (the base case), which would otherwise clamp a
    # negative bear growth straight back to flat.
    fcf_bear, fcf_bull = _scenario_growth(m, "fcf_growth_trend")
    if pd.notna(fcf_bear):
        out["intrinsic_value_dcf_bear"] = IV._dcf(fin, m, quote, shares, risk_free,
                                                  growth=fcf_bear, wacc=wacc, growth_floor=bear_floor)
    if pd.notna(fcf_bull):
        out["intrinsic_value_dcf_bull"] = IV._dcf(fin, m, quote, shares, risk_free,
                                                  growth=fcf_bull, wacc=wacc)

    div_bear, div_bull = _scenario_growth(m, "div_growth_trend")
    if pd.notna(div_bear):
        out["intrinsic_value_ddm_bear"] = IV._ddm(m, quote, risk_free,
                                                  growth=div_bear, wacc=wacc, growth_floor=bear_floor)
    if pd.notna(div_bull):
        out["intrinsic_value_ddm_bull"] = IV._ddm(m, quote, risk_free, growth=div_bull, wacc=wacc)

    applicable = IV._applicable_models(screen_type, regulated) - {"graham"}

    def _valid_positive(x) -> bool:
        return pd.notna(x) and x > 0

    # Same method set feeds every scenario (see module docstring) -- a method
    # only counts if its base, bear AND bull values are all positive numbers.
    usable = [
        model for model in applicable
        if _valid_positive(iv.get(_SCENARIO_MODEL_COLUMN[model]))
        and _valid_positive(out[f"{_SCENARIO_MODEL_COLUMN[model]}_bear"])
        and _valid_positive(out[f"{_SCENARIO_MODEL_COLUMN[model]}_bull"])
    ]

    for scenario in ("bear", "bull"):
        estimates = [out[f"{_SCENARIO_MODEL_COLUMN[model]}_{scenario}"] for model in usable]
        if estimates:
            fair = statistics.median(estimates)
            out[f"fair_value_{scenario}"] = fair
            if price and price > 0:
                out[f"margin_of_safety_{scenario}"] = (fair - price) / fair * 100

    return out
