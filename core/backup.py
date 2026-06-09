"""
Dated, count-capped backups (roadmap "Key Decisions").

Run before each fetch run. Each backup is copied into BACKUP_DIR under a dated name
so the directory is self-describing at a glance::

    {stem}_{YYYY-MM-DD_HH-MM-SS}{suffix}    e.g. symbols_2026-06-09_13-00-20.db

The ISO-style stamp sorts chronologically by name. Retention is count-based: only
the newest BACKUP_VERSIONS copies of each file are kept; older ones are pruned. The
scheme is suffix-agnostic, so the same `backup_file()` backs up the SQLite databases
(.db), the run log (.log, via core.logging_config.roll_log) and config/settings.py.

One backup_all() run stamps every database with a single shared timestamp, so a
run's files form one consistent snapshot (grouped by that exact stamp) — see
core.restore, which reverts the live databases to a chosen snapshot.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from config import settings
from core.logging_config import get_logger

log = get_logger("backup")

# Stamp embedded in every backup filename. ISO-ish so names sort chronologically;
# hyphens (not colons) in the time part because ':' is illegal in Windows filenames.
TS_FMT = "%Y-%m-%d_%H-%M-%S"


def database_paths() -> list[Path]:
    """The configured SQLite databases, in a stable order.

    Single source of truth shared by the backup and the restore tool
    (`core.restore`), so the two never drift on which files are in scope.
    """
    return [
        settings.SYMBOLS_DB,
        settings.QUOTES_DB,
        settings.OHLCV_DB,
        settings.FINANCIALS_DB,
        settings.ANALYSIS_DB,
        settings.MACRO_DB,
    ]


def now_stamp() -> str:
    """The current time as a backup-filename stamp."""
    return datetime.now().strftime(TS_FMT)


def backup_stamp(name: str, stem: str) -> str | None:
    """The timestamp embedded in a backup file `name`, or None if it doesn't match.

    `name` is a bare filename (e.g. ``symbols_2026-06-09_13-00-20.db``); returns
    ``2026-06-09_13-00-20`` when it fits ``{stem}_{stamp}{suffix}`` with a parseable
    stamp, else None (an unrelated file, or a pre-dated-scheme `_1` backup).
    """
    core = Path(name).stem  # drops the final suffix
    prefix = f"{stem}_"
    if not core.startswith(prefix):
        return None
    raw = core[len(prefix):]
    try:
        datetime.strptime(raw, TS_FMT)
    except ValueError:
        return None
    return raw


def backup_all() -> None:
    """Back up every configured database under one shared snapshot timestamp."""
    settings.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now_stamp()
    db_paths = database_paths()
    n = 0
    for db in db_paths:
        if db.exists():
            backup_file(db, stamp)
            n += 1
    log.info("Backup complete — %d databases backed up as snapshot %s", n, stamp)


def backup_file(path: Path, stamp: str | None = None) -> None:
    """Copy `path` into BACKUP_DIR as ``{stem}_{stamp}{suffix}``, then prune to the
    newest BACKUP_VERSIONS copies of that file.

    Pass `stamp` to share one timestamp across a batch (so all of a run's databases
    land in the same snapshot); omit it for a standalone backup (the log, settings.py),
    which then stamps with the current time. Suffix-agnostic. Caller decides whether
    to back up at all (e.g. roll_log skips an empty log).
    """
    settings.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = stamp or now_stamp()
    dest = settings.BACKUP_DIR / f"{path.stem}_{stamp}{path.suffix}"
    shutil.copy2(path, dest)
    _prune(path.stem, path.suffix)


def _prune(stem: str, suffix: str) -> None:
    """Keep only the newest BACKUP_VERSIONS dated backups of ``{stem}{suffix}``.

    Ordered by the timestamp embedded in the name (authoritative — a file's mtime
    can be identical across copies of an unchanged database). Any leftover file that
    predates the dated scheme doesn't parse, so it sorts oldest and is pruned first.
    """
    def order(p: Path) -> datetime:
        stamp = backup_stamp(p.name, stem)
        return datetime.strptime(stamp, TS_FMT) if stamp else datetime.min

    backups = sorted(
        settings.BACKUP_DIR.glob(f"{stem}_*{suffix}"), key=order, reverse=True
    )
    for old in backups[settings.BACKUP_VERSIONS:]:
        old.unlink()
