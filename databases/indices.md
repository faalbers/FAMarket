# indices.db — self-built sector and industry index series

Daily index levels for every Yahoo **sector** and every **sector | industry** group, built from the stock data in the other databases.
Each series starts at 100, so a level reads as growth since that series' start (180 = +80%). Rebuilt from scratch on every full analysis run.

Snapshot when written (2026-11-09): 11 sectors, 143 industries, data to 2026-11-06, ~4.3k constituent stocks.

## Table `sector_industry_index` — one row per (kind, label, date), long/tidy

- **`kind`** — `sector` or `industry`.
- **`label`** — the Yahoo sector name (e.g. `Technology`), or `"<sector> | <industry>"` for industries (e.g. `Technology | Semiconductors`). Same names as `sector`/`industry` in `quotes.db` and `analysis.db`.
- **`date`** — trading day.
- **`level`** — index level, 100 on the series' own first day.

Series start on different dates (sectors ~2008-2010, some industries later), so compare two series only over their common dates, rebased to the same day.

## Table `index_meta` — one row describing the build

- **`built_at`** (UTC), **`prices_as_of`** (last price date used), **`start_date`** / **`end_date`** (overall span), **`field`** (`adj_close` = total return), **`n_sectors`**, **`n_industries`**, **`n_constituents`**.

## How the levels are calculated

The method follows the S&P **SPDR Select Sector** index rules (the XLK, XLF, … sector ETFs):

- **Formula**: level moves with the float-adjusted, capped market-cap-weighted return of its members.
  - Daily moves use `adj_close` — so it is a **total-return** index (dividends reinvested).
  - Weights use split-adjusted `close` × shares outstanding × float factor (floatShares / sharesOutstanding from `quotes.db`).
- **Rebalancing**: weights are reset quarterly (first trading day on/after the third Friday of Mar/Jun/Sep/Dec) and held fixed in between.
- **Capping** (current Select Sector rule): no single stock above 24% (cut to 23%); the stocks above 4.8% together capped at 50% (cut to 45%); the rest capped at 4.5%.
- **Shares**: from `financials.db` (`ordinary_shares_number`, else `share_issued`), interpolated daily between reports, put on today's split basis, and rescaled when today's implied market cap disagrees with Yahoo's `marketCap` by more than 2x (wrong share-class or ADR units).

## Who is included

- Active, analysable symbols (see `symbols.md`) that have a Yahoo sector.
- **Liquidity floor**: average daily dollar volume of at least $1M over the last 63 trading days — both today (to be a member at all) and at each rebalance (to be weighted then).
- **Liquidity ratio**: at a rebalance, a year of dollar volume must be at least 10% of the float market cap. Filters out market caps no real trading supports (bad share counts).
- **One class per company**: multi-class companies (GOOG/GOOGL, BRK-A/BRK-B) keep only their most liquid ticker.
- Industries need at least 3 members.
- **Breadth before start**: a sector series starts once 20 members can be weighted, an industry once 2 can (or the group's most-ever, if it never gets there).
- **Price-break guard**: a one-day move of 10x or more (up or down) for a member is treated as a data error and counted as 0%.

## Known limitations

- **Survivorship bias**: membership comes from symbols active today. Companies that were delisted, went bankrupt or were acquired before today are missing from the history, so older returns are biased upward.
- Sector and industry tags are today's Yahoo tags applied to the whole history.
- History is limited by share-count data: most starts are the late-2008 filings, so nothing before ~2008.
- These are approximations of the SPDR method on free data, not official index values. Sector series track the SPDR ETFs closely but not exactly.
- Small industries (2-5 names) are noisy and can be dominated by one company.
