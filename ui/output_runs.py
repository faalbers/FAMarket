"""
Filter-run persistence (Topic 6) — one saved "run" per Run Filter click.

Each run is a parquet (the result rows) + json (metadata sidecar) pair in
`settings.OUTPUT_RUNS_DIR`, named by a run id that is URL-safe, Windows-safe and
sorts chronologically by name:

    {YYYY-MM-DD_HH-MM-SS-ffffff}_{uuid4().hex[:8]}
    e.g. 2026-06-11_14-03-22-481291_a1b2c3d4

Why files instead of session state: every browser tab is its own Streamlit
session, so persisting runs to disk lets each run open in its own tab at
/output?run=<id>, keeps several output screens alive side by side, and survives
app restarts. The sidecar carries everything the Output launcher needs to list
runs (name, time, row count, types) WITHOUT reading any row data.

Parquet over pickle: pandas explicitly disclaims pickle compatibility across
versions, and these files must outlive upgrades. pyarrow is already a Streamlit
dependency. Retention is count-capped (newest `settings.OUTPUT_RUNS_KEEP`),
pruned on each save — the same newest-N idea as core/backup.py.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd

from config import settings
from core.logging_config import get_logger

log = get_logger("output_runs")

# Run ids come straight from the URL (?run=...) and are joined into file paths,
# so anything outside this strict shape is rejected before touching the disk.
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _dir() -> Path:
    settings.OUTPUT_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    return settings.OUTPUT_RUNS_DIR


def save_run(df: pd.DataFrame, *, screen_types: list[str], param_cols: list[str],
             filter_name: str | None, blocks: list[dict]) -> str:
    """Persist one filter run (rows + metadata); prune old runs; return its id."""
    # Microseconds included so same-second saves still sort chronologically by
    # name (prune/list rely on name order; the uuid tail is NOT a tiebreaker).
    run_id = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S-%f')}_{uuid.uuid4().hex[:8]}"
    out = _dir()
    try:
        df.to_parquet(out / f"{run_id}.parquet", index=False)
    except Exception:
        # Arrow rejects mixed-type object columns (rare; SQLite's dynamic typing
        # can produce them) — cast object columns to string and retry once.
        obj_cols = df.select_dtypes(include="object").columns
        df = df.copy()
        df[obj_cols] = df[obj_cols].astype("string")
        df.to_parquet(out / f"{run_id}.parquet", index=False)
    meta = {
        "version": 1,
        "run_id": run_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "filter_name": filter_name,
        "screen_types": list(screen_types),
        "param_cols": list(param_cols),
        "row_count": int(len(df)),
        "blocks": blocks,
    }
    (out / f"{run_id}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log.info("Saved filter run %s — %d rows (%s)", run_id, len(df),
             filter_name or "ad-hoc")
    _prune()
    return run_id


def load_run(run_id: str) -> tuple[pd.DataFrame, dict] | None:
    """(rows, metadata) for a run id, or None when missing/pruned/corrupt."""
    if not run_id or not _RUN_ID_RE.match(run_id):
        return None
    pq, js = _dir() / f"{run_id}.parquet", _dir() / f"{run_id}.json"
    if not pq.exists() or not js.exists():
        return None
    try:
        meta = json.loads(js.read_text(encoding="utf-8"))
        df = pd.read_parquet(pq)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return df, meta


def list_runs() -> list[dict]:
    """Metadata of every saved run, newest first (corrupt sidecars skipped)."""
    runs: list[dict] = []
    for js in _dir().glob("*.json"):
        try:
            runs.append(json.loads(js.read_text(encoding="utf-8")))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    # The timestamp prefix makes name order chronological; newest first.
    return sorted(runs, key=lambda m: m.get("run_id", ""), reverse=True)


def _prune() -> None:
    """Keep the newest OUTPUT_RUNS_KEEP runs; delete the parquet+json beyond."""
    stems = sorted((p.stem for p in _dir().glob("*.json")), reverse=True)
    for stem in stems[settings.OUTPUT_RUNS_KEEP:]:
        for suffix in (".parquet", ".json"):
            (_dir() / f"{stem}{suffix}").unlink(missing_ok=True)
        log.info("Pruned old filter run %s", stem)
