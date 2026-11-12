# quotes.db — Yahoo Finance quote and profile snapshot

One row per symbol holding Yahoo Finance's `info` record: price snapshot, company profile, Yahoo's own pre-computed ratios, analyst targets, short interest, and fund details.
It is a **current snapshot only** — each fetch overwrites the symbol's row, so there is no history.

Join key: `symbol` (see `symbols.md` for shared conventions). Snapshot when written (2026-11-09): ~39.5k rows, 235 columns.

## How it is retrieved

- Source: Yahoo Finance's quote/profile record for each symbol — one request per symbol.
- Fetched for every active, non-index symbol, of every security type.
- Normally refetched weekly. Rows for symbols that later stopped being fetched (delisted, abandoned) **stay with their old values** — check `fetched_at`.
- Every scalar field Yahoo returns becomes its own column, named exactly as Yahoo names it (camelCase). Lists/dicts (e.g. company officers) are dropped. Columns only exist for fields Yahoo has ever returned, so most are sparse.
- Cleaning: blanks, `"N/A"`, `"-"`, NaN and infinities are stored as NULL. A symbol with no usable price at all gets no row.
- Values are stored **as Yahoo returns them**. Nothing is recalculated here.

## Columns added on top of Yahoo's fields

- **`symbol`** (key).
- **`quote_type`** — Yahoo's raw `quoteType` (`EQUITY`, `ETF`, `MUTUALFUND`, `INDEX`, …).
- **`security_type_yf`** — that type normalized to the `security_type` vocabulary of `symbols.db` (stock / etf / mutual_fund / index; NULL if unmapped).
- **`fetched_at`** — UTC time this row was written.
- **`fund_categoryName`**, **`fund_family`**, **`fund_legalType`** — ETFs and mutual funds only, from Yahoo's fund-overview block. `fund_family` is much better filled than Yahoo's `fundFamily`; prefer it.

## Main Yahoo field groups (not exhaustive)

- **Identity / profile** — `shortName`, `longName`, `displayName`, `sector`, `industry` (Yahoo's taxonomy — the sector/industry used in every other database), `country`, `city`, `website`, `longBusinessSummary`, `fullTimeEmployees`, `exchange`, `fullExchangeName`, `currency`, `financialCurrency`.
- **Price snapshot** — `currentPrice`, `regularMarketPrice`, `previousClose`, `open`, `dayLow`, `dayHigh`, `volume`, `averageVolume`, `fiftyTwoWeekLow/High`, `fiftyDayAverage`, `twoHundredDayAverage`, `allTimeHigh/Low`.
  - Taken whenever the fetch ran, possibly mid-session. For analysis-grade prices use `ohlcv.db`.
- **Size and shares** — `marketCap`, `enterpriseValue`, `sharesOutstanding`, `floatShares`, `impliedSharesOutstanding`.
- **Yahoo's valuation ratios** — `trailingPE`, `forwardPE`, `priceToBook`, `priceToSalesTrailing12Months`, `enterpriseToEbitda`, `enterpriseToRevenue`, `pegRatio`, `trailingPegRatio`, `trailingEps`, `forwardEps`, `bookValue`.
- **Yahoo's fundamentals (TTM)** — `totalRevenue`, `ebitda`, `freeCashflow`, `operatingCashflow`, `totalCash`, `totalDebt`, `grossMargins`, `operatingMargins`, `profitMargins`, `returnOnEquity`, `returnOnAssets`, `debtToEquity`, `currentRatio`, `quickRatio`, `revenueGrowth`, `earningsGrowth`.
- **Dividends** — `dividendRate`, `dividendYield`, `trailingAnnualDividendRate/Yield`, `payoutRatio`, `fiveYearAvgDividendYield`, `exDividendDate`, `lastDividendValue`.
- **Ownership and short interest** — `heldPercentInsiders`, `heldPercentInstitutions`, `sharesShort`, `shortRatio`, `shortPercentOfFloat`.
- **Analysts** — `targetMeanPrice`, `targetHighPrice`, `targetLowPrice`, `recommendationMean` (1 = strong buy … 5 = sell), `recommendationKey`, `numberOfAnalystOpinions`, `averageAnalystRating`.
- **Risk** — `beta`, ISS governance scores `auditRisk`, `boardRisk`, `compensationRisk`, `shareHolderRightsRisk`, `overallRisk` (1-10).
- **Funds** — `totalAssets`, `netAssets`, `navPrice`, `category`, `fundFamily`, `netExpenseRatio`, `annualReportExpenseRatio`, `ytdReturn`, `threeYearAverageReturn`, `fiveYearAverageReturn`, `beta3Year`, `morningStarOverallRating`, `morningStarRiskRating`, `yield`, `annualHoldingsTurnover`.

## Units — Yahoo is inconsistent, check before using

- **Fractions** (0.25 = 25%): `grossMargins`, `operatingMargins`, `profitMargins`, `returnOnEquity`, `returnOnAssets`, `payoutRatio`, `heldPercentInsiders`, `heldPercentInstitutions`, `revenueGrowth`, `earningsGrowth`, `trailingAnnualDividendYield`, `shortPercentOfFloat`.
- **Already percent** (2.22 = 2.22%): `dividendYield`, `fiveYearAvgDividendYield`, `debtToEquity` (84 = 0.84x).
- **Dates as Unix epoch seconds**: `exDividendDate`, `lastFiscalYearEnd`, `mostRecentQuarter`, `lastSplitDate`, `dividendDate`, `earningsTimestamp*`, `firstTradeDateEpochUtc`. `firstTradeDateMilliseconds` is in milliseconds.
- **Money** is in `currency` for price fields. Statement-based fields (`totalRevenue`, `ebitda`, …) are in `financialCurrency`, which differs for many foreign filers (CAD, EUR, CNY, JPY, …).

## How it is used

- `sector`, `industry`, `country` and `fund_family` are copied into `analysis.db`.
- `sharesOutstanding`, `beta`, `forwardEps`, `floatShares`, `marketCap`, `currency`, `financialCurrency` feed analysis calculations.
- Yahoo's ratios are used as a fallback (and a cross-check) when the ratios computed from statements aren't possible — see `analysis.md`, `valuation_basis`.

## Known limitations

- Yahoo field coverage varies by security type and by symbol. Absence of a column value is normal.
- Yahoo data quality issues exist: e.g. a few `beta` values are absurdly large (hundreds+), and `floatShares` can be in different share-class units than `sharesOutstanding` (e.g. BRK-B).
- No history: a ratio here is "as of `fetched_at`" only.
