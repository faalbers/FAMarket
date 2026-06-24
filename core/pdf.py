"""
Generic PDF report engine — the reusable core of the report pipeline.

Content-agnostic: it knows about *blocks* (titles, headings, paragraphs, tables,
clickable links), not about news or any specific report. A report builder
(see `reporting/`) composes these blocks and calls `build()` to get PDF bytes;
`reporting.store` then writes those bytes into `settings.REPORTS_DIR`.

This is the single place report styling lives — fonts, colors, margins, the
running page header/footer — mirroring how `ui/chart_theme.py` centralizes chart
styling. New report types reuse `ReportBuilder` rather than touching reportlab
directly.

Built on reportlab (pure-Python, no native deps — important on this Windows box).
Hyperlinks use reportlab's inline `<a href>` markup inside a `Paragraph`, so a
headline cell is real clickable text in the PDF, not a separate URL column.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# --------------------------------------------------------------------------- #
# Shared palette / metrics (one place to tweak the look of every report)
# --------------------------------------------------------------------------- #
ACCENT = colors.HexColor("#2f6db5")        # headings, table header, links (matches the email tool)
INK = colors.HexColor("#1a2733")           # body text
MUTED = colors.HexColor("#6b7785")         # captions / subtitles / footer
GRID = colors.HexColor("#d9dde3")          # table grid lines
STRIPE = colors.HexColor("#f4f6f9")        # alternating row background
HEADER_TEXT = colors.white

_MARGIN = 0.6 * inch


def _styles() -> dict[str, ParagraphStyle]:
    """The shared paragraph styles, derived once from reportlab's sample sheet."""
    base = getSampleStyleSheet()["Normal"]
    common = dict(fontName="Helvetica", textColor=INK, alignment=TA_LEFT, leading=12)
    return {
        "title": ParagraphStyle("ti", parent=base, fontName="Helvetica-Bold",
                                 fontSize=20, leading=24, textColor=INK, spaceAfter=2),
        "subtitle": ParagraphStyle("st", parent=base, fontSize=9.5, leading=13,
                                    textColor=MUTED, spaceAfter=2),
        "heading": ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold",
                                   fontSize=14, leading=17, textColor=ACCENT,
                                   spaceBefore=16, spaceAfter=4),
        "subheading": ParagraphStyle("h3", parent=base, fontName="Helvetica-Bold",
                                      fontSize=10.5, leading=13, textColor=INK,
                                      spaceBefore=8, spaceAfter=3),
        "body": ParagraphStyle("bd", parent=base, fontSize=10, **common),
        "muted": ParagraphStyle("mu", parent=base, fontSize=9, leading=12,
                                textColor=MUTED),
        "cell": ParagraphStyle("cl", parent=base, fontSize=8.5, leading=11,
                               textColor=INK),
        "cell_link": ParagraphStyle("ck", parent=base, fontSize=8.5, leading=11,
                                    textColor=ACCENT),
        "cell_head": ParagraphStyle("ch", parent=base, fontName="Helvetica-Bold",
                                    fontSize=8.5, leading=11, textColor=HEADER_TEXT),
    }


class ReportBuilder:
    """Accumulates report blocks, then renders them to PDF bytes.

    `title` is the report's running-header label (drawn top-left on every page);
    call `cover_title()` to also print it big at the top of page one. Add blocks in
    order with the methods below, then `build()`.
    """

    def __init__(self, *, title: str, pagesize=LETTER) -> None:
        self.title = title
        self._pagesize = pagesize
        self._generated = datetime.now()
        self._s = _styles()
        self._flow: list = []

    # ---- usable content width (page minus L/R margins) --------------------- #
    @property
    def content_width(self) -> float:
        return self._pagesize[0] - 2 * _MARGIN

    # ---- block methods (each returns self so calls can chain) ------------- #
    def cover_title(self, text: str | None = None):
        self._flow.append(Paragraph(escape(text or self.title), self._s["title"]))
        return self

    def subtitle(self, text: str):
        self._flow.append(Paragraph(escape(text), self._s["subtitle"]))
        return self

    def heading(self, text: str):
        self._flow.append(Paragraph(escape(text), self._s["heading"]))
        return self

    def subheading(self, text: str):
        self._flow.append(Paragraph(escape(text), self._s["subheading"]))
        return self

    def paragraph(self, text: str):
        self._flow.append(Paragraph(escape(text), self._s["body"]))
        return self

    def muted(self, text: str):
        self._flow.append(Paragraph(escape(text), self._s["muted"]))
        return self

    def spacer(self, height: float = 8):
        self._flow.append(Spacer(1, height))
        return self

    def page_break(self):
        self._flow.append(PageBreak())
        return self

    # ---- cell helpers (build the flowables a table row is made of) --------- #
    def link(self, text: str, url: str) -> Paragraph:
        """A clickable cell: `text` linked to `url` (the headline-as-link block)."""
        url = (url or "").strip()
        if not url:
            return self.text(text)
        return Paragraph(
            f'<a href="{escape(url, quote=True)}">{escape(str(text))}</a>',
            self._s["cell_link"],
        )

    def text(self, value) -> Paragraph:
        """A plain wrapping text cell."""
        return Paragraph(escape("" if value is None else str(value)), self._s["cell"])

    def table(self, headers: list[str], rows: list[list], *,
              col_widths: list[float] | None = None):
        """A styled table. Each cell may be a plain value (wrapped as text) or a
        flowable from `link()`/`text()`. `col_widths` are fractions of the content
        width (default: equal columns)."""
        n = len(headers)
        if col_widths is None:
            col_widths = [1 / n] * n
        widths = [f * self.content_width for f in col_widths]

        head = [Paragraph(escape(h), self._s["cell_head"]) for h in headers]
        body = [
            [c if hasattr(c, "wrap") else self.text(c) for c in row]
            for row in rows
        ]
        tbl = Table([head, *body], colWidths=widths, repeatRows=1, hAlign="LEFT")
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, GRID),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        # zebra striping on body rows (row 0 is the header)
        for i in range(1, len(body) + 1):
            if i % 2 == 0:
                style.append(("BACKGROUND", (0, i), (-1, i), STRIPE))
        tbl.setStyle(TableStyle(style))
        self._flow.append(tbl)
        return self

    # ---- render ----------------------------------------------------------- #
    def _draw_chrome(self, canvas, doc) -> None:
        """Running header (title + generated date) and footer (page number)."""
        canvas.saveState()
        w, h = self._pagesize
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(_MARGIN, h - _MARGIN + 14, self.title)
        canvas.drawRightString(w - _MARGIN, h - _MARGIN + 14,
                               self._generated.strftime("Generated %Y-%m-%d %H:%M"))
        canvas.setStrokeColor(GRID)
        canvas.setLineWidth(0.5)
        canvas.line(_MARGIN, h - _MARGIN + 10, w - _MARGIN, h - _MARGIN + 10)
        canvas.drawRightString(w - _MARGIN, _MARGIN - 14, f"Page {doc.page}")
        canvas.restoreState()

    def build(self) -> bytes:
        """Render the accumulated blocks to PDF bytes."""
        buf = BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=self._pagesize,
            leftMargin=_MARGIN, rightMargin=_MARGIN,
            topMargin=_MARGIN + 0.25 * inch, bottomMargin=_MARGIN,
            title=self.title, author="FAMarket",
        )
        doc.build(list(self._flow),
                  onFirstPage=self._draw_chrome, onLaterPages=self._draw_chrome)
        return buf.getvalue()
