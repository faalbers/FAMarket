# FAMarket — Stock Screening System

A flexible, Python-based system that gathers US stock-market data from free APIs
and screens for (mainly long-term) investment candidates. Local-only; the UI is
an interactive Streamlit app.

> Full design lives in [`ROADMAP.md`](ROADMAP.md) (the completed brainstorm) and
> [`Stock_Screening_Analysis.md`](Stock_Screening_Analysis.md) (the original
> blueprint). Read those before making architectural changes.

## Architecture — three independent layers

1. **Data Layer** (`data_layer/`) — discovers the symbol universe, fetches data
   from yfinance / Polygon / FMP / E*Trade / FRED / SEC EDGAR, and stores it in
   separate SQLite databases.
2. **Analysis Layer** (`analysis_layer/`) — reads the data DBs and fully rebuilds
   `analysis.db` each run with metrics, technical indicators, peer comparisons,
   intrinsic values, and 0–100 scores.
3. **UI Layer** (`ui/` + `app.py`) — Streamlit app for fetch control, the
   block-based filter builder, and the results/output interface.

The layers are intentionally decoupled: you can swap a data source, add a metric,
or change the output without touching the others. Shared infrastructure (the
SQLite wrapper, logging, backups) lives in `core/`; all tunable settings live in
`config/`.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.template .env   # then fill in your free API keys
streamlit run app.py
```

## Build status

Scaffold (Phase 0) is in place: package layout, the SQLite wrapper, config,
logging, and backup are functional; fetchers, analysis, and UI pages are
documented skeletons. Build order is **Data → Analysis → UI**, each layer fully
before the next. See `ROADMAP.md` → "Topic 8 — Build Phases".

## Data flow

```
Polygon/EDGAR ─► symbols.db ─┐
yfinance/FMP/E*Trade ────────┼─► quotes.db, ohlcv.db, financials.db ─► Analysis ─► analysis.db ─► Filter UI ─► Output
FRED ────────────────────────┴─► macro.db
```

Fetch runs manually (Friday evening, after the close); analysis runs
automatically afterward; the UI reads `analysis.db`.
