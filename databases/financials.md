# financials.db — income statement, balance sheet and cash flow history

Reported financial statements for operating companies, annual and quarterly, merged into one wide table.
Recent periods come from Yahoo Finance; older history (often back to ~2008, sometimes much earlier) is filled in from SEC EDGAR XBRL filings.

Join key: `symbol` (see `symbols.md` for shared conventions). Snapshot when written (2026-11-09): ~370k rows, ~8.3k symbols, 357 columns.

## Table `financials` — one row per (symbol, period_end, freq)

- **`symbol`**, **`period_end`**, **`freq`** (unique key).
  - **`period_end`** — fiscal period end, snapped to a calendar month-end (e.g. Apple's 52/53-week years all land on `YYYY-09-30`).
  - **`freq`** — `annual` (fiscal year) or `quarterly` (one fiscal quarter).
- **`source`** — `yfinance` or `edgar` (which source wrote this row).
- **`is_complete`** — 1 when both `total_revenue` and `net_income` are present.
- Every other column is one statement line item, named after Yahoo's line-item label in lower case with underscores (e.g. "Total Revenue" → `total_revenue`). Most columns are sparse.

Only `stock`, `adr` and REIT-type stocks have rows. No funds, ETFs or preferreds.

## Source 1 — Yahoo Finance (recent window)

- Yahoo Finance's income statement, balance sheet and cash-flow statement, annual and quarterly.
- Yahoo's free data covers only ~4-5 fiscal years and ~5-7 quarters, so this source only reaches a few years back.
- All ~350 Yahoo line items are kept. Key ones: `total_revenue`, `cost_of_revenue`, `gross_profit`, `operating_income`, `ebit`, `ebitda`, `pretax_income`, `tax_provision`, `net_income`, `basic_eps`, `diluted_eps`, `diluted_average_shares`, `total_assets`, `current_assets`, `total_liabilities_net_minority_interest`, `current_liabilities`, `stockholders_equity`, `total_debt`, `net_debt`, `cash_and_cash_equivalents`, `invested_capital`, `working_capital`, `retained_earnings`, `ordinary_shares_number`, `share_issued`, `operating_cash_flow`, `capital_expenditure`, `free_cash_flow`, `cash_dividends_paid`, `repurchase_of_capital_stock`, `stock_based_compensation`.
- New fetches update existing periods and add new ones, so periods that age out of Yahoo's window stay stored.
- **Quirk: some Yahoo rows are completely empty** (every value NULL). Yahoo lists a period it has no numbers for. Filter on the columns you need, not on row existence.

## Source 2 — SEC EDGAR (deep history backfill)

- From the SEC "company facts" XBRL API (`data.sec.gov/api/xbrl/companyfacts`), keyed by the company's CIK.
- **Additive only**: an EDGAR period is written only if Yahoo doesn't already own that (period_end, freq). EDGAR never overwrites Yahoo.
- Fetched once per company (history doesn't change), so EDGAR rows are a one-time snapshot.
- Only a curated subset of columns is filled, mapped from US-GAAP tags (several tags tried in order, to survive tag changes over the years):
  - Flows: `total_revenue`, `cost_of_revenue`, `gross_profit`, `operating_income`, `pretax_income`, `tax_provision`, `net_income`, `research_and_development`, `interest_expense`, `basic_eps`, `diluted_eps`, `operating_cash_flow`, `capital_expenditure`, `investing_cash_flow`, `financing_cash_flow`, `cash_dividends_paid`.
  - Balance sheet: `total_assets`, `current_assets`, `total_liabilities_net_minority_interest`, `current_liabilities`, `stockholders_equity`, `cash_and_cash_equivalents`, `retained_earnings`, `inventory`, `long_term_debt`, `ordinary_shares_number`, `share_issued`.
- `freq` is decided by the reported duration: ~1 year = annual, ~1 quarter = quarterly. Year-to-date (6/9-month) figures are dropped.
- A restated figure keeps the latest filing's value.
- Isolated one-off share-count spikes (far from both neighbours) are dropped as tagging noise.
- No `free_cash_flow`, `ebitda`, `total_debt` or `invested_capital` from EDGAR — derive FCF as `operating_cash_flow + capital_expenditure`.

## Conventions

- **Units**: money in the company's **reporting currency** (`quotes.financialCurrency`; usually USD, but not for many foreign filers). EPS in currency per share. Share counts in shares.
- **Signs follow Yahoo**: cash outflows are **negative** (`capital_expenditure`, `cash_dividends_paid`, `repurchase_of_capital_stock`). EDGAR values were flipped to match.
- **Quarterly flows are single quarters** (not year-to-date). TTM = sum of the last four consecutive quarters. Balance-sheet items are point-in-time.
- **Annual and quarterly can share a `period_end`** (the fiscal-year-end quarter). Always filter by `freq` before summing.
- **`stockholders_equity` is never 0** (since 2026-11-11, both sources): a 0 is a placeholder (partnerships report "partners' capital"; some new listings), so `common_stock_equity` is stored instead when it is non-zero (negative included), else NULL.
- **Per-share values and share counts are as filed** — not adjusted for later splits. EPS or share series crossing a split jump (AAPL 2014 7:1, 2020 4:1). Use `ohlcv.splits` to adjust. A later filing may already have restated older values, so a series can mix bases.

## Known limitations

- **No filing date.** Only `period_end` is stored. For point-in-time work, assume a figure became public ~45 days after `period_end` (10-Q deadline; 10-K is later).
- Quarterly coverage can have gaps (a missing quarter breaks a TTM sum).
- A few EDGAR rows have implausible `period_end` dates (years in the future) from mis-tagged filings.
- Share counts are per company, not per share class: GOOG/GOOGL, BRK-A/BRK-B etc. carry the same count. ADRs may report home-market shares, not ADR units.
- Yahoo can shift a `period_end` by a few days between fetches, which can leave two near-duplicate periods.
