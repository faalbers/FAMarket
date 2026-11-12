# macro.db — US macro-economic time series (FRED)

Four long US macro series from the Federal Reserve Bank of St. Louis (FRED). Market-wide context, not per symbol.

## How it is retrieved

- Source: the FRED API — the full available history of each series, refreshed on every fetch run.
- Each refresh overwrites existing (series, date) values, so revised values replace older ones. Missing observations are skipped, not stored as NULL.

## Table `macro` — one row per (series, date), long/tidy format

- **`series`** — short readable name for the series.
- **`fred_id`** — the FRED series code.
- **`date`** — observation date (FRED's date for the period; monthly/quarterly series use the period's first day).
- **`value`** — the observation, in FRED's units.

| series | fred_id | What it is | Frequency | Units |
|---|---|---|---|---|
| `treasury_10y` | DGS10 | 10-year US Treasury constant-maturity yield | daily (business days), from 1962 | percent (4.84 = 4.84%) |
| `fed_funds_rate` | FEDFUNDS | Effective federal funds rate, monthly average | monthly, from 1954 | percent |
| `cpi` | CPIAUCSL | CPI for all urban consumers, seasonally adjusted | monthly, from 1947 | index level (1982-84 = 100) |
| `gdp_growth` | A191RL1Q225SBEA | Real GDP growth, quarter over quarter, annualized, seasonally adjusted | quarterly, from 1947 | percent |

## How it is used

- The latest `treasury_10y` value is the **risk-free rate** for discount rates (CAPM / WACC / DCF) in `analysis.db`. The other series are context only.

## Known limitations

- CPI is a level, not inflation — compute year-over-year change yourself (12-month ratio).
- Macro data is released with a lag and revised later; values here are the latest vintage, not as first published.
