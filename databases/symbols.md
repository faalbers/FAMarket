# symbols.db — the symbol universe and fetch bookkeeping

The master list of every US-listed security the system knows about, plus its type and state flags.
Every other database joins on its `symbol` column. Also holds `fetch_status`, the per-fetcher bookkeeping of when each symbol was last fetched.

Snapshot when written (2026-11-09): ~55k symbols, ~116k fetch_status rows.

## Conventions shared by all databases in this folder

- **`symbol`** — the canonical ticker in Yahoo Finance / E*Trade spelling (`BRK-B`, `ABR-PD`, `XYZ-WT`, `^GSPC`). It is the join key across every `.db` file.
- **Separate files, no cross-file joins** — each data type lives in its own SQLite file. Combine them yourself on `symbol` (and on date where relevant).
- **NULL means "not applicable / not available"** — never zero.
- **Dates** are ISO text (`YYYY-MM-DD`). Timestamps ending in `+00:00` are UTC.
- **Only US-market securities.** Foreign companies appear only through US listings (ADRs, OTC tickers).

## Table `symbols` — one row per symbol

Built by merging three discovery sources, in priority order. A later source only **adds** symbols the earlier ones lack; it never overwrites.

1. **Polygon.io** ticker list (`stocks` and `indices` markets, active tickers only) — the main source.
2. **SEC EDGAR `company_tickers.json`** — operating companies Polygon missed.
3. **SEC EDGAR `company_tickers_mf.json`** — mutual-fund share classes (the only free list of them).

Columns:

- **`symbol`** (key) — canonical ticker. Converted from Polygon spelling: `I:SPX`→`^SPX`, `ABC.WS`→`ABC-WT`, `ABC.U`→`ABC-UN`, `ABRpD`→`ABR-PD`, `BRK.B`→`BRK-B`.
- **`polygon_symbol`** — the raw Polygon ticker (NULL for EDGAR-sourced rows).
- **`name`** — security name from Polygon or EDGAR. NULL for mutual funds (the SEC fund file has no names; use `quotes.db` `longName`).
- **`security_type`** — normalized type. Values: `stock`, `etf`, `mutual_fund`, `adr`, `preferred`, `closed_end_fund`, `spac`, `warrant`, `unit`, `index`. NULL = never resolved.
  - Mapped from Polygon's type code (`CS`→stock, `ADRC`→adr, `ETF/ETN/ETV/ETS`→etf, `FUND`→closed_end_fund, `PFD`→preferred, `SP`→spac, `RIGHT`→warrant, `WARRANT`, `UNIT`).
  - EDGAR rows arrive untyped; their type is filled later from Yahoo's `quoteType` (EQUITY→stock, ETF, MUTUALFUND, INDEX).
  - **REITs are not a separate type here** — they are `stock`. The REIT/bank/insurance split is the `screen_type` column in `analysis.db`.
- **`polygon_type`** — Polygon's raw type code.
- **`market`** — Polygon market (`stocks` / `indices`).
- **`primary_exchange`** — Polygon's exchange MIC code (e.g. `XNAS`, `XNYS`).
- **`source`** — where the row came from: `polygon`, `edgar`, `edgar_mf`, or `benchmark` (seeded benchmark indices such as `^GSPC`, `^IXIC`).
- **`in_polygon`** — 1 if the symbol was in the latest Polygon list. 0 = dropped by Polygon (likely delisted). Rows are never deleted.
- **`cik`** — SEC Central Index Key (EDGAR rows only; stored as a float).
- **`series_id`**, **`class_id`** — SEC fund series/class IDs (mutual funds only).
- **`is_active`** — 1 = still fetched. Flipped to 0 only when a fetch returned no data at all *and* had no errors (genuinely gone, not an API hiccup).
- **`is_validated`** — 1 = enough recent data to be analysed. Type-aware:
  - stock / adr: quote + price bar within 8 weeks + a financial statement within ~15 months.
  - etf / closed_end_fund / preferred: quote + price bar within 8 weeks.
  - everything else: a quote.
- **`last_active_date`** — date of the symbol's newest stored price bar.
- **`regulatory`** — 1 = the company reports SEC "regulated operations" (ASC 980) items, i.e. a rate-regulated utility or pipeline. 0 = checked, not regulated. NULL = never checked (only stock/reit/adr with an SEC filing get checked).

**The analysis universe** = `is_active = 1 AND is_validated = 1 AND security_type != 'index'`.
Index symbols are reference-only. ~13.7k index symbols exist here, but only the benchmark list gets price history (see `ohlcv.md`).

## Table `fetch_status` — one row per (symbol, fetcher)

Bookkeeping for each data fetcher. Useful to judge how fresh a symbol's data is, or why it has none.

- **`symbol`**, **`fetcher_name`** (composite key). Fetcher names:
  - `yfinance_quotes` → quotes.db
  - `yfinance_ohlcv` → ohlcv.db
  - `yfinance_financials`, `edgar_financials` → financials.db
  - `yfinance_signals` → signals.db (`yfinance_estimates` is the same fetcher's old name; those rows are leftovers).
- **`last_fetched`** — UTC time of the last fetch that completed (with or without data). Not updated on an error.
- **`fetch_errors`** — consecutive failed attempts. Reset to 0 on any completed fetch.
- **`no_data_count`** — consecutive fetches that returned nothing. At 4 the pair is **abandoned** (no longer fetched). Resets the moment data returns.
- **`coverage_*`** — price-history sanity flags (yfinance_ohlcv only). Written only when a fetch returned under half the bars already stored; the stored history was then kept instead of replaced.
  - **`coverage_actual`** / **`coverage_expected`** — bars returned vs bars stored.
  - **`coverage_reason`** — `source_reset` (Yahoo itself no longer has the older years), `thin` (fewer than 30 stored bars, ratio meaningless), `truncated` (Yahoo glitch, retryable).
  - **`coverage_first_trade`** — Yahoo's reported first trade date.
  - **`coverage_checked_at`** — equals `last_fetched` while the flag is still current; older means a later clean fetch fixed it.

## Known limitations

- `security_type` comes from Polygon's coarse codes, so some odd instruments are mistyped (e.g. `SP` is mapped to `spac`).
- Abandoned or deactivated symbols keep their old rows in the other databases. Check freshness (`fetched_at`, `last_active_date`) before trusting them.
