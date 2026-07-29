# FAMarket — Stock Screening System

A local, Python-based system that gathers US stock-market data from free APIs,
computes fundamental and technical metrics, and screens for (mainly long-term)
investment candidates. Everything runs on your own machine — no server, no
account, single user.

> The full design lives in [`ROADMAP.md`](ROADMAP.md) — every architectural
> decision and the build sequencing. Read it before making architectural changes.

## Architecture — three independent layers

1. **Data layer** (`data_layer/`) — discovers the symbol universe and fetches
   from yfinance, Polygon, FRED and SEC EDGAR into separate SQLite databases.
2. **Analysis layer** (`analysis_layer/`) — reads those databases and rebuilds
   `analysis.db` each run with metrics, technical indicators, peer comparisons,
   intrinsic values and 0–100 scores.
3. **UI layer** — a React front end (`frontend/`) on a FastAPI backend (`api/`),
   with the UI-agnostic view logic in `services/`.

The layers are decoupled on purpose: you can swap a data source, add a metric or
change the output without touching the others. Shared infrastructure (the SQLite
wrapper, logging, backups) lives in `core/`; tunable settings live in `config/`.

## What you need installed

| Requirement | Version | Why |
|---|---|---|
| **Python** | 3.14 | data, analysis and the API |
| **Node.js** | 20.19+ or 22.12+ | building the front end (Vite 8) |
| **npm** | ships with Node | front-end packages |

Python packages come from [`requirements.txt`](requirements.txt); front-end
packages from [`frontend/package.json`](frontend/package.json). Both are pinned
loosely, and `frontend/package-lock.json` is committed for reproducible installs.

## First-time setup

```powershell
# 1. Python side
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. API keys
copy .env.template .env        # then fill it in — see below

# 3. Front end (one-off install, then build)
cd frontend
npm install
npm run build
cd ..
```

`npm run build` writes `frontend/dist`, which the server serves. It is
gitignored, so a fresh clone must build once before the app will start.

## Running it

```powershell
python scripts/serve_ui.py
```

One command: it serves the built front end and the API together, opens your
browser, and exits when you close the last tab. Useful flags: `--port`,
`--no-browser`, `--no-exit` (keep serving with no tabs open).

**Front-end development** (hot reload) needs two terminals:

```powershell
python -m uvicorn api.main:app --reload     # terminal 1 — API on :8000
cd frontend; npm run dev                    # terminal 2 — Vite on :5173
```

Vite proxies `/api` to the backend, so open <http://localhost:5173>.

### Other entry points

```powershell
.\run_app.bat                                 # same as serve_ui.py, double-clickable
python -m scripts.discover_symbols --edgar    # symbol discovery, no key needed
python -m scripts.discover_symbols --show     # summarise symbols.db
```

## API keys

Keys live only in `.env` (gitignored). Copy `.env.template` and fill in what you
need — everything is a free tier.

**Needed for a full data run**

- `POLYGON_API_KEY` — symbol universe and reference data. [polygon.io](https://polygon.io)
- `FRED_API_KEY` — macro series. [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html)
- `SEC_USER_AGENT` — SEC EDGAR requires a contact string, e.g.
  `FAMarket you@example.com`. Not a key; EDGAR rejects requests without it.

yfinance needs no key.

**Optional, per feature**

- `GMAIL_USER` / `GMAIL_APP_PASSWORD` — the Utilities email tool. Use a Google
  *app password*, not your account password; strip the spaces Google shows.
- `JINA_API_KEY` — improves article scraping for the AI news reports. The
  fallback works without it.
- `ANTHROPIC_API_KEY` — ad-hoc research scripts only, never the fetch pipeline.
- `FMP_API_KEY`, `ETRADE_*` — placeholders for deferred fetchers; leave blank.

## Type checking

```powershell
npx pyright api services      # Python — see pyrightconfig.json
cd frontend; npx tsc -b       # TypeScript
```

There is **no test framework** — a deliberate decision (ROADMAP topic 9.1).
Verification is manual: ad-hoc scripts plus a SQLite viewer over the `.db` files.

## Data flow

```
Polygon / EDGAR ──► symbols.db ──┐
yfinance ────────────────────────┼──► quotes.db, ohlcv.db, financials.db, signals.db
FRED ────────────────────────────┘         │
                                           ▼
                                    Analysis layer
                                           │
                                           ▼
                            analysis.db  +  indices.db
                                           │
                                           ▼
                              Filter ──► Output ──► Charts
```

A fetch runs as its own detached OS process, so it survives closing the app;
analysis runs automatically afterwards. Sector and industry indices are built on
**full** analysis runs only.

To reset everything, delete the `.db` files in `databases/` — the system detects
empty databases as an initial load. (Close any SQLite viewer first; it holds a
file lock on Windows.)

## Platform notes

Developed on Windows with PowerShell. This machine sits behind TLS interception,
so `core/net.configure_tls()` runs before any network call — any new entry point
must call it too. See `CLAUDE.md` for the details.
