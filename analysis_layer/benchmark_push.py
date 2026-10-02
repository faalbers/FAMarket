"""
Push a trimmed copy of indices.db to FAPortfolio's `benchmark-data` branch.

FAPortfolio's GitHub Track runs in the cloud and cannot read FAMarket's databases, so
after a FULL analysis run rebuilds `indices.db` this module hands the industry indices
over through a data branch of the FAPortfolio repo:

  1. Skip if the source `index_meta` stamp equals the last pushed one (state file).
  2. Build `indices.db.gz` in a temp dir: same two tables, `index_meta` copied
     unchanged, `sector_industry_index` trimmed to `date >= prices_as_of - 1490 days`.
  3. Verify the extract row-for-row against the source window before pushing.
  4. Commit it as ONE orphan commit in a fresh temp repo and force-push it to
     `benchmark-data` only (explicit refspec) over SSH with a dedicated deploy key.
  5. Read back: fetch the branch, check it is our commit, 1 commit deep, holds only
     the file, and its `index_meta` matches the source. Then record the state.

FAMarket's own checkout and remotes are never touched. The contract (branch name,
file name, tables, trim window) belongs to FAPortfolio — see the handoff in
dev_docs/famarket_benchmark_push.md — so it is fixed here, not a setting.

    python -m analysis_layer.benchmark_push             # push if there is something new
    python -m analysis_layer.benchmark_push --force     # push even if unchanged
    python -m analysis_layer.benchmark_push --dry-run   # build + verify only, no network
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from config import settings
from core.logging_config import get_logger

log = get_logger("benchmark_push")

# FAPortfolio's contract — do not change without updating FAPortfolio.
BRANCH = "benchmark-data"
FILE_NAME = "indices.db.gz"
WINDOW_DAYS = 1460 + 30  # Track's 1460-day baseline + 30-day margin
_SERIES_DDL = 'CREATE TABLE sector_industry_index("kind" TEXT,"label" TEXT,"date" TEXT,"level" REAL)'
_META_DDL = ('CREATE TABLE index_meta("built_at" TEXT,"prices_as_of" TEXT,"start_date" TEXT,'
             '"end_date" TEXT,"field" TEXT,"n_sectors" INTEGER,"n_industries" INTEGER,'
             '"n_constituents" INTEGER)')
_META_COLS = "built_at, prices_as_of, start_date, end_date, field, n_sectors, n_industries, n_constituents"
_SERIES_SQL = ("SELECT kind, label, date, level FROM sector_industry_index WHERE date >= ? "
               "ORDER BY kind, label, date")

_GIT_TIMEOUT_S = 180


class PushError(RuntimeError):
    """A step of the push failed; the message says which and why."""


@dataclass(frozen=True)
class Extract:
    built_at: str
    prices_as_of: str
    since: str
    rows: int
    gz_bytes: int


# ---------------------------------------------------------------------------- #
# Build + verify
# ---------------------------------------------------------------------------- #

def _connect_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _read_meta(path: Path) -> list[tuple]:
    con = _connect_ro(path)
    try:
        return con.execute(f"SELECT {_META_COLS} FROM index_meta").fetchall()
    finally:
        con.close()


def build_extract(src: Path, out_gz: Path) -> Extract:
    """Write the trimmed, gzipped copy of `src` to `out_gz`."""
    con = _connect_ro(src)
    try:
        meta = con.execute(f"SELECT {_META_COLS} FROM index_meta").fetchall()
        if not meta or not meta[0][1]:
            raise PushError("index_meta has no prices_as_of stamp")
        built_at, prices_as_of = str(meta[0][0]), str(meta[0][1])
        since = (date.fromisoformat(prices_as_of[:10]) - timedelta(days=WINDOW_DAYS)).isoformat()
        rows = con.execute(_SERIES_SQL, (since,)).fetchall()
    finally:
        con.close()
    if not rows:
        raise PushError(f"no index rows on or after {since}")

    flat = out_gz.with_suffix("")  # indices.db next to the .gz
    flat.unlink(missing_ok=True)
    out = sqlite3.connect(flat)
    try:
        out.execute(_SERIES_DDL)
        out.execute(_META_DDL)
        out.executemany("INSERT INTO sector_industry_index VALUES (?,?,?,?)", rows)
        out.executemany("INSERT INTO index_meta VALUES (?,?,?,?,?,?,?,?)", meta)
        out.commit()
        out.execute("VACUUM")
    finally:
        out.close()
    with open(flat, "rb") as a, gzip.open(out_gz, "wb") as b:
        shutil.copyfileobj(a, b)
    flat.unlink()
    return Extract(built_at, prices_as_of, since, len(rows), out_gz.stat().st_size)


def _gunzip(gz: Path, dest: Path) -> Path:
    with gzip.open(gz, "rb") as a, open(dest, "wb") as b:
        shutil.copyfileobj(a, b)
    return dest


def verify_extract(src: Path, gz: Path, ex: Extract) -> None:
    """Decompress the extract and compare it with the source window, row for row.

    Every (kind, label, date, level) row and the whole index_meta must be identical —
    a stronger check than spot-comparing a few labels, and cheap at ~160k rows.
    """
    flat = _gunzip(gz, gz.with_name("verify.db"))
    try:
        src_con, ext_con = _connect_ro(src), _connect_ro(flat)
        try:
            src_rows = src_con.execute(_SERIES_SQL, (ex.since,)).fetchall()
            ext_rows = ext_con.execute(_SERIES_SQL, (ex.since,)).fetchall()
            ext_total = ext_con.execute("SELECT COUNT(*) FROM sector_industry_index").fetchone()[0]
        finally:
            src_con.close()
            ext_con.close()
        if ext_total != len(ext_rows):
            raise PushError(f"extract holds {ext_total - len(ext_rows)} rows before {ex.since}")
        if len(src_rows) != len(ext_rows):
            raise PushError(f"extract has {len(ext_rows)} rows, source window {len(src_rows)}")
        bad = next((s for s, e in zip(src_rows, ext_rows) if s != e), None)
        if bad is not None:
            raise PushError(f"extract differs from source at {bad[0]} '{bad[1]}' {bad[2]}")
        if _read_meta(flat) != _read_meta(src):
            raise PushError("extract index_meta differs from source")
    finally:
        flat.unlink(missing_ok=True)


# ---------------------------------------------------------------------------- #
# Git
# ---------------------------------------------------------------------------- #

def _git_env(key: Path) -> dict[str, str]:
    """Environment that makes git use ONLY the deploy key, and never prompt."""
    env = dict(os.environ)
    env["GIT_SSH_COMMAND"] = (f'ssh -i "{key.as_posix()}" -o IdentitiesOnly=yes '
                              "-o StrictHostKeyChecking=accept-new -o BatchMode=yes")
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git(args: list[str], cwd: Path, env: dict[str, str]) -> str:
    """Run git; raise PushError naming the step on failure or timeout."""
    try:
        res = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True,
                             text=True, timeout=_GIT_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise PushError(f"git {args[0]} timed out after {_GIT_TIMEOUT_S}s") from exc
    if res.returncode != 0:
        detail = (res.stderr or res.stdout).strip().splitlines()
        raise PushError(f"git {args[0]} failed: {detail[-1] if detail else res.returncode}")
    return res.stdout.strip()


def _push_and_read_back(work: Path, ex: Extract, src: Path, remote: str,
                        env: dict[str, str]) -> str:
    """Orphan-commit the file, force-push it to BRANCH, and verify what landed."""
    _git(["init", "--quiet", f"--initial-branch={BRANCH}"], work, env)
    _git(["add", FILE_NAME], work, env)
    _git(["-c", "user.name=FAMarket", "-c", f"user.email={settings.BENCHMARK_PUSH_AUTHOR_EMAIL}",
          "-c", "commit.gpgsign=false", "commit", "--quiet", "-m",
          f"FAMarket industry indices, prices as of {ex.prices_as_of} (built {ex.built_at})"],
         work, env)
    local = _git(["rev-parse", "HEAD"], work, env)
    # Explicit refspec: this can only ever write refs/heads/benchmark-data.
    _git(["push", "--force", remote, f"HEAD:refs/heads/{BRANCH}"], work, env)

    _git(["fetch", "--quiet", remote, f"refs/heads/{BRANCH}"], work, env)
    landed = _git(["rev-parse", "FETCH_HEAD"], work, env)
    if landed != local:
        raise PushError(f"read-back: branch is at {landed[:12]}, expected {local[:12]}")
    depth = _git(["rev-list", "--count", "FETCH_HEAD"], work, env)
    if depth != "1":
        raise PushError(f"read-back: branch has {depth} commits, expected 1")
    files = _git(["ls-tree", "--name-only", "FETCH_HEAD"], work, env).splitlines()
    if files != [FILE_NAME]:
        raise PushError(f"read-back: branch root holds {files}, expected [{FILE_NAME}]")
    shown = subprocess.run(["git", "show", f"FETCH_HEAD:{FILE_NAME}"], cwd=work, env=env,
                           capture_output=True, timeout=_GIT_TIMEOUT_S)
    if shown.returncode != 0:
        raise PushError(f"read-back: git show failed: {shown.stderr.decode(errors='replace').strip()}")
    back_gz = work / "readback.db.gz"
    back_gz.write_bytes(shown.stdout)
    back = _gunzip(back_gz, work / "readback.db")
    if _read_meta(back) != _read_meta(src):
        raise PushError("read-back: pushed index_meta differs from source")
    return landed


# ---------------------------------------------------------------------------- #
# State + entry points
# ---------------------------------------------------------------------------- #

def _last_pushed() -> dict[str, Any]:
    try:
        return json.loads(settings.BENCHMARK_PUSH_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _record_push(ex: Extract, commit: str) -> None:
    settings.BENCHMARK_PUSH_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    settings.BENCHMARK_PUSH_STATE_FILE.write_text(json.dumps({
        "built_at": ex.built_at, "prices_as_of": ex.prices_as_of,
        "rows": ex.rows, "gz_bytes": ex.gz_bytes, "commit": commit,
    }, indent=2), encoding="utf-8")


def push(*, force: bool = False, dry_run: bool = False) -> dict[str, Any]:
    """Build, verify and push the extract. Raises PushError on any failure."""
    src: Path = settings.INDICES_DB
    if not src.exists():
        raise PushError(f"{src.name} does not exist")
    meta = _read_meta(src)
    if not meta:
        raise PushError("index_meta is empty")
    built_at, prices_as_of = str(meta[0][0]), str(meta[0][1])
    last = _last_pushed()
    if (not force and not dry_run and last.get("built_at") == built_at
            and last.get("prices_as_of") == prices_as_of):
        log.info("Benchmark push — nothing new (prices as of %s, built %s already pushed)",
                 prices_as_of, built_at)
        return {"status": "unchanged", "prices_as_of": prices_as_of, "built_at": built_at}

    key: Path = settings.BENCHMARK_PUSH_KEY
    if not dry_run and not key.exists():
        raise PushError(f"deploy key not found at {key}")

    # ignore_cleanup_errors: git marks its object files read-only, which makes
    # Windows refuse the temp-dir delete; a leftover temp dir is harmless.
    with tempfile.TemporaryDirectory(prefix="famarket_push_", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        gz = work / FILE_NAME
        ex = build_extract(src, gz)
        verify_extract(src, gz, ex)
        log.info("Benchmark push — extract verified: %d rows since %s, %.1f MB gzipped",
                 ex.rows, ex.since, ex.gz_bytes / 1e6)
        if dry_run:
            return {"status": "dry_run", "rows": ex.rows, "since": ex.since,
                    "gz_bytes": ex.gz_bytes, "prices_as_of": ex.prices_as_of}
        commit = _push_and_read_back(work, ex, src, settings.BENCHMARK_PUSH_REMOTE,
                                     _git_env(key))

    _record_push(ex, commit)
    log.info("Benchmark push — pushed %s to %s (prices as of %s, commit %s), read-back OK",
             FILE_NAME, BRANCH, ex.prices_as_of, commit[:12])
    return {"status": "pushed", "rows": ex.rows, "gz_bytes": ex.gz_bytes,
            "prices_as_of": ex.prices_as_of, "built_at": ex.built_at, "commit": commit}


def push_after_analysis() -> dict[str, Any] | None:
    """Pipeline hook: push if enabled, and never let a failure escape."""
    if not settings.BENCHMARK_PUSH_ENABLED:
        return None
    try:
        return push()
    except Exception as exc:  # a missed push must never fail the analysis run
        log.warning("Benchmark push failed — FAPortfolio keeps the previous file: %s", exc)
        return {"status": "failed", "error": str(exc)}


def main() -> None:
    p = argparse.ArgumentParser(description="Push indices.db to FAPortfolio's benchmark-data branch")
    p.add_argument("--force", action="store_true", help="push even if already pushed")
    p.add_argument("--dry-run", action="store_true", help="build + verify only; no network")
    args = p.parse_args()
    try:
        result = push(force=args.force, dry_run=args.dry_run)
    except PushError as exc:
        log.error("Benchmark push failed: %s", exc)
        raise SystemExit(1) from exc
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
