"""Reset local state to a clean slate (Fetch Control "danger zone").

Takes a versioned backup of the current databases first (the normal rotating
backup) and archives the current log the same way (a versioned famarket_1.log in
BACKUP_DIR; an empty log is skipped), then deletes the live SQLite databases (and
their WAL/SHM sidecars). **Backups and logs are preserved** — this is a recoverable
reset, not a wipe: the just-taken backups sit in BACKUP_DIR as _1.

After the reset the project is at an initial-load state (the system auto-detects
empty databases as a first run). The Fetch Control page gates this behind an
explicit confirmation. Any database file that can't be removed — most often a .db
still locked open by the VSCode SQLite viewer on Windows — is reported back, not
silently skipped.
"""

from __future__ import annotations

from config import settings
from core.backup import backup_all
from core.logging_config import get_logger, roll_log


def reset_all_data() -> dict:
    """Back up current DBs, roll the log, then delete the live databases.

    Returns ``{"deleted": [names], "failed": [names], "errors": [strings]}`` for the
    database files. ``failed`` is non-empty when a file was locked (close the SQLite
    viewer and retry); the rest are still deleted (best-effort, no rollback). Backups
    and logs are NOT deleted — a fresh DB backup is added and the current log is
    archived as a versioned backup too (unless it is empty).
    """
    log = get_logger("reset")
    log.warning("RESET requested — backing up databases, archiving the log, then deleting databases")

    # 1. Versioned backup of the current databases (rotating, keeps BACKUP_VERSIONS),
    #    before anything is deleted so the dataset stays recoverable.
    backup_all()

    # 2. Archive the log the same way: roll_log() rotates the current famarket.log
    #    into a versioned backup (famarket_1.log ..) and starts fresh. Empty = skipped.
    roll_log()

    # 3. Delete the live databases (+ WAL/SHM sidecars). Backups + logs are preserved.
    deleted: list[str] = []
    failed: list[str] = []
    errors: list[str] = []
    for path in sorted(settings.DB_DIR.glob("*.db*")):
        try:
            path.unlink()
            deleted.append(path.name)
        except FileNotFoundError:
            pass
        except OSError as exc:  # locked file (SQLite viewer), permissions, etc.
            failed.append(path.name)
            errors.append(f"{path.name}: {exc}")

    # Recreate the (now empty) databases directory so the next run / app launch finds it.
    settings.DB_DIR.mkdir(parents=True, exist_ok=True)

    return {"deleted": deleted, "failed": failed, "errors": errors}
