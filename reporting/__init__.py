"""
Report pipeline — the screen-facing entry point.

A screen "requests a report" by id:

    from reporting import generate, store
    pdf_bytes = generate("news", df=df, sources=sources, sym_meta=meta, order=syms)
    path = store.save(pdf_bytes, name="News Report")

The generic PDF engine lives in `core/pdf.py`; each report type is a builder
module here. Add a new report = add a module + one line in `_REGISTRY`; every
screen reaches it through the same `generate()` call.
"""

from __future__ import annotations

from collections.abc import Callable

from reporting import news_report, store  # noqa: F401  (store re-exported for callers)

# report id -> builder returning PDF bytes. Each builder takes **kwargs.
_REGISTRY: dict[str, Callable[..., bytes]] = {
    "news": news_report.build_pdf,
}


def available() -> list[str]:
    """The report ids a screen can request."""
    return sorted(_REGISTRY)


def generate(report_id: str, **kwargs) -> bytes:
    """Build the report `report_id` and return its PDF bytes. Raises KeyError on an
    unknown id (caller passes the report's own kwargs through)."""
    try:
        builder = _REGISTRY[report_id]
    except KeyError:
        raise KeyError(f"unknown report id {report_id!r}; known: {available()}") from None
    return builder(**kwargs)
