"""
Output report — the Output screen's results table as a PDF.

Turns the exact frame the Output screen shows (`ui/pages/output.py`'s `view`: the
lead columns + the visible parameter columns, already in the user's custom sort
order) into a PDF that mirrors that screen:

  * page 1 — the table, only the shown columns, sorted as displayed;
  * page 2 — the filter's Comment + AI instructions, rendered as markdown.

Landscape LETTER so a wide multi-column table reads comfortably. It does NOT
re-query or re-sort — the caller passes the already-built display frame.
"""

from __future__ import annotations

import math

import pandas as pd
from reportlab.lib.pagesizes import LETTER, landscape

from core.pdf import ReportBuilder

# Lead (non-parameter) columns get a wider share than the numeric parameter columns.
_LEAD = {"Symbol", "Company", "Sector", "Industry"}
_LEAD_WEIGHT = 1.8
_PARAM_WEIGHT = 1.0


def _fmt(value) -> str:
    """Format a cell like the on-screen table: floats to 2 decimals, NaN/None blank."""
    if value is None:
        return ""
    if isinstance(value, float):
        return "" if math.isnan(value) else f"{value:.2f}"
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value)


def _col_widths(headers: list[str]) -> list[float]:
    """Fractions of the content width: lead text columns wider than parameter columns."""
    weights = [_LEAD_WEIGHT if h in _LEAD else _PARAM_WEIGHT for h in headers]
    total = sum(weights) or 1.0
    return [w / total for w in weights]


def build_pdf(*, table: pd.DataFrame, comment: str = "", ai_instructions: str = "",
              output_name: str | None = None, type_labels: str = "",
              sort_summary: str = "", created_at: str = "") -> bytes:
    """Render the Output report to PDF bytes.

    table        — the display frame (lead + visible param columns, already sorted);
                   column names are the on-screen header labels.
    comment       — the filter's Comment (markdown).
    ai_instructions — the filter's AI instructions / source spec (markdown).
    output_name   — the run/filter name (also drives the saved filename).
    type_labels   — pre-formatted security-type labels for the subtitle.
    sort_summary  — the Output sort summary string (e.g. "ROE ▼ → Company ▲").
    created_at    — the run's creation time, for the subtitle.
    """
    table = table if isinstance(table, pd.DataFrame) else pd.DataFrame()
    name = (output_name or "").strip()
    title = f"Output Report — {name}" if name else "Output Report"

    b = ReportBuilder(title=title, pagesize=landscape(LETTER))
    b.cover_title(title)
    n = len(table)
    bits = [f"{n} row{'' if n == 1 else 's'}"]
    if type_labels:
        bits.append(type_labels)
    bits.append(f"sorted: {sort_summary or 'none'}")
    if created_at:
        bits.append(f"created {created_at}")
    b.subtitle(" · ".join(bits))
    b.spacer(6)

    if table.empty:
        b.muted("No rows to report.")
    else:
        headers = [str(c) for c in table.columns]
        rows = [[_fmt(v) for v in row] for row in table.itertuples(index=False)]
        b.table(headers, rows, col_widths=_col_widths(headers))

    # Page 2 — the filter's notes, rendered as markdown.
    b.page_break()
    b.heading("Comment")
    if (comment or "").strip():
        b.markdown(comment)
    else:
        b.muted("No comment.")
    b.heading("AI instructions")
    if (ai_instructions or "").strip():
        b.markdown(ai_instructions)
    else:
        b.muted("None.")

    return b.build()
