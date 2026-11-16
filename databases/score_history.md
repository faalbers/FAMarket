# score_history.db — one snapshot of the scores per full analysis run

Keeps the category scores of every scored symbol, run after run, so the scores can be checked against what prices did next (fix list §18: "validate before treating scores as signals").
Written by `analysis_layer/pipeline._save_score_history` after each **full** analysis run; subset runs don't write it. Started 2026-11-15, so it holds only the runs since then.

## Table `score_history` — one row per (as_of, symbol)

- **`as_of`** — the run's price date (`analysis_meta.prices_as_of`). A rerun on the same price date replaces that date's rows.
- **`symbol`**, **`security_type`**, **`price`** — as in `analysis.db` on that run.
- **`rank_universe`** (0/1) — whether the row was in its ranking pool's reference set (see `analysis.md` → 11).
- **`value_score`**, **`quality_score`**, **`growth_score`**, **`momentum_score`**, **`income_score`**, **`overall_score`** — 0-100, NULL where too few inputs.

Rows with no score at all are skipped. Size: ~32k rows, ~3 MB per run.

## Known consumers

- None yet. Intended use: later returns per score decile across runs (join `price` of a later `as_of`, or `ohlcv.db` prices, on `symbol`).

## Known gaps / limitations

- Scores follow the rules and weights in force on each run; a rule change makes later snapshots not strictly comparable with earlier ones.
- Deleting this file loses the history; it is not rebuilt from other databases. The pre-fetch backups (`core/backup.py`) copy it like every other `.db`.
