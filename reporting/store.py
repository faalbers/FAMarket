"""
Report store — where generated PDFs land on disk.

Every report a screen requests is written into `settings.REPORTS_DIR` with a
timestamped, filesystem-safe name, and the folder is count-capped to
`settings.REPORTS_KEEP` (newest kept, oldest pruned on each save). Same scheme as
`ui/output_runs.py` and `core/backup.py`.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from config import settings
from core.logging_config import get_logger

log = get_logger("reporting.store")

_SLUG = re.compile(r"[^A-Za-z0-9._-]+")


def _dir() -> Path:
    settings.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return settings.REPORTS_DIR


def _slug(name: str) -> str:
    """A short, filesystem-safe slug for the report name (spaces -> '-')."""
    s = _SLUG.sub("-", (name or "report").strip()).strip("-_.")
    return (s or "report")[:60]


def save(pdf_bytes: bytes, *, name: str) -> Path:
    """Write `pdf_bytes` as `{timestamp}_{slug(name)}.pdf`, prune old, return path."""
    fname = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{_slug(name)}.pdf"
    path = _dir() / fname
    path.write_bytes(pdf_bytes)
    log.info("Saved report %s (%d bytes)", fname, len(pdf_bytes))
    _prune()
    return path


def list_reports() -> list[Path]:
    """Every saved report, newest first (the timestamp prefix sorts chronologically)."""
    return sorted(_dir().glob("*.pdf"), key=lambda p: p.name, reverse=True)


def _prune() -> None:
    """Keep the newest REPORTS_KEEP PDFs; delete the rest."""
    for path in list_reports()[settings.REPORTS_KEEP:]:
        path.unlink(missing_ok=True)
        log.info("Pruned old report %s", path.name)
