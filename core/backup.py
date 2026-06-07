"""
Rotating backup of all SQLite databases (roadmap "Key Decisions").

Run before each fetch run. Keeps 5 versions per database:
    {db_name}_1.db  (most recent)  ...  {db_name}_5.db  (oldest)

On each run, versions shift up by one: _5 is dropped, _4 -> _5, ... _1 -> _2,
and the current live .db is copied in as _1.
"""

from __future__ import annotations

import shutil

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
            _rotate_one(db)
    log.info("Backup complete — %d databases rotated", sum(p.exists() for p in db_paths))


def _rotate_one(db_path) -> None:
    name = db_path.stem  # e.g. "symbols"
    n = settings.BACKUP_VERSIONS

    # Drop the oldest, then shift each version up by one.
    oldest = settings.BACKUP_DIR / f"{name}_{n}.db"
    if oldest.exists():
        oldest.unlink()
    for v in range(n - 1, 0, -1):
        src = settings.BACKUP_DIR / f"{name}_{v}.db"
        if src.exists():
            src.rename(settings.BACKUP_DIR / f"{name}_{v + 1}.db")

    # Current live DB becomes version 1.
    shutil.copy2(db_path, settings.BACKUP_DIR / f"{name}_1.db")
