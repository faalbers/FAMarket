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
  - **Corrected after every quote fetch** (since 2026-11-12): a `mutual_fund` Yahoo calls EQUITY → `adr` if the name says ADR, else `stock` (companies on the SEC fund list: ASBHY, SBCLY); a `stock` with no Yahoo market cap whose name reads as a preferred or note ("Preferred", "Depositary Shares", "Notes due", "Senior Notes" …) → `preferred` (ACGLN, AGNCL, ADAMG). Polygon discovery writes its own type back each run, so the correction runs every time.
  - **REITs are not a separate type here** — they are `stock`. The REIT/bank/insurance split is the `screen_type` column in `analysis.db`.
- **`polygon_type`** — Polygon's raw type code.
- **`market`** — Polygon market (`stocks` / `indices`).
- **`primary_exchange`** — Polygon's exchange MIC code (e.g. `XNAS`, `XNYS`).
- **`source`** — where the row came from: `polygon`, `edgar`, `edgar_mf`, or `benchmark` (seeded benchmark indices such as `^GSPC`, `^IXIC`).
- **`in_polygon`** — 1 if the symbol was in the latest Polygon list. 0 = dropped by Polygon (likely delisted). Rows are never deleted.
- **`cik`** — SEC Central Index Key, an integer. Since 2026-11-12 filled from the SEC ticker list on every EDGAR run (~96% of live stocks / ADRs).
- **`company_id`** — the company a listing belongs to (= `cik`). Share classes (GOOG / GOOGL, BRK-A / BRK-B) and an ADR with its foreign OTC twin (DTEGY / DTEGF) share one. NULL when the SEC list doesn't know the ticker.
- **`series_id`**, **`class_id`** — SEC fund series/class IDs (mutual funds only).
- **`is_active`** — 1 = still fetched. Flipped to 0 only when a fetch returned no data at all *and* had no errors (genuinely gone, not an API hiccup).
- **`deactivated_date`** — when `is_active` went to 0 (since 2026-11-12). While it is within `RECHECK_WINDOW_DAYS` (183), the symbol is switched back on for one try every `OHLCV_RECHECK_DAYS` (30) (`symbols.rearm_inactive`); a try that finds nothing keeps the original date, so the window ends. NULL = no retries: switched off before 2026-11-12, including the 1,906 confirmed dead by the one-time recovery pass that day (`scripts/recover_stuck_symbols.py`).
- **`is_validated`** — 1 = enough recent data to be analysed. Type-aware:
  - stock / adr: quote + price bar within 8 weeks + a financial statement within ~15 months.
  - etf / closed_end_fund / preferred: quote + price bar within 8 weeks.
  - everything else: a quote.
- **`last_active_date`** — date of the symbol's newest stored price bar.
- **`is_delisted`**, **`delisted_date`** — 1 = its price history has stopped; `delisted_date` = its last bar. Set after every price fetch (`symbols.mark_delisted`, since 2026-11-12): the last bar is more than max(5, 3 × its usual gap between bars) trading sessions older than the session it was last fetched in. Clears itself when a new bar arrives. Not judged for types without price history (warrant, unit, spac). `is_active` is not changed, so the symbol keeps being checked.
- **`regulatory`** — 1 = the company reports SEC "regulated operations" (ASC 980) items, i.e. a rate-regulated utility or pipeline. 0 = checked, not regulated. NULL = never checked (only stock/reit/adr with an SEC filing get checked).

**The analysis universe** = `is_active = 1 AND is_validated = 1 AND security_type != 'index' AND is_delisted != 1`.
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

- `security_type` comes from Polygon's coarse codes, so some odd instruments are mistyped (e.g. `SP` is mapped to `spac`); the correction pass catches preferreds named as such and SEC-list companies, not every case.
- **Yahoo data outages** drop weeks of bars for many symbols at once (150 symbols missing 2026-10-02..11-06). Such a symbol is marked delisted until Yahoo fills the hole; a burst of 25+ new marks on one date is logged as a warning. Price-fetch give-ups (stale prices, repeated no-data answers) are re-checked monthly while the newest bar is under 6 months old, so it comes back.
- Warrants, units and SPACs have no price history, so `is_delisted` can't judge them; Yahoo's quote time is not reliable for it (stamped with the latest close even when nothing traded).
- Companies delisted before the system first ran were never discovered (survivorship). Dead symbols since then are kept with their history.
- Abandoned or deactivated symbols keep their old rows in the other databases. Check freshness (`fetched_at`, `last_active_date`) before trusting them.
