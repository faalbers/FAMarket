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


class _StampedFormatter(logging.Formatter):
    """LOG_FORMAT, but multi-line records repeat the timestamp on every line.

    A traceback is ONE log record whose continuation lines normally print bare —
    easy to misread as separate, unstamped entries (and impossible to grep by
    date). Prefixing each continuation line with the record's own timestamp
    keeps every line of the block visibly tied to when it happened.
    """

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if "\n" not in text:
            return text
        stamp = self.formatTime(record, self.datefmt)
        first, rest = text.split("\n", 1)
        cont = "\n".join(f"{stamp} | {line}" for line in rest.splitlines())
        return f"{first}\n{cont}"


def _make_formatter() -> logging.Formatter:
    return _StampedFormatter(settings.LOG_FORMAT, datefmt=settings.LOG_DATEFMT)


def _make_file_handler(log_file: Path | None = None) -> logging.FileHandler:
    handler = logging.FileHandler(log_file or settings.LOG_FILE, encoding="utf-8")
    handler.setFormatter(_make_formatter())
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

    # Import the backup helper BEFORE closing handlers: importing core.backup runs its
    # module-level get_logger("backup"), which (on first import in this process — e.g.
    # a fresh fetch subprocess) calls setup_logging() and opens a FileHandler on the
    # run log. Doing it now means that handler is present for — and closed by — the
    # loop below; if we imported it later (after the close loop) it would re-open the
    # file and the unlink() would fail trying to delete a file THIS process holds open.
    from core.backup import backup_file  # lazy: avoid backup<->logging import cycle

    root = logging.getLogger()
    had_file_handler = False
    for h in list(root.handlers):
        if isinstance(h, logging.FileHandler) and _same_path(h.baseFilename, log_file):
            root.removeHandler(h)
            h.close()
            had_file_handler = True

    # Only back up a non-empty log (skip when missing or empty), then start fresh.
    if log_file.exists() and log_file.stat().st_size > 0:
        try:
            backup_file(log_file)  # dated copy into BACKUP_DIR (famarket_<stamp>.log)
            log_file.unlink()      # fresh start; recreated below / by setup_logging
        except OSError as exc:
            # Another process (the app on its own app.log shouldn't, but a SQLite-style
            # viewer or a leftover handle might) holds the run log open, so it can't be
            # archived/removed right now. A log-rotation hiccup must never abort the
            # run — keep appending to the existing log instead.
            print(f"[FAMarket] Could not roll the run log ({exc}); appending instead.",
                  flush=True)

    # If logging was already running, re-attach a handler on the (possibly fresh) file
    # so subsequent log lines flow to it. Otherwise setup_logging adds it later.
    if had_file_handler:
        root.addHandler(_make_file_handler(log_file))


def _same_path(a: str, b: Path) -> bool:
    try:
        return Path(a).resolve() == b.resolve()
    except OSError:
        return False


def setup_logging(log_file: Path | None = None) -> None:
    """Configure root logging once. Idempotent — safe to call on every entry.

    `log_file` chooses the file the FileHandler writes to (defaults to the run log,
    `settings.LOG_FILE`). The Streamlit app passes `settings.APP_LOG_FILE` so it
    never opens the run log — that file belongs to the detached fetch process, which
    rolls it each run (a shared handle would make that roll's unlink fail on Windows).
    """
    global _configured
    if _configured:
        return

    settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = _make_formatter()

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
    root.addHandler(_make_file_handler(log_file))

    _quiet_noisy_libraries()
    _configured = True


# Third-party loggers that emit per-symbol noise which violates our summary-level
# policy (Topic 9.3) — most notably yfinance's "HTTP Error 404: quote not found"
# ERROR for delisted/unknown tickers. These symbols are accounted for in the
# fetcher's batch "No-data" count, so the raw library errors are pure noise here.
_NOISY_LIBRARIES = ("yfinance", "peewee", "urllib3", "curl_cffi")


class _BenignDisconnectFilter(logging.Filter):
    """Drop asyncio's ConnectionResetError noise from abrupt client disconnects.

    On Windows' Proactor event loop, closing the browser tab while a stream is
    in flight makes the transport's connection_lost callback call
    socket.shutdown() on a socket the peer already reset → WinError 10054,
    which asyncio logs as ERROR ("Exception in callback
    _ProactorBasePipeTransport._call_connection_lost()"). The session is being
    torn down anyway (autoshutdown follows), so this is pure noise — a known
    CPython/Proactor quirk. Any other asyncio error still passes through.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if "_call_connection_lost" not in record.getMessage():
            return True
        exc = record.exc_info[1] if record.exc_info else None
        return not isinstance(exc, ConnectionResetError)


def _quiet_noisy_libraries() -> None:
    for name in _NOISY_LIBRARIES:
        logging.getLogger(name).setLevel(logging.CRITICAL)
    logging.getLogger("asyncio").addFilter(_BenignDisconnectFilter())


def get_logger(name: str) -> logging.Logger:
    """Return a named logger (e.g. the fetcher name), configuring on first use."""
    setup_logging()
    return logging.getLogger(name)
