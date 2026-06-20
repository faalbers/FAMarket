"""
Scoring Rules page (left-menu) — define how each parameter is judged strong vs weak.

Each metric gets a RULE (shape + anchor + band/line) that decides its 0-100 "goodness",
which the metrics heatmap colors by (orange = strong, blue = weak). The rules + the
goodness math live in `analysis_layer/scoring_rules.py`; this page just edits them and
saves to `scoring_rules.json` (Save persists; delete the file to reset to defaults).

Built like the calibration tuner (`ui/calibration.py`): one metric edited at a time with
a LIVE preview — a histogram of the metric's real values across the universe, each bar
colored by the rule's goodness — so you SEE the strong/weak mapping before saving. Per the
project rule, the ECharts preview is NOT inside a collapsed expander (the text info box is).

v1 edits BASE rules only (Option A); per-type overrides (e.g. REIT payout) ship seeded in
code and resolve automatically in the heatmap — their editing UI is a deep-pass addition.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st
from streamlit_echarts import st_echarts

from analysis_layer import scoring_rules as SR
from config import settings, param_hints, rule_hints
from core.database import Database
from ui.chart_theme import (
    HEAT_RAMP, heat_color, DARK_BG, DARK_TEXT, GRID_LINE,
)

_SHAPE_LABELS = {
    "higher_better": "Higher is better",
    "lower_better": "Lower is better",
    "sweet_spot": "Sweet-spot (middle is best)",
}
_ANCHOR_LABELS = {
    "peer": "Peer (vs industry → sector)",
    "universe": "Universe (vs all stocks)",
    "absolute": "Absolute (a fixed line)",
}

# Preview x-axis zoom: Tukey IQR fence multiplier (hard-coded). The histogram shows the
# box ± _IQR_FENCE_K × IQR, so fat tails (P/E to ~1376) don't flatten the bell. Coloring
# always uses the full data — this only sets the visible window.
_IQR_FENCE_K = 1.5
# Number of histogram bars in the preview (finer = better resolution to read the shape).
_PREVIEW_BINS = 60


@st.cache_data(show_spinner="Reading analysis.db…")
def _load_analysis(_mtime: float) -> pd.DataFrame:
    """The full analysis snapshot (all columns) — the universe the rules rank against."""
    if not settings.ANALYSIS_DB.exists():
        return pd.DataFrame()
    with Database(settings.ANALYSIS_DB) as db:
        if not db.table_exists("analysis"):
            return pd.DataFrame()
        return db.read("analysis")


def _metric_name(metric: str) -> str:
    h = param_hints.get_hint(metric)
    if h:
        return h["name"]
    return metric.replace("_", " ").title()


def _present_metrics(df: pd.DataFrame) -> dict[str, list[str]]:
    """RULE_CATEGORIES restricted to metrics that actually exist as columns in analysis.db."""
    cols = set(df.columns)
    out: dict[str, list[str]] = {}
    for cat, keys in SR.RULE_CATEGORIES.items():
        present = [k for k in keys if k in cols]
        if present:
            out[cat] = present
    return out


def _overview_table(rules: dict[str, dict], by_cat: dict[str, list[str]]) -> pd.DataFrame:
    rows = []
    for cat, keys in by_cat.items():
        for k in keys:
            r = rules.get(k) or SR.rule_for(k) or {}
            band = ""
            if r.get("shape") == "sweet_spot" and r.get("lo") is not None:
                band = f"{r['lo']:g} – {r['hi']:g}"
            elif r.get("anchor") == "absolute" and r.get("value") is not None:
                band = f"line @ {r['value']:g}"
            rows.append({"Category": cat, "Metric": _metric_name(k),
                         "Shape": _SHAPE_LABELS.get(r.get("shape", ""), ""),
                         "Anchor": r.get("anchor", ""), "Band / line": band})
    return pd.DataFrame(rows)


def _preview(metric: str, rule: dict, df: pd.DataFrame) -> None:
    """Histogram of the metric's universe values, each bar colored by the rule's goodness."""
    vals = pd.to_numeric(df[metric], errors="coerce")
    if rule.get("positive_only"):
        vals = vals.where(vals > 0)
    clean = vals.dropna()
    if clean.empty:
        st.info("No data for this metric in analysis.db.")
        return

    tiers = ([df["industry"], df["sector"]]
             if rule.get("anchor") == "peer" and {"industry", "sector"} <= set(df.columns)
             else None)
    good = SR.goodness(df[metric], rule, tiers)  # aligned to df.index

    # Zoom the x-axis to the IQR "outlier fence" (Tukey, hard-coded 1.5×): the middle 50%
    # of values (the box) extended by 1.5× its width each side, clamped to the data. This
    # trims fat tails so the dense bell is readable (P/E shows ~0-66, not 0-1376) and does
    # NOT over-trim a flat spread like rs_rank. Coloring still uses ALL the data — this only
    # changes the visible x-window; values outside it are simply not drawn.
    q1, q3 = float(clean.quantile(0.25)), float(clean.quantile(0.75))
    iqr = q3 - q1
    if iqr > 0:
        lo = max(q1 - _IQR_FENCE_K * iqr, float(clean.min()))
        hi = min(q3 + _IQR_FENCE_K * iqr, float(clean.max()))
    else:
        lo, hi = float(clean.min()), float(clean.max())
    if lo == hi:
        hi = lo + 1.0
    edges = np.linspace(lo, hi, _PREVIEW_BINS + 1)
    cut = pd.cut(clean, bins=edges, include_lowest=True)
    counts = cut.value_counts(sort=False)
    gmean = good.reindex(clean.index).groupby(cut, observed=False).mean()
    centers = [round((iv.left + iv.right) / 2, 2) for iv in counts.index.categories]

    data = []
    for iv, c in zip(counts.index.categories, counts.values):
        gm = gmean.get(iv)
        data.append({"value": int(c),
                     "itemStyle": {"color": heat_color(float(gm) if pd.notna(gm) else None)}})

    mark = {}
    if rule.get("shape") == "sweet_spot" and rule.get("lo") is not None:
        def _x(v):  # nearest bin center for a value, as the category-axis position
            return min(range(len(centers)), key=lambda i: abs(centers[i] - v))
        mark = {"markArea": {"silent": True,
                             "itemStyle": {"color": "rgba(224,123,26,0.10)"},
                             "data": [[{"xAxis": _x(rule["lo"])}, {"xAxis": _x(rule["hi"])}]]}}
    elif rule.get("anchor") == "absolute" and rule.get("value") is not None:
        def _x(v):
            return min(range(len(centers)), key=lambda i: abs(centers[i] - v))
        mark = {"markLine": {"silent": True, "symbol": "none",
                             "label": {"show": False},
                             "lineStyle": {"type": "dashed", "color": "rgba(230,230,230,0.6)"},
                             "data": [{"xAxis": _x(rule["value"])}]}}

    options = {
        "backgroundColor": DARK_BG,
        "textStyle": {"color": DARK_TEXT},
        "tooltip": {"trigger": "axis", "backgroundColor": "rgba(15,18,25,0.92)",
                    "borderColor": "rgba(255,255,255,0.20)", "textStyle": {"color": DARK_TEXT}},
        "grid": {"left": 8, "right": 18, "top": 16, "bottom": 40, "containLabel": True},
        "xAxis": {"type": "category", "data": [f"{c:g}" for c in centers],
                  "name": _metric_name(metric),
                  "axisLine": {"lineStyle": {"color": "rgba(255,255,255,0.35)"}},
                  # Show ~12 labels however many bars there are, so they stay readable.
                  "axisLabel": {"color": DARK_TEXT, "interval": max(1, len(centers) // 12)}},
        "yAxis": {"type": "value", "name": "Stocks",
                  "nameTextStyle": {"color": DARK_TEXT}, "axisLabel": {"color": DARK_TEXT},
                  "splitLine": {"show": True, "lineStyle": {"color": GRID_LINE}}},
        "series": [{"type": "bar", "data": data, "barCategoryGap": "8%", **mark}],
    }
    st_echarts(options=options, height="320px", key=f"sr_prev_{metric}")
    st.caption("Each bar = a slice of the universe's values for this metric, colored by the "
               "rule: **orange = strong, blue = weak.** The shaded band / dashed line marks "
               "the rule's ideal band / anchor.")


def _editor(metric: str, df: pd.DataFrame, rules: dict[str, dict]) -> dict:
    """Render the rule widgets for one metric and return the edited rule (keys carry the
    metric name so switching metrics gives fresh widgets honoring their value=)."""
    cur = rules.get(metric) or SR.rule_for(metric) or {"shape": "higher_better", "anchor": "universe"}
    suffix = f"__{metric}"

    c = st.columns([1.2, 1.2, 1.6])
    shape = c[0].radio("Shape", list(_SHAPE_LABELS),
                       index=list(_SHAPE_LABELS).index(cur.get("shape", "higher_better")),
                       format_func=lambda k: _SHAPE_LABELS[k], key="sr_shape" + suffix)

    # Start from the current rule so DORMANT fields survive a shape switch: e.g. flipping
    # sweet-spot → higher-better and back must NOT lose the band (lo/hi), and vice-versa for
    # an absolute line value. Only the active shape's fields are overwritten below.
    rule: dict = dict(cur)
    rule["shape"] = shape
    if shape == "sweet_spot":
        rule["anchor"] = "absolute"
        lo = c[1].number_input("Ideal low", value=float(cur.get("lo", 0.0)),
                               step=0.1, key="sr_lo" + suffix)
        hi = c[2].number_input("Ideal high", value=float(cur.get("hi", lo + 1.0)),
                               step=0.1, key="sr_hi" + suffix)
        rule["lo"], rule["hi"] = float(lo), float(max(hi, lo))
    else:
        anchors = ["peer", "universe", "absolute"]
        anchor = c[1].radio("Anchor", anchors,
                            index=anchors.index(cur.get("anchor", "universe")),
                            format_func=lambda k: _ANCHOR_LABELS[k], key="sr_anchor" + suffix)
        rule["anchor"] = anchor
        if anchor == "absolute":
            val = c[2].number_input(
                "Line (value where it's neutral; blank-ish = use the value as-is)",
                value=float(cur.get("value", 0.0)), step=0.1, key="sr_val" + suffix)
            rule["value"] = float(val)

    if cur.get("positive_only") or shape in ("lower_better",):
        rule["positive_only"] = st.checkbox(
            "Ignore non-positive values (a negative ratio is 'not applicable', not best)",
            value=bool(cur.get("positive_only", False)), key="sr_pos" + suffix)
    # (Seeded per-type overrides ride through unchanged via dict(cur) above — v1 doesn't edit them.)
    return rule


# --------------------------------------------------------------------------- #
# page
# --------------------------------------------------------------------------- #
st.title("Scoring Rules")
st.caption(
    "Decide how each parameter is judged **strong vs weak** — the rule that colors the "
    "**metrics heat map**. Pick a metric, set its rule, watch the live preview, then "
    "**Save**. Rules also carry seeded per-type exceptions (e.g. REITs pay out more) that "
    "apply automatically. Delete `scoring_rules.json` to reset everything to defaults.")

_mtime = settings.ANALYSIS_DB.stat().st_mtime if settings.ANALYSIS_DB.exists() else 0.0
_df = _load_analysis(_mtime)
if _df.empty:
    st.warning("No analysis.db yet — run an analysis first (Fetch Control), then the rules "
               "can preview against real data.")
    st.stop()

_by_cat = _present_metrics(_df)
if "scoring_rules" not in st.session_state:
    st.session_state["scoring_rules"] = SR.load_rules()
_rules = st.session_state["scoring_rules"]

# -- metric picker: category then metric -----------------------------------------
_pick = st.columns([1, 2])
_cat = _pick[0].selectbox("Category", list(_by_cat))
_metric = _pick[1].selectbox("Parameter", _by_cat[_cat], format_func=_metric_name)

# Data-driven suggestion — sits right above the info box (apply clears this metric's
# widget keys so value= is honored on the rerun).
if st.button("💡 Suggest from data",
             help="Propose a rule from this metric's distribution (sweet-spot band = "
                  "the middle 50% of values)."):
    _rules[_metric] = SR.suggest_rule(_df[_metric], _metric)
    for _k in list(st.session_state):
        if _k.endswith(f"__{_metric}"):
            del st.session_state[_k]
    st.rerun()

# -- info box: SELF-MANAGED collapse (header button + session flag) ------------------
# A plain st.markdown inside a *collapsed* st.expander renders STALE when the selected
# metric changes (the body keeps showing the previous metric while the label updates) —
# the documented expander gotcha. So we drive the collapse ourselves and render the body
# as a normal element, which always refreshes on rerun.
_info_open = st.session_state.get("sr_info_open", False)
if st.button(("▾ " if _info_open else "▸ ") + f"ℹ️ About {_metric_name(_metric)} and this rule",
             key="sr_info_toggle"):
    _info_open = not _info_open
    st.session_state["sr_info_open"] = _info_open
if _info_open:
    st.markdown(rule_hints.rule_hint_markdown(_metric, _rules.get(_metric)))

# -- editor + live preview ------------------------------------------------------
_edited = _editor(_metric, _df, _rules)
_rules[_metric] = _edited  # keep the working set in sync for Save + overview

_preview(_metric, _edited, _df)

# -- save -----------------------------------------------------------------------
_changed = _edited != (SR.DEFAULT_RULES.get(_metric) or SR.rule_for(_metric))
_save = st.columns([1, 3], vertical_alignment="center")
if _save[0].button("💾 Save rules", type="primary", width="stretch"):
    SR.save_rules(_rules)
    st.success("Saved to scoring_rules.json. The heat map uses these immediately.")
_save[1].caption("Saves every metric whose rule differs from the built-in default "
                 + ("· this metric **differs** from its default." if _changed
                    else "· this metric matches its default."))

# -- whole-set overview (text table, no charts) ---------------------------------
st.divider()
st.subheader("All rules")
st.caption("The full rule set at a glance. Pick any row's metric above to edit it.")
st.dataframe(_overview_table(_rules, _by_cat), width="stretch", hide_index=True)
