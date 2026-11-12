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
- **Only completed sessions are stored.** A bar is written only once that day's close has settled (close + 15 min, NYSE calendar). There are never intraday or partial bars.
- Only real trading days appear — no weekend/holiday rows, no gap filling.

## Table `ohlcv` — one row per (symbol, date)

- **`symbol`**, **`date`** (unique key).
- **`open`**, **`high`**, **`low`**, **`close`** — Yahoo's prices, **adjusted for splits but not for dividends**. Example: AAPL's close on 2020-08-28 is stored as 124.81, not the pre-4:1-split 499.
- **`adj_close`** — close adjusted for splits **and** dividends (total-return series). All return and indicator calculations in `analysis.db` use this.
- **`volume`** — shares traded, split-adjusted (scaled up for later splits). `close × volume` is the real dollar volume that day.
- **`dividends`** — cash dividend per share with its ex-date on this row; `0` on days without one. Split-adjusted like the prices.
- **`splits`** — split ratio on the ex-date (`4.0` = 4-for-1, `0.1` = 1-for-10 reverse); `0` on days without one.

## Things to know before analysing

- **Mutual funds are flat NAV series**: `open = high = low = close`, `volume = 0`. Range- and volume-based indicators mean nothing for them.
- **Price return vs total return**: use `close` for price return and `adj_close` for total return (dividends reinvested).
- **Historic valuation ratios**: because `close` is split-adjusted, multiplying an old `close` by a share count *as filed back then* gives a wrong market cap. Split-adjust old share counts first (multiply by every later split ratio).
- **Dividend yield from history**: divide the trailing dividends by `close`, not `adj_close`.
- Delisted symbols keep whatever history Yahoo still serves; their last bar is old. `symbols.last_active_date` holds each symbol's newest bar date.
- Some Yahoo histories have gaps or occasional bad prints (e.g. a reverse split Yahoo didn't adjust). A one-day move of 10x or more is almost always a data break, not a real move.
