"""Revert the databases to a dated backup snapshot (Fetch Control danger zone).

`core.backup` keeps the newest BACKUP_VERSIONS copies of every .db named
``{stem}_{YYYY-MM-DD_HH-MM-SS}{suffix}``, taken before each fetch run — one run stamps
all databases with a single shared timestamp, so each timestamp is one consistent
**snapshot**. This reverts the live databases to a chosen snapshot by copying each
``{stem}_{stamp}.db`` back over its live file.

**Databases only** — the run log is never touched (it isn't part of the dataset).

Safety:
  * Before overwriting, the current live databases are copied into
    ``BACKUP_DIR/pre_restore/`` — a single-slot undo point outside the dated backup
    set, so a wrong choice is recoverable and the dated backups stay intact.
  * Stale WAL/SHM sidecars are removed after the copy so SQLite can't replay them
    onto the just-restored file.
  * Locked files (most often the VSCode SQLite viewer holding a .db open on Windows)
    are reported back, not silently skipped — same contract as `core.reset`.
"""

from __future__ import annotations

import shutil
from datetime import datetime

from config import settings
from core.backup import TS_FMT, backup_stamp, database_paths
from core.logging_config import get_logger

log = get_logger("restore")


def list_snapshots() -> list[dict]:
    """Summarize each restorable backup snapshot, newest first::

        {"stamp": "2026-06-09_13-00-20", "saved_at": "2026-06-09 13:00:20",
         "databases": [names], "count": n}

    A snapshot is one shared timestamp across the database backups. `saved_at` is the
    stamp rendered for display; `databases` lists which DBs that snapshot holds.
    """
    by_stamp: dict[str, list[str]] = {}
    for live in database_paths():
        for bk in settings.BACKUP_DIR.glob(f"{live.stem}_*{live.suffix}"):
            stamp = backup_stamp(bk.name, live.stem)
            if stamp:
                by_stamp.setdefault(stamp, []).append(live.name)

    out: list[dict] = []
    for stamp in sorted(by_stamp, key=lambda s: datetime.strptime(s, TS_FMT), reverse=True):
        out.append({
            "stamp": stamp,
            "saved_at": datetime.strptime(stamp, TS_FMT).strftime("%Y-%m-%d %H:%M:%S"),
            "databases": by_stamp[stamp],
            "count": len(by_stamp[stamp]),
        })
    return out


def restore_snapshot(stamp: str) -> dict:
    """Revert the live databases to backup snapshot `stamp`. Returns a result dict::

        {"snapshot": stamp, "restored": [names], "missing": [names],
         "failed": [names], "errors": [str], "pre_restore_dir": str}

    - restored: live DB overwritten from ``{stem}_{stamp}.db``
    - missing : a configured DB with no backup in this snapshot (left untouched)
    - failed  : couldn't overwrite (locked .db — close the SQLite viewer and retry)

    Best-effort with no rollback: the current databases are copied to ``pre_restore``
    first, so recovery is a second restore from there if needed.
    """
    log.warning("RESTORE requested — reverting databases to backup snapshot %s", stamp)
    settings.DB_DIR.mkdir(parents=True, exist_ok=True)

    # Single-slot safety copy of the current live DBs (outside the dated backup set).
    pre = settings.BACKUP_DIR / "pre_restore"
    shutil.rmtree(pre, ignore_errors=True)
    pre.mkdir(parents=True, exist_ok=True)

    restored: list[str] = []
    missing: list[str] = []
    failed: list[str] = []
    errors: list[str] = []
    for live in database_paths():
        backup = settings.BACKUP_DIR / f"{live.stem}_{stamp}{live.suffix}"
        if not backup.exists():
            missing.append(live.name)
            continue
        try:
            if live.exists():
                shutil.copy2(live, pre / live.name)  # undo point for this restore
            shutil.copy2(backup, live)               # overwrite live with the backup
            # Drop stale WAL/SHM so SQLite never replays them onto the restored file.
            for sidecar in (live.with_name(live.name + "-wal"),
                            live.with_name(live.name + "-shm")):
                if sidecar.exists():
                    sidecar.unlink()
            restored.append(live.name)
        except OSError as exc:  # locked (SQLite viewer), permissions, etc.
            failed.append(live.name)
            errors.append(f"{live.name}: {exc}")

    log.warning("RESTORE complete — restored %d, missing %d, failed %d",
                len(restored), len(missing), len(failed))
    return {"snapshot": stamp, "restored": restored, "missing": missing,
            "failed": failed, "errors": errors, "pre_restore_dir": str(pre)}
