"""REIT funds from operations and the payout on it (fix list §21, Group H part 3c).

A REIT's net income is crushed by property depreciation, so its payout on net
income (`div_payout_ratio`) reads as unsafe when it is not. FFO adds that back.

    ffo = net income to common
          + depreciation and amortization
          - gains on sales (property, business / real-estate entity, investments)
          + impairments (write-downs)
    ffo_payout = common dividends paid / ffo * 100

Equity REITs only (company type "reit"); mortgage REITs and everything else get
NULL. Latest fiscal year, not trailing quarters: Yahoo's quarterly rows hold the
depreciation line for ~58% of large REITs and the write-down line for ~24%, and a
missing quarter line can't be told from a zero. The newest fiscal year must carry
net income and depreciation (EDGAR backfill years carry no depreciation, so FFO
starts with Yahoo's ~5 years).

Rough, not NAREIT FFO as reported:
  * all depreciation, not only real estate; no adjustment for joint ventures;
  * a missing gain or write-down line counts as 0, so FFO errs high there;
  * Yahoo files most REITs' property-sale gains under `gain_on_sale_of_security`
    (Prologis, Realty Income, Alexandria); Public Storage's 2022 sale of its PS
    Business Parks stake sits under `gain_on_sale_of_business`.
Checked against reported figures in the done file (Round 14).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from analysis_layer import health

# Each gain as (income-statement line, cash-flow line). The cash-flow line is
# the same item with the opposite sign (a reconciliation adjustment), so it is
# only a fallback, never added on top (Public Storage 2022 carries both).
_GAINS = (("gain_on_sale_of_ppe", "gain_loss_on_sale_of_ppe"),
          ("gain_on_sale_of_business", "gain_loss_on_sale_of_business"),
          ("gain_on_sale_of_security", "gain_loss_on_investment_securities"))
_IMPAIRMENT = ("asset_impairment_charge", "impairment_of_capital_assets")

FIELDS = ("net_income", "net_income_common_stockholders", "preferred_stock_dividends",
          "reconciled_depreciation", "depreciation_and_amortization",
          *[f for pair in _GAINS for f in pair], *_IMPAIRMENT,
          "common_stock_dividend_paid", "cash_dividends_paid", "preferred_stock_dividend_paid")

COLUMNS = ("ffo", "ffo_payout")

# The cash-flow preferred split is distrusted when it is above 3x the income
# statement's preferred dividends and above 5% of all dividends paid.
PREF_MISMATCH = 3.0
PREF_SLACK = 0.05


def _v(row: pd.Series, field: str) -> float:
    x = row.get(field, np.nan)
    return float(x) if x is not None and pd.notna(x) else math.nan


def _first(row: pd.Series, *fields: str) -> float:
    for f in fields:
        x = _v(row, f)
        if not math.isnan(x):
            return x
    return math.nan


def compute(tab: pd.DataFrame | None, ctype: str | None) -> dict:
    """{ffo, ffo_payout} from the newest fiscal year of a prebuilt annual table."""
    out = {c: math.nan for c in COLUMNS}
    if ctype != health.REIT or tab is None or tab.empty:
        return out
    row = tab.iloc[-1]
    ni = _v(row, "net_income_common_stockholders")
    if math.isnan(ni):
        ni = _v(row, "net_income")
        pref = _v(row, "preferred_stock_dividends")
        ni = ni - pref if not math.isnan(pref) else ni
    da = _first(row, "reconciled_depreciation", "depreciation_and_amortization")
    if math.isnan(ni) or math.isnan(da):
        return out
    gains = 0.0
    for income_line, cash_line in _GAINS:
        g = _v(row, income_line)
        if math.isnan(g):
            g = -_v(row, cash_line)
        gains += 0.0 if math.isnan(g) else g
    imp = _first(row, *_IMPAIRMENT)
    ffo = ni + abs(da) - gains + (0.0 if math.isnan(imp) else abs(imp))
    out["ffo"] = ffo

    divs = _common_dividends(row)
    if not math.isnan(divs) and ffo > 0:
        out["ffo_payout"] = divs / ffo * 100
    return out


def _common_dividends(row: pd.Series) -> float:
    """Common dividends paid (positive). Yahoo's common line, unless its preferred
    split disagrees with the income statement's preferred dividends: Simon
    Property 2025 shows 2.79B "preferred" paid against 3M on the income statement
    (the common line holds 0.44B of a 3.23B total). Then total - that preferred."""
    total = abs(_v(row, "cash_dividends_paid"))
    common = abs(_v(row, "common_stock_dividend_paid"))
    pref_paid = abs(_v(row, "preferred_stock_dividend_paid"))
    pref_is = abs(_v(row, "preferred_stock_dividends"))
    if not math.isnan(total) and not math.isnan(pref_is):
        split = total - common if not math.isnan(common) else pref_paid
        if not math.isnan(split) and split > max(PREF_MISMATCH * pref_is, PREF_SLACK * total):
            return max(total - pref_is, 0.0)
    if not math.isnan(common):
        return common
    if math.isnan(total):
        return math.nan
    return max(total - (0.0 if math.isnan(pref_paid) else pref_paid), 0.0)
