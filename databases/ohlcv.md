# ohlcv.db — daily price history, dividends and splits

Daily price bars for every traded symbol, up to 30 years back, with dividend and split events on the same rows.
This is the price source for every calculation in the other databases. Very large (~13 GB); always filter by `symbol` and/or `date` in queries.

Join key: `symbol` (see `symbols.md` for shared conventions).

## How it is retrieved

- Source: Yahoo Finance daily price history, requested with both unadjusted and adjusted closes plus dividend and split events.
- Window: a fixed 30 years back from the last completed trading session (e.g. AAPL: 1996-11-06 → 2026-11-06).
- Covered types: stock, etf, adr, preferred, closed_end_fund, mutual_fund, plus a short benchmark index list: `^GSPC` (S&P 500), `^IXIC`, `^DJI`, `^NDX`, `^RUT`, `^VIX`, `^TNX`, `^IRX`.
  - All other index symbols, warrants, units and SPACs have no price history here.
- **Each fetch fully replaces a symbol's rows** with what Yahoo returned. Yahoo re-adjusts its whole history after every split or dividend, so the stored series is always on one consistent adjustment basis.
  - Exception: if Yahoo returned less than half the bars already stored, the old rows are kept and the new ones merged in (see `fetch_status` coverage flags in `symbols.md`).
- **Repair only when broken** (since 2026-11-12): if a symbol's `adj_close` comes back impossible (≤ 0, or above `close`), it is downloaded again with yfinance's price repair. The repaired series is kept only if it is clean, pays dividends on the same dates (amounts may be corrected: 100x unit mix-ups, a dividend on a split day) and has no fewer bars. Typical cause: a foreign-currency dividend applied to a USD price on OTC lines of Japanese companies. Repair is not run on healthy symbols — there it deleted real dividends (CRH, QQQ, DNP).
- **Only completed sessions are stored.** A bar is written only once that day's close has settled (close + 15 min, NYSE calendar). There are never intraday or partial bars.
- Only real trading days appear — no weekend/holiday rows, no gap filling.

## Table `ohlcv` — one row per (symbol, date)

- **`symbol`**, **`date`** (unique key).
- **`open`**, **`high`**, **`low`**, **`close`** — Yahoo's prices, **adjusted for splits but not for dividends**. Example: AAPL's close on 2020-08-28 is stored as 124.81, not the pre-4:1-split 499.
- **`adj_close`** — close adjusted for splits **and** dividends (total-return series). All return and indicator calculations in `analysis.db` use this.
- **`volume`** — shares traded, split-adjusted (scaled up for later splits). `close × volume` is the real dollar volume that day.
- **`dividends`** — cash dividend per share with its ex-date on this row; `0` on days without one. Split-adjusted like the prices.
- **`splits`** — split ratio on the ex-date (`4.0` = 4-for-1, `0.1` = 1-for-10 reverse); `0` on days without one.

## Table `price_flags` — known defects, one row per flagged date or range

Written on every fetch from the stored rows (`data_layer/price_checks.py`); a clean re-fetch clears a symbol's old flags.

- **`symbol`**, **`check`**, **`start_date`**, **`end_date`**, **`detail`** (e.g. `x0.0002395, 1 bar(s)`, `12 bars`).
- **`check`** values:
  - **`bad_adj`** — `adj_close` ≤ 0 or above `close`; what repair could not fix (PTPIF).
  - **`bad_print`** — a ≥ 10x one-day move whose price comes back within 2x inside 5 bars; the range covers the bad bars (BHPLF 2026-10-15).
  - **`level_shift`** — a ≥ 10x move that stays: an unrecorded reverse split (QH 2026-04-27), an old and a new company stitched together (CHRD 2020-11-20), or a real collapse. Not corrected anywhere.
  - **`stale`** — 10+ bars in a row with an unchanged close and zero volume (no trade). Not checked for series with zero volume on ≥ 95% of bars (mutual funds).
  - **`gap`** — more than 31 days between two bars.
  - **`double_event`** — dividend + split on one day whose adjustment jumps > 2x (TDS 2005-05-16, 40x).
- Thresholds: `OHLCV_PRICE_BREAK_RATIO` (10), `OHLCV_STALE_RUN_DAYS` (10).
- **How it is used:** the analysis drops `bad_print` and `stale` bars and uses `close` for `adj_close` on `bad_adj` symbols in its 2-year window; every flag in the window is listed in `analysis.price_issues` (see `analysis.md`).

## Things to know before analysing

- **Mutual funds are flat NAV series**: `open = high = low = close`, `volume = 0`. Range- and volume-based indicators mean nothing for them.
- **Price return vs total return**: use `close` for price return and `adj_close` for total return (dividends reinvested).
- **Historic valuation ratios**: because `close` is split-adjusted, multiplying an old `close` by a share count *as filed back then* gives a wrong market cap. Split-adjust old share counts first (multiply by every later split ratio).
- **Dividend yield from history**: divide the trailing dividends by `close`, not `adj_close`.
- Delisted symbols keep whatever history Yahoo still serves; their last bar is old. `symbols.last_active_date` holds each symbol's newest bar date.
- Some Yahoo histories have gaps or bad prints; check `price_flags` before trusting a stretch of history. A one-day move of 10x or more is almost always a data break — but on penny stocks it can be a real collapse.
