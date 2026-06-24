"""
News report — the first report type in the pipeline.

Turns an already-fetched, already-classified news DataFrame (the exact frame the
Charts News view holds: `data_layer.news.fetch_news` + `classify_relevance`) into
a PDF that mirrors that screen:

  * one section per symbol, in the selection's order;
  * each split into "About <SYMBOL>" (company-specific) and "Broader context"
    (sector / peers / market);
  * rows newest-first (the frame is already sorted that way);
  * each **headline is a clickable link** to its source article.

It does NOT fetch — the caller passes the frame it already loaded, so opening the
report never triggers a second round of news API calls.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from core.pdf import ReportBuilder

# Same columns/order as the on-screen news table, except the Title cell is itself
# the link (so there's no separate URL column). Fractions of the content width.
_COLS = ["Published (UTC)", "Title", "Publisher", "Sentiment"]
_WIDTHS = [0.16, 0.56, 0.18, 0.10]


def _published(value) -> str:
    """Format a tz-aware UTC Published timestamp as the screen does, '' when missing."""
    ts = pd.Timestamp(value) if value is not None and not pd.isna(value) else None
    return "" if ts is None else ts.strftime("%Y-%m-%d %H:%M")


def _rows(builder: ReportBuilder, frame: pd.DataFrame) -> list[list]:
    """Build table rows for a subset; the Title cell is a hyperlink to its Url."""
    out = []
    for r in frame.itertuples(index=False):
        out.append([
            _published(getattr(r, "Published", None)),
            builder.link(getattr(r, "Title", "") or "", getattr(r, "Url", "") or ""),
            getattr(r, "Publisher", "") or "",
            getattr(r, "Sentiment", "") or "",
        ])
    return out


def build_pdf(df: pd.DataFrame, *, sources, order=None, sym_meta=None,
              title: str | None = None) -> bytes:
    """Render the news report to PDF bytes.

    df        — classified news frame (Symbol, Published, Title, Url, Publisher,
                Sentiment, Relevance, …).
    sources   — the source names used (shown in the subtitle).
    order     — symbols in selection order (default: order of appearance in df).
    sym_meta  — {symbol: {company, sector, industry}} for the section headings.
    title     — override the report/header title.
    """
    df = df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    sym_meta = sym_meta or {}
    if order:
        symbols = list(dict.fromkeys(s for s in order))
    elif not df.empty:
        symbols = list(dict.fromkeys(df["Symbol"].tolist()))
    else:
        symbols = []

    title = title or f"News Report — {datetime.now():%Y-%m-%d}"
    b = ReportBuilder(title=title)
    b.cover_title(title)
    src = ", ".join(sources) if sources else "—"
    b.subtitle(f"Sources: {src} · newest first · duplicate stories merged · "
               f"{len(symbols)} symbol(s)")

    if df.empty or not symbols:
        b.spacer(10).muted("No recent news found for the selected symbols.")
        return b.build()

    for i, sym in enumerate(symbols):
        if i:  # each symbol starts on its own page (first one stays under the cover)
            b.page_break()
        meta = sym_meta.get(sym) or {}
        company = (meta.get("company") or "").strip()
        b.heading(f"{sym} — {company}" if company else sym)

        sub = df[df["Symbol"] == sym]
        if sub.empty:
            b.muted("No recent news.")
            continue

        about = sub[sub["Relevance"] == "Company"]
        context = sub[sub["Relevance"] == "Context"]

        b.subheading(f"About {sym}")
        if about.empty:
            b.muted("No company-specific articles.")
        else:
            b.table(_COLS, _rows(b, about), col_widths=_WIDTHS)

        b.subheading("Broader context (sector · peers · market)")
        if context.empty:
            b.muted("None.")
        else:
            b.table(_COLS, _rows(b, context), col_widths=_WIDTHS)

    return b.build()
