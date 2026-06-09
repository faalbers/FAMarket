"""
Logging setup — terminal + per-run file (Topic 9.3).

Summary-level logging only: batch progress lines, fetcher start/stop, failures.
No per-symbol or per-value noise; sanitize fixes are silent. Every line carries
a timestamp via the formatter configured in config/settings.py.

Rotation strategy is one log per fetch run: roll_log() archives the current log as
a versioned backup in BACKUP_DIR — famarket_1.log (newest) .. famarket_5.log
(oldest), the same rotating scheme databases use (core.backup) — then starts a
fresh famarket.log. An empty log is not backed up. The fetch orchestrator calls it
at the top of every run, so both the CLI runner and the Streamlit Fetch Control
panel behave identically.

Typical batch line produced by callers:
    2026-06-06 19:42:11 [yfinance] INFO Batch 3/40 — Fetched: 100 | Success: 98 | Failed: 2 | Remaining: 3900
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from config import settings

_configured = False


def _make_file_handler() -> logging.FileHandler:
    handler = logging.FileHandler(settings.LOG_FILE, encoding="utf-8")
    handler.setFormatter(logging.Formatter(settings.LOG_FORMAT, datefmt=settings.LOG_DATEFMT))
    return handler


def roll_log() -> None:
    """Versioned-backup the current log into BACKUP_DIR, then start fresh.

    Each fetch run (and the reset action) begins with an empty famarket.log while
    the previous run's log is archived as a dated backup —
    famarket_{YYYY-MM-DD_HH-MM-SS}.log, newest BACKUP_VERSIONS kept — exactly the
    scheme databases use (core.backup.backup_file). An empty log is NOT backed up.
    Safe whether or not logging is already configured: any handler holding LOG_FILE
    open is closed first (so the file can be swapped on Windows) and re-created
    against the fresh file.
    """
    global _configured
    log_file = settings.LOG_FILE
    settings.LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    had_file_handler = False
    for h in list(root.handlers):
        if isinstance(h, logging.FileHandler) and _same_path(h.baseFilename, log_file):
            root.removeHandler(h)
            h.close()
            had_file_handler = True

    # Only back up a non-empty log (skip when missing or empty), then start fresh.
    if log_file.exists() and log_file.stat().st_size > 0:
        from core.backup import backup_file  # lazy: avoid backup<->logging import cycle

        backup_file(log_file)  # dated copy into BACKUP_DIR (famarket_<stamp>.log)
        log_file.unlink()      # fresh start; recreated below / by setup_logging

    # If logging was already running, re-attach a handler on the fresh file so
    # subsequent log lines flow to it. Otherwise setup_logging adds it later.
    if had_file_handler:
        root.addHandler(_make_file_handler())


def _same_path(a: str, b: Path) -> bool:
    try:
        return Path(a).resolve() == b.resolve()
    except OSError:
        return False


def setup_logging() -> None:
    """Configure root logging once. Idempotent — safe to call on every entry."""
    global _configured
    if _configured:
        return

    settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(settings.LOG_FORMAT, datefmt=settings.LOG_DATEFMT)

    # Windows consoles default to a legacy code page (cp1252) that can't render
    # the em-dash used in batch log lines; force the stream to UTF-8.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(settings.LOG_LEVEL)
    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(_make_file_handler())

    _quiet_noisy_libraries()
    _configured = True


# Third-party loggers that emit per-symbol noise which violates our summary-level
# policy (Topic 9.3) — most notably yfinance's "HTTP Error 404: quote not found"
# ERROR for delisted/unknown tickers. These symbols are accounted for in the
# fetcher's batch "No-data" count, so the raw library errors are pure noise here.
_NOISY_LIBRARIES = ("yfinance", "peewee", "urllib3", "curl_cffi")


def _quiet_noisy_libraries() -> None:
    for name in _NOISY_LIBRARIES:
        logging.getLogger(name).setLevel(logging.CRITICAL)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger (e.g. the fetcher name), configuring on first use."""
    setup_logging()
    return logging.getLogger(name)
