"""
Value-level cleaning rules for fetched data (data-system fix list, Group A).

Each rule turns a known Yahoo placeholder or impossible value into NULL (or into
the value it should have been), following the "NULL = not applicable" convention.
The same functions run in two places, so the two can never drift:

  * at import — the yfinance / EDGAR sanitize steps, on every new fetch;
  * once, over the stored rows — ``scripts/clean_stored_data.py``.

Every function takes a DataFrame, returns ``(cleaned_frame, counts)`` where
``counts`` maps a rule name to the number of cells it changed. Columns a frame
doesn't have are skipped, so a rule is safe on any subset of a table. Import
callers ignore the counts (sanitize fixes are silent, per the logging policy);
the cleanup script reports them.

Rules and the evidence behind them: ``docs/fix_list_for_data_system_done.md``.
"""

from __future__ import annotations

import pandas as pd

Counts = dict[str, int]

# --- quotes ----------------------------------------------------------------- #
# Upper bounds past which a Yahoo ratio is a data error, not a real value.
BETA_ABS_MAX = 10.0          # |beta| above this is noise (ELOX showed -169,205)
PAYOUT_RATIO_MAX = 10.0      # a fraction: 10 = 1,000% of earnings
GROSS_MARGIN_MAX = 1.0       # a fraction: gross profit can't exceed revenue

# --- estimates -------------------------------------------------------------- #
# Value columns that hold a placeholder 0 when no analyst covers the horizon,
# keyed by the analyst-count column that tells whether the row is real.
_ESTIMATE_GROUPS: dict[str, tuple[str, ...]] = {
    "rev_num_analysts": ("rev_avg", "rev_low", "rev_high", "rev_year_ago", "rev_growth"),
    "eps_num_analysts": ("eps_avg", "eps_low", "eps_high", "eps_year_ago", "eps_growth"),
}


def _null_where(df: pd.DataFrame, col: str, mask: pd.Series) -> int:
    """Set ``df[col]`` to NULL where ``mask`` is true; return how many cells changed."""
    mask = mask.fillna(False).astype(bool)
    n = int(mask.sum())
    if n:
        df[col] = df[col].astype(object)
        df.loc[mask, col] = None
    return n


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce")


def clean_quotes(df: pd.DataFrame) -> tuple[pd.DataFrame, Counts]:
    """Quote snapshot rules (fix list §5, §7, §25.5)."""
    counts: Counts = {}
    if "recommendationMean" in df.columns:
        # The scale is 1 (strong buy) .. 5 (sell); 0 is Yahoo's "no rating".
        counts["recommendationMean_zero"] = _null_where(
            df, "recommendationMean", _num(df, "recommendationMean") == 0)
    if "beta" in df.columns:
        counts["beta_out_of_range"] = _null_where(
            df, "beta", _num(df, "beta").abs() > BETA_ABS_MAX)
    if "payoutRatio" in df.columns:
        counts["payoutRatio_out_of_range"] = _null_where(
            df, "payoutRatio", _num(df, "payoutRatio") > PAYOUT_RATIO_MAX)
    if "grossMargins" in df.columns:
        counts["grossMargins_out_of_range"] = _null_where(
            df, "grossMargins", _num(df, "grossMargins") > GROSS_MARGIN_MAX)
    if "shortName" in df.columns:
        if "longName" not in df.columns:
            df["longName"] = None
        long_missing = df["longName"].isna() | (df["longName"].astype(str).str.strip() == "")
        short_present = df["shortName"].notna() & (df["shortName"].astype(str).str.strip() != "")
        fill = long_missing & short_present
        n = int(fill.sum())
        if n:
            df["longName"] = df["longName"].astype(object)
            df.loc[fill, "longName"] = df.loc[fill, "shortName"]
        counts["longName_from_shortName"] = n
    return df, counts


def clean_estimates(df: pd.DataFrame) -> tuple[pd.DataFrame, Counts]:
    """Analyst-estimate rules (fix list §5).

    A 0 in a revenue/EPS estimate column is a placeholder only when the horizon has
    no analysts (count 0 or missing). With analysts, 0 can be a real forecast — a
    pre-revenue biotech forecast at $0 revenue — so those rows are left alone.
    """
    counts: Counts = {}
    for count_col, value_cols in _ESTIMATE_GROUPS.items():
        if count_col in df.columns:
            no_analysts = _num(df, count_col).fillna(0) == 0
        else:
            no_analysts = pd.Series(True, index=df.index)
        n = 0
        for col in value_cols:
            if col in df.columns:
                n += _null_where(df, col, no_analysts & (_num(df, col) == 0))
        counts[f"{count_col.split('_')[0]}_zero_no_analysts"] = n
    return df, counts


def clean_financials(df: pd.DataFrame) -> tuple[pd.DataFrame, Counts]:
    """Statement rules (fix list §24).

    ``stockholders_equity`` = 0 is a placeholder: Yahoo returns it for partnerships
    (equity is "partners' capital") and some new listings, and a few EDGAR rows
    carry it too. Take ``common_stock_equity`` when it holds a non-zero figure
    (negative equity included), else store NULL.
    """
    counts: Counts = {"stockholders_equity_from_common": 0, "stockholders_equity_zero": 0}
    if "stockholders_equity" not in df.columns:
        return df, counts
    zero = (_num(df, "stockholders_equity") == 0).fillna(False)
    if not zero.any():
        return df, counts
    if "common_stock_equity" in df.columns:
        common = _num(df, "common_stock_equity")
        use_common = zero & common.notna() & (common != 0)
    else:
        common = pd.Series(float("nan"), index=df.index)
        use_common = pd.Series(False, index=df.index)
    to_null = zero & ~use_common
    df["stockholders_equity"] = df["stockholders_equity"].astype(object)
    df.loc[use_common, "stockholders_equity"] = common[use_common]
    df.loc[to_null, "stockholders_equity"] = None
    counts["stockholders_equity_from_common"] = int(use_common.sum())
    counts["stockholders_equity_zero"] = int(to_null.sum())
    return df, counts
