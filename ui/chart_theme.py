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
