"""
Rotating, versioned backups (roadmap "Key Decisions").

Run before each fetch run. Keeps BACKUP_VERSIONS copies per file:
    {stem}_1{suffix}  (most recent)  ...  {stem}_5{suffix}  (oldest)

On each run, versions shift up by one: _5 is dropped, _4 -> _5, ... _1 -> _2, and
the current live file is copied in as _1. The rotation is suffix-agnostic, so the
exact same scheme backs up the SQLite databases (.db) AND the run log (.log) — see
rotate_file(), used by core.logging_config.roll_log().
"""

from __future__ import annotations

import shutil
from pathlib import Path

from config import settings
from core.logging_config import get_logger

log = get_logger("backup")


def backup_all() -> None:
    """Back up every configured database file, rotating versions."""
    settings.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    db_paths = [
        settings.SYMBOLS_DB,
        settings.QUOTES_DB,
        settings.OHLCV_DB,
        settings.FINANCIALS_DB,
        settings.ANALYSIS_DB,
        settings.MACRO_DB,
    ]
    for db in db_paths:
        if db.exists():
            rotate_file(db)
    log.info("Backup complete — %d databases rotated", sum(p.exists() for p in db_paths))


def rotate_file(path: Path) -> None:
    """Copy `path` into BACKUP_DIR as version 1, rotating older versions up by one.

    Keeps BACKUP_VERSIONS copies named ``{stem}_{v}{suffix}`` (_1 newest .. _N
    oldest); the oldest is dropped each call. Suffix-agnostic — used for both the
    databases (.db) and the run log (.log). Caller decides whether to back up at all
    (e.g. roll_log skips an empty log).
    """
    settings.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name, suffix = path.stem, path.suffix
    n = settings.BACKUP_VERSIONS

    # Drop the oldest, then shift each version up by one.
    oldest = settings.BACKUP_DIR / f"{name}_{n}{suffix}"
    if oldest.exists():
        oldest.unlink()
    for v in range(n - 1, 0, -1):
        src = settings.BACKUP_DIR / f"{name}_{v}{suffix}"
        if src.exists():
            src.rename(settings.BACKUP_DIR / f"{name}_{v + 1}{suffix}")

    # Current live file becomes version 1.
    shutil.copy2(path, settings.BACKUP_DIR / f"{name}_1{suffix}")
