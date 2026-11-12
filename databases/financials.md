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
- **Leftover periods are skipped** (since 2026-11-11): Yahoo lists its oldest year (and some odd quarters) with no revenue, net income or total assets — only a few minor lines. Those periods are not stored, so the EDGAR backfill can fill them (AAPL FY2021, MSFT FY2021-22 used to be empty).

## Source 2 — SEC EDGAR (deep history backfill)

- From the SEC "company facts" XBRL API (`data.sec.gov/api/xbrl/companyfacts`), keyed by the company's CIK.
- **Additive only**: an EDGAR period is written only if Yahoo doesn't already own that (period_end, freq). EDGAR never overwrites a Yahoo value.
  - **Exception — EDGAR-only columns** (`filed_date`, `non_interest_expense`) are also written onto Yahoo-owned periods, since Yahoo never supplies them.
- **Refresh:** re-fetched only to pick up a new filing — when a company's newest period has no `filed_date` yet, from `FINANCIALS_REPORT_LAG_DAYS` after the period end, at most every `EDGAR_REFRESH_LOCK_DAYS` (30), until `EDGAR_REFRESH_WINDOW_DAYS` (120) after it (since 2026-11-11; before, fetched once per company). Each fetch **replaces** that company's previous EDGAR rows. Last full fetch: 2026-11-11 (one-time re-fetch for the fix list).
- Only a curated subset of columns is filled, mapped from US-GAAP tags (several tags tried in order, to survive tag changes over the years):
  - Flows: `total_revenue` (broad `Revenues` tried first), `cost_of_revenue`, `gross_profit`, `operating_income`, `pretax_income`, `tax_provision`, `net_income`, `research_and_development`, `interest_expense`, `basic_eps`, `diluted_eps`, `diluted_average_shares`, `basic_average_shares`, `non_interest_expense` (banks), `operating_cash_flow`, `capital_expenditure`, `investing_cash_flow`, `financing_cash_flow`, `cash_dividends_paid`.
  - Balance sheet: `total_assets`, `current_assets`, `total_liabilities_net_minority_interest`, `current_liabilities`, `stockholders_equity`, `cash_and_cash_equivalents`, `retained_earnings`, `inventory`, `long_term_debt`, `ordinary_shares_number`, `share_issued`.
- **`freq`:** ~1 quarter = quarterly. Year-to-date (6/9-month) figures are dropped. ~1 year = annual in two tiers:
  - **Regular:** from an annual report (10-K / 20-F / 40-F), ending in the company's fiscal-year-end month.
  - **Fill-in:** from another non-quarterly filing in that month (6-K results, 8-K recast, proxy), or from an annual report at another month-end (a year-end change, e.g. Goldman Sachs Nov → Dec 2008). Kept only when ≥ 300 days from every accepted year.
  - Quarterly reports never supply a year — that keeps out trailing-12-month figures (Amazon) and misdated spans (JBTM).
  - A balance-sheet date is annual when it is a fiscal-year end.
- **History guard:** a re-fetch that would lose 2+ stored fiscal years keeps the company's old EDGAR rows and logs a warning.
- **Restatements:** money columns keep the latest filing's value. **Per-share and share-count columns keep the first-filed value**, so every value sits on the split basis of its own `filed_date` — multiply by the splits after `filed_date` to reach today's basis.
- **`filed_date`** — the earliest filing date of the facts in that row (when the numbers became public). Also on Yahoo rows for SEC filers.
- **`quarter_sum_flag`** (annual EDGAR rows) — 1 when the four quarters' revenue or net income differ from the annual figure by > 2%, 0 when they agree, NULL when four quarters aren't available.
- **Cleaning:** capex is always negative; facts dated after their own filing (debt maturities) are dropped; isolated one-off share-count spikes (far from both neighbours) are dropped as tagging noise; a revenue tag whose last year is > 3x off Yahoo's first year while assets moved < 1.5x is treated as a sub-line and left NULL.
- No `free_cash_flow`, `ebitda`, `total_debt` or `invested_capital` from EDGAR — derive FCF as `operating_cash_flow + capital_expenditure`.

## Conventions

- **Units**: money in the company's **reporting currency** (`quotes.financialCurrency`; usually USD, but not for many foreign filers). EPS in currency per share. Share counts in shares.
- **Signs follow Yahoo**: cash outflows are **negative** (`capital_expenditure`, `cash_dividends_paid`, `repurchase_of_capital_stock`). EDGAR values were flipped to match.
- **Quarterly flows are single quarters** (not year-to-date). TTM = sum of the last four consecutive quarters. Balance-sheet items are point-in-time.
- **Annual and quarterly can share a `period_end`** (the fiscal-year-end quarter). Always filter by `freq` before summing.
- **`stockholders_equity` is never 0** (since 2026-11-11, both sources): a 0 is a placeholder (partnerships report "partners' capital"; some new listings), so `common_stock_equity` is stored instead when it is non-zero (negative included), else NULL.
- **Per-share values and share counts: split basis differs by source.**
  - **EDGAR rows:** first-filed, so each sits on the basis of its own `filed_date`. Multiply by the `ohlcv.splits` after `filed_date` for today's basis (NVDA FY2019 0.625B × 4 × 10 = 25.0B).
  - **Yahoo rows:** already restated to today's basis (NVDA FY2023 ~25.1B) — do not adjust again.
  - A series crossing the EDGAR → Yahoo seam therefore jumps by the later splits until it is adjusted.

## Known limitations

- **Filing date only for SEC filers.** `filed_date` is NULL for companies with no SEC XBRL filings (many ADRs). For those, assume a figure became public ~45 days after `period_end`.
- Quarterly coverage can have gaps (a missing quarter breaks a TTM sum). Companies rarely tag a separate Q4 figure in the 10-K, so EDGAR's fiscal-Q4 quarter is often missing.
- **Company record changes.** The SEC can re-point a ticker at a new record (holding-company reorganisation) with little history. SUPN, NAVI, JSM, XPRO and CLBK keep their pre-2026-11-11 EDGAR rows (restored from backup: no `filed_date`, latest-filed share counts) and have no 2021 year.
- Very old years that a small company tagged only inside a 10-Q are not stored (~110 years across ~60 micro-caps).
- Older EDGAR years can lack revenue where a company used a tag outside the list (e.g. `SalesRevenueGoodsNet` before 2018, bank-specific revenue tags).
- Revenue definitions differ slightly between the sources (excise taxes, bank netting), so a 2-20% step can appear at the EDGAR → Yahoo seam (Altria, Molson Coors ~18%).
- Share counts are per company, not per share class: GOOG/GOOGL, BRK-A/BRK-B etc. carry the same count. ADRs may report home-market shares, not ADR units.
- Yahoo can shift a `period_end` by a few days between fetches, which can leave two near-duplicate periods.
