"""
Shared ECharts dark theme + helpers for the chart pages.

Lifted out of `ui/pages/charts.py` once a second chart view (the peak-detection
calibration tool) needed the same look (CLAUDE.md: "lift them into shared config
when more chart views are built"). The price chart and the calibration chart both
import these so the dark background, the color-blind-safe line palette (Paul Tol's
"vibrant" scheme — higher contrast on dark than the Okabe-Ito set in
`settings.CHART_COLORWAY`), and the data-gap break logic stay identical.
"""

from __future__ import annotations

import pandas as pd

DARK_BG = "#0e1117"      # matches Streamlit's default dark theme background
DARK_TEXT = "#e6e6e6"
GRID_LINE = "rgba(255,255,255,0.14)"  # subtle gridlines on the dark background
COLORWAY = ("#33BBEE", "#EE7733", "#EE3377", "#009988", "#0077BB", "#CC3311", "#BBBBBB")

GAP_DAYS = 7  # consecutive bars more than this many days apart -> draw a line break

# Legend on/off clarity. The ECharts default line-legend icon is a thin line with a
# hollow circle; toggled off it only dims a little, so it was very hard to tell which
# lines were on. A SOLID filled swatch (`roundRect`) reads as brightly-colored when on
# and obviously greyed when off, and a bigger item makes the state easy to see.
LEGEND_ICON = "roundRect"
LEGEND_ITEM_W = 26
LEGEND_ITEM_H = 14
LEGEND_INACTIVE = "rgba(255,255,255,0.28)"  # greyed swatch + label for a hidden line


def legend_style(data, **overrides):
    """Shared vertical-scroll legend for the line/radar charts.

    Centralizes the dark-theme legend so every chart's line-toggle list looks the same
    and so the on/off state is clear at a glance (filled swatch icon + dimmed inactive
    color). Pass per-chart extras (e.g. ``selectorButtonGap=8``) as keyword overrides.
    """
    legend = {
        "type": "scroll", "orient": "vertical", "left": 8, "top": "middle",
        "data": data,
        "icon": LEGEND_ICON,
        "itemWidth": LEGEND_ITEM_W, "itemHeight": LEGEND_ITEM_H,
        "textStyle": {"color": DARK_TEXT},
        "inactiveColor": LEGEND_INACTIVE,
        "inactiveBorderColor": LEGEND_INACTIVE,
        "pageTextStyle": {"color": DARK_TEXT},
        "selector": [{"type": "all", "title": "All"}, {"type": "inverse", "title": "Invert"}],
        "selectorPosition": "end",
        "selectorLabel": {"color": DARK_TEXT, "borderColor": "rgba(255,255,255,0.30)",
                          "backgroundColor": "rgba(255,255,255,0.05)"},
    }
    legend.update(overrides)
    return legend


def echarts_points(dates: pd.Series, values: pd.Series) -> list[list]:
    """Build ECharts [date, value] points, inserting a null wherever consecutive
    bars are >GAP_DAYS apart.

    With series `connectNulls=False`, ECharts renders the null as a break, so genuine
    data gaps (missing fetch, trading halt) show as a discontinuity instead of a
    straight-line interpolation. Normal weekend/holiday gaps stay under the threshold.
    """
    pts: list[list] = []
    prev = None
    for d, v in zip(dates, values):
        if prev is not None and (d - prev).days > GAP_DAYS:
            pts.append([(prev + (d - prev) / 2).strftime("%Y-%m-%d"), None])
        pts.append([d.strftime("%Y-%m-%d"), round(float(v), 2)])
        prev = d
    return pts
