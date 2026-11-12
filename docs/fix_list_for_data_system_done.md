# Fix list — what was done

Response to `docs/fix_list_for_data_system.md`, item by item, using its section numbers. It records what was fixed and how, the review's own check results before and after, what was skipped or deferred and why, and where the data showed something different from the review. It grows as each group of items is worked through.

**Status words:**
- **Fixed** — the item is done in full.
- **Partly fixed** — some bullets of the item are done; the rest are listed as open.
- **Deferred** — accepted or under study, to be done in a later group.
- **Skipped** — not done, with the reason.

**How the work is grouped** (order follows dependencies):
- **A** import cleaning rules · **B** EDGAR backfill · **C** price history · **D** symbol state and identity · **E** signals consistency · **F** analysis foundations · **G** analysis built on F · **H** new calculations · **I** indices.

## Round 1 — Group A: cleaning rules at import (2026-11-11)

**How it works, for every item in this round:**
- **One rule module:** `data_layer/cleaning_rules.py`. Pure functions on a DataFrame (`clean_quotes`, `clean_estimates`, `clean_financials`).
- **At import:** the same functions run inside the Yahoo sanitize steps (quotes, estimates, financials) and the EDGAR extract step. Every new fetch is cleaned.
- **Stored rows:** a one-time script, `scripts/clean_stored_data.py`, ran the same functions over the stored tables and wrote back only the changed cells. A second run changes 0 rows.
- **Safety:** before writing, the script copied the three databases it changes to a dedicated backup folder.
- **`analysis.db` not yet rebuilt.** It picks up the changes on the next full analysis run. The `analysis.db` checks below are pending that run.

### §5 "Missing" stored as 0 — Fixed, narrower than proposed

- **Rule (`estimates`):** when a horizon's analyst count is 0 or missing, a value of exactly 0 in that group's `_avg`, `_low`, `_high`, `_year_ago`, `_growth` becomes NULL. Revenue and EPS groups each use their own count (`rev_num_analysts`, `eps_num_analysts`).
- **Rule (`quotes`):** `recommendationMean` = 0 becomes NULL.
- **Check (the review's SQL):**
  - `estimates` with `rev_avg = 0` and no analysts: **12,934 rows / 3,688 symbols → 0**.
  - `quotes` with `recommendationMean = 0`: **14 → 0**.
- **Where we differ from the review:**
  - **Only zeros are cleared, not every value.** The review proposed NULL for all `rev_*` / `eps_*` when the count is 0. Two rows carry a real non-zero `_year_ago` with a count of 0; that is reported history, so it was kept.
  - **0 with analysts is left alone.** A further **1,346 rows (458 symbols)** show revenue 0 *with* analysts — e.g. AARD (Aardvark Therapeutics), 9 analysts. These are pre-revenue companies where $0 is a real forecast.
  - **Found more:** two EPS rows (PAM `+1q`, CRD-A `+1q`) had `eps_low` or `eps_high` = 0 beside a non-zero average with 0 analysts. Cleared by the same rule (2 cells).

### §7 Stale or inconsistent snapshot fields — Partly fixed

- **Fixed — absurd Yahoo values, rule (`quotes`):** `|beta|` > 10, `payoutRatio` > 10 (a fraction, 1,000%), `grossMargins` > 1 → NULL.
- **Check:** `|beta| > 10`: **144 → 0**; `payoutRatio > 10`: **49 → 0**; `grossMargins > 1`: **18 → 0**.
- **Open (Group E):** past `next_earnings_date`, TTM a quarter behind after earnings season, and the YPF currency label.

### §24 Stockholders' equity stored as 0 — Fixed, wider than proposed

- **Rule (`financials`, Yahoo and EDGAR rows):** `stockholders_equity` = 0 → `common_stock_equity` when that is non-zero, else NULL.
- **Check:**
  - Zero equity with positive common equity: **236 annual (77 symbols) + 228 quarterly (53) → 0**.
  - All rows with `stockholders_equity = 0`: **1,458 → 0** — 554 filled from common equity, 904 set to NULL.
  - BEP annual equity now 4.86B / 4.72B / 4.24B / 4.60B (FY2022-2025), equal to its common equity.
  - Pending the analysis run: `roe`, `pb`, `debt_to_equity` for WES, BEP, BIP, XIFR, DLNG.
- **Where we differ from the review:**
  - **EDGAR rows too.** Of the 1,458 zero rows, 872 come from EDGAR, not only Yahoo. The rule runs in both import paths.
  - **Negative common equity counts.** 90 rows had 0 equity beside a *negative* common equity. Real equity is negative there, so that value is taken. The review's rule covered only common equity > 0.

### §25 Company identity and industry tags — Partly fixed (names only)

- **Fixed — rule (`quotes`):** an empty `longName` is filled from `shortName`.
- **Check:** live stocks/ADRs with no `longName` but a `shortName`: **134 → 0** (452 rows across all security types). CubeSmart now has `longName` "CubeSmart".
- **Note:** FAMarket's own screens take the name from `symbols.name`, not `longName`, so this helps readers of `quotes.db` directly.
- **Open (Group D):** CIK for every company, `company_id`, SEC SIC code, industry overrides.

### §20 Smaller data items — EBITDA bullet: Deferred (the example does not hold)

- **The review said:** Yahoo's EBITDA is below its own parts — Welltower 2.31B vs net income + D&A + interest = 3.73B. Check EBITDA ≥ operating income + D&A, and recompute from the parts when it fails.
- **What the data shows:**
  - **Yahoo's EBITDA equals its own EBIT + D&A in 98.6% of annual rows** (25,141 of 25,504 within 1%), with EBIT = pre-tax income + interest.
  - **Welltower FY2025 is consistent:** EBIT 0.17B + D&A 2.14B = 2.31B. The 3.73B sum used net income (0.94B), but pre-tax income was **−0.48B**. Net income sat well above pre-tax income because of an item below the pre-tax line, so adding net income overstates EBITDA.
- **The real question is a definition, not an error:**
  - Reported EBITDA includes one-off charges such as write-downs.
  - Yahoo's `normalized_ebitda` excludes them.
  - 2,351 annual rows (1,586 companies) have reported EBITDA below 80% of normalized.
  - Which one `debt_to_ebitda` / `ev_ebitda` should use is an analysis choice. It is deferred to Groups F/G, not handled as a cleaning rule.
- **Other §20 bullets** — open in Groups C (gaps, stale runs) and E (surprise near zero, currency mismatch, insider holdings, analyst targets); `enterpriseValue` in Group F.

**Group A follow-up (after the 2026-11-11 analysis run):** `roe` / `pb` / `debt_to_equity` were NULL for all five examples and are now filled — BEP −2.5% / 2.15 / 8.43, BIP 12.6% / 3.50 / 12.09, DLNG 13.6% / 0.22 / 0.55, WES 30.0% / 3.87 / 2.14, XIFR 1.4% / 0.06 / 0.56.

## Round 2 — Group B: EDGAR backfill rework (2026-11-11)

**How it works, for every item in this round:**
- **Extraction rules** in `data_layer/fetchers/edgar_fetcher.py` (`extract_financials` and its helpers); the Yahoo side in `yfinance_fetcher.sanitize_financials` + `cleaning_rules.drop_empty_periods`.
- **Replace, not add:** each EDGAR fetch deletes that company's own `source='edgar'` rows before writing, so rows the old rules produced disappear.
- **One full re-fetch** of SEC company facts for 8,389 companies (`scripts/refetch_edgar.py`, 111 min, 0 failures), after a one-off backup of `financials.db` + `symbols.db`. A repair pass followed (see "Found during the re-fetch").
- **Before → after** below compares the backup taken just before the re-fetch with the databases after the repair pass. The full analysis run followed.

### §1 Empty Yahoo periods block the EDGAR backfill — Fixed

- **Rule:** a Yahoo period with no `total_revenue`, `net_income` and `total_assets` is not stored (`drop_empty_periods`, at import). The stored ones were deleted once.
- **Check:** Yahoo rows with all three NULL: **7,215 annual + 15,604 quarterly → 0**. AAPL FY2021 now 365.8B revenue; MSFT FY2021 168.1B and FY2022 198.3B (all from EDGAR).
- **Where we differ / found more:**
  - **They are not empty, and not random.** They hold a few minor lines (prepaid assets, debt issuance), and 6,824 of the 7,278 annual ones were **Yahoo's oldest year** — a partial leftover at the edge of its window.
  - **A trap in the proposed fix.** Filling them from EDGAR alone does not last: the next weekly Yahoo fetch upserts the same period back, and EDGAR does not re-run. So the Yahoo import itself had to stop writing them.

### §4 Bad EDGAR backfill rows — Fixed

- **Duplicate / misdated years (JBTM):** fixed by the fiscal-year rule under §12. JBTM's 2012-03-31 and 2013-03-31 rows are gone; its years are Dec only.
  - **Cause, not in the review:** JBTM's own FY2013 **10-K** tagged FY values with wrong dates (2011-04-01 → 2012-03-31). Filtering by form alone does not catch it.
- **Future dates:** facts dated after their own filing date are dropped. **72 → 48 rows**; all 48 are on preferred-share tickers outside the re-fetch scope (see "Found during the re-fetch").
  - **Cause:** 84 of the original 90 were `long_term_debt` amounts tagged at their maturity dates (PMT 2026-2030), not reporting periods.
- **Positive capex:** stored always negative (`-|value|`). **533 → 10 rows**; the 10 are on preferred-share tickers, as above.
  - **Cause:** 227 of 243 companies were negative in every other period — a filer sign slip, not a real inflow.
- **Revenue as a sub-line:** see §13.
- **Quarter-sum cross-check (the review's fix bullet):** new column `quarter_sum_flag` on EDGAR annual rows. 1 = the four quarters' revenue or net income differ from the year by > 2%, 0 = they agree, NULL = four quarters not available.
  - **Result:** 18,229 agree, **2,111 flagged**, 40,596 not checkable — EDGAR rarely has a separate fiscal-Q4 quarter (it is rarely tagged).

### §12 Quarter-ends stored as "annual" — Fixed

- **Rule:** fiscal-year-end months = the month of the latest annual-length end in each annual-report filing. A ~1-year figure is a fiscal year in two tiers:
  1. **Regular:** from an annual report (10-K / 20-F / 40-F), ending in a fiscal-year-end month.
  2. **Fill-in:** from another non-quarterly filing in that month (6-K results, 8-K recast, proxy table), or from an annual report at another month-end (a year-end change). Kept only when ≥ 300 days from every accepted year.
  - Quarterly reports (10-Q) never supply a year. A balance-sheet date is annual only on an accepted year-end.
- **Check:**
  - Annual rows < 300 days apart: **9,994 (2,390 companies) → 1,726 (504)**. Counting only rows with revenue or net income: **788 (324) → 408 (205)**. The rest are mostly balance-sheet-only rows at a year-end change (Goldman Dec 2008) and real year-end changes.
  - AMZN annual rows: December only.
  - Real fiscal years with revenue or net income: **80,777 → 85,309**.
- **Where we differ:**
  - **Cause found:** Amazon's 10-Qs report **trailing-12-month** cash flows (`10-Q`, Q1, 2022-04-01 → 2023-03-31). Any ~365-day fact was read as a fiscal year, and its date then turned that quarter's balance sheet into "annual" too.
  - **The `frame` field was tested and rejected.** It fixes JBTM but not Amazon: some of Amazon's TTM facts carry a calendar-year frame.
  - **`fp` / `form` used for flows only.** The same balance-sheet date appears in a 10-Q comparative and a 10-K, so form is unreliable for instants. Instants are classified by the accepted year-ends instead.
  - **The review's `form = 10-K` rule was too strict.** Tried first, it dropped real years: Canadian National (files results as **6-K**), Disney 2016 and P&G 2007 (only in later **8-K recasts**), BlackRock 2021 (proxy table), Goldman Sachs / Morgan Stanley's November years (year-end change 2008-09). Hence the two-tier rule above.

### §13 Revenue from a sub-line tag — Fixed

- **Rule:** the broad `Revenues` tag is tried first, then the contract-revenue tags (`RevenueFromContractWithCustomer…`), then `SalesRevenueNet`.
- **Seam test (the review's step 3):** if EDGAR's last year before Yahoo's first is > 3x off in revenue while total assets are within 1.5x, that tag's revenue is treated as a sub-line and left NULL. **261 companies** in the full re-fetch, among them RF (Regions, a review example).
- **Check — 57 companies** (banks, insurers, REITs, utilities, tobacco, energy, tech), 220 years where SEC and Yahoo overlap, match = within 2%: **149/220 → 185/220**. A "largest of the broad tags" variant also gave 185, so the simpler order was kept.
  - Fixed: AAT FY2018 40.0M → 330.9M, MET 1.26B → 67.9B, AMT 0.49B → 7.44B, plus COF, ESS, EXR, MGRC, KEY.
- **Cause confirmed:** the contract-revenue tag (ASC 606) excludes lease income, insurance premiums and interest.
- **Left open — definition gaps, not sub-lines (2-20%):** tobacco and brewers (MO, TAP: SEC gross of excise tax, Yahoo net, ~18%), CVX ~5%, banks JPM/KEY/PNC 3-5%, NEE, DE, BRK-B. A 3x seam test does not touch these, by design.
- **Skipped — bank composite revenue** (interest + non-interest income when no `Revenues` tag): not needed in the sample. Possible follow-up: older years of KO, JNJ, WFC lack revenue because they used `SalesRevenueGoodsNet` or bank-specific tags outside the list.

### §14 Missing and mixed-basis share counts — Partly fixed (EDGAR side)

- **New EDGAR columns:** `diluted_average_shares` (← `WeightedAverageNumberOfDilutedSharesOutstanding`), `basic_average_shares` (← `…NumberOfSharesOutstandingBasic`).
- **Check:** EDGAR annual rows with diluted average shares, 2008-2020: **1-2% → 49-69%** per year (2021: 79%).
- **Found more — keep the *first-filed* value for per-share and share-count columns.**
  - "Latest filing wins" mixed split bases inside one series: NVDA FY2020 0.618B as first filed, 2.472B after a later 10-K restated it for the 2021 4:1 split.
  - First-filed values all sit on the basis of their own filing date, so value × splits after `filed_date` = today's basis, exactly: NVDA FY2019 0.625B × 4 × 10 = 25.0B; AAPL FY2019 4.649B × 4 = 18.6B.
  - Money columns keep the latest filing (restatements).
- **Deferred to Group F:** the review's steps 2-4 — switching the analysis split adjustment from "splits after `period_end`" to "splits after `filed_date`", and handling Yahoo rows (already on today's basis). Talen Energy's near-zero share counts go with it.

### §22 No filing date — Fixed (filing-date part)

- **New column `filed_date`:** the earliest SEC filing date of the facts in a row, i.e. when the numbers became public. Also written onto Yahoo-owned periods for SEC filers.
- **Check:** filled on **91.5%** of EDGAR rows and **63.4%** of Yahoo rows (companies with no XBRL filings, many ADRs, have none).
- **Kept current:** EDGAR now re-fetches a company when its newest period has no `filed_date` yet (see "Ongoing behaviour").
- **Open (Group D):** the delisted-history bullet of §22.

### §23 Bank efficiency ratio — Fixed

- **New EDGAR column `non_interest_expense`** (← `NoninterestExpense`), also written onto Yahoo-owned periods. **26,887 rows, 497 companies.** JPM FY2025 95.64B (Yahoo's pieces summed to 79.55B).
- **New metric `efficiency_ratio`** in `analysis.db` = non-interest expense ÷ revenue × 100, banks only, lower is better. Filterable, with a hint entry.
  - Uses the newest **same-period** pair: TTM over four consecutive quarters that carry both, or the latest fiscal year that does. Blank if that pair is > 15 months older than the newest period.
- **Check (after the analysis run):** **383 of 618 banks** have a value; median 61.9%, 10th-90th percentile 37-84%. JPM 52.6, BAC 61.7, WFC 65.5, USB 59.0, PNC 59.9, KEY 64.6, RF 57.3, C 64.7.
  - **Outliers are real inputs, not errors:** SFNC 602% (FY2025 revenue 94M after a securities-loss repositioning), MGNO (a new bank with ~0 revenue), SNEX / IBKR (brokers with gross trading revenue). Percentile scoring ranks them last.
  - **No value for 235 banks:** 102 have no SEC data (foreign banks / ADRs), 104 file without the `NoninterestExpense` tag, 29 have only stale or unmatched periods. Possible follow-up: fallback tags for the 104.

### Found during the re-fetch (not in the review)

- **The SEC can re-point a ticker at a different company record.** After a holding-company reorganisation, NAVI, JSM, XPRO and CLBK map to new records with almost no history, and SUPN maps to Indivior. The replace-on-fetch wiped their history.
  - **Fix:** their pre-re-fetch EDGAR rows were restored from the backup (no `filed_date`, old share-count basis, no 2021 year). A **history guard** now keeps a company's stored rows (and logs a warning) whenever a fetch would lose more than one stored fiscal year.
- **Lost years after the repair:** 64 companies / 114 years, almost all micro-caps whose very old years existed only inside 10-Q filings. Accepted as unreliable. NBIS (formerly Yandex) lost revenue for 2012-2015 by the seam test — a different business, reported in roubles.
- **Preferred-share tickers still carry old EDGAR rows** (PMT-PA, PCG-PA…PX, DSX-PB): the 48 future dates and 10 positive capex left above. They are outside the EDGAR fetch scope, so the re-fetch never replaced them → Group D (symbol scope).

### Ongoing behaviour (so the fixes last)

- **Yahoo import:** cleaning rules and the empty-period skip run on every fetch.
- **EDGAR refresh:** previously fetched once per company. It now re-fetches a company only to pick up a new filing — when its newest period has no `filed_date` yet, from 45 days after that period's end, at most every 30 days, for up to 120 days. Load comes in waves: about an extra hour on the first weekly run ~45 days after each calendar quarter-end.
- **Sector indices unchanged by the new share counts** (SPDR-style weights use them): since 2008-12-31, Financials 7.8x vs XLF 7.7x, Industrials 10.2x vs XLI 9.7x, Healthcare 8.5x vs XLV 9.4x, Technology 31.5x vs XLK 27.0x. Daily correlation 0.92-0.99 — the same as before the re-fetch.

### Recorded, no fix proposed by the review

- **Gross profit ≠ revenue − cost of revenue** and **~250 unit jumps** were listed as observations without a fix. Not acted on; candidates for a later data-quality-flags item.

## Round 3 — Group C: price history (2026-11-12)

**How it works, for both items in this round:**
- **Repair only when broken** — `YFinanceOHLCV` (`data_layer/fetchers/yfinance_fetcher.py`) re-fetches a symbol with yfinance's own `history(repair=True)` only when its `adj_close` comes back impossible.
- **Flags** — `data_layer/price_checks.py` checks every fetched series and writes a new `price_flags` table in `ohlcv.db` (symbol, check, start_date, end_date, detail) — the review's "separate `price_flags` table" option. Rewritten per symbol on each fetch.
- **Analysis** — `analysis_layer/price_quality.py` applies the flags to each symbol's 2-year price window before any price-based number (valuation price, technicals, RS), and a new screenable column `analysis.price_issues` lists the checks that touched the window.
- **Stored data** — one-time `scripts/fix_stored_prices.py`: flagged every stored symbol and re-fetched the broken ones (their old rows exported to a small backup first).

### §3 Price history defects — Fixed (flag + work around), one case left open

- **Check (the review's SQL), full history:**
  - `adj_close` ≤ 0 or > close: **152 → 79 symbols**. In the 2-year analysis window, analysis universe: **81 → 22**, and those 22 use the plain close in the analysis.
  - Dividend and split on one day: **390 rows** — kept as real (see below); **9 double-booked → 4** after repair, all flagged `double_event`.
  - One-day moves ≥ 10x: flagged as `bad_print` (413 symbols) or `level_shift` (780 symbols).
- **Named cases:** TDS 2005-05-16 → `double_event` (adjustment factor jumps 40.8x); CHRD 2020-11-20 → `level_shift` (x258); QH 2026-04-27 → `level_shift` (x25); PLTYF, PSBAF broken `adj_close` → PSBAF repaired, PLTYF flagged `bad_adj` (repair moved its dividend dates and was rejected).
- **Rule per check, as built:**
  - **`bad_adj`** — repair first; if it can't be fixed, the analysis uses `close` instead of `adj_close` for that symbol (a price return, but a real one).
  - **`bad_print`** — a ≥ 10x move whose price comes back within 2x inside 5 bars: dropped from the analysis window.
  - **`level_shift`** — a ≥ 10x move that stays: **flagged only** (see below).
  - **`double_event`** — flagged only; it lies outside the 2-year window in every case found.
- **Result after the analysis run:** no zero or negative `price`; 109 symbols with a `bad_print` and 22 with `bad_adj` in their window now get technicals and RS on corrected data (PTPIF: no RS rank before, 48 now).
- **Where we differ from the review / found more:**
  - **Cause of most broken `adj_close`:** a foreign-currency dividend (yen) applied to a USD price on OTC lines of Japanese companies (JPSTF, SMTOF, DQJCF, OBIIF…) — the adjustment overshoots below zero. 8 of the 9 "double-booked" events are the same issue.
  - **Dividend + split on one day is usually real** — spin-offs (Danaher 2016, Expedia 2011). Measured by the jump in the adjustment factor that day, only 9 of 390 were double-booked. So the review's check "no ex-date carries both" would flag ~380 real events; we flag only factor jumps > 2x.
  - **yfinance's repair must not run on healthy symbols.** Run on everything, it **deleted real dividends** (CRH's $0.98 March 2022 final, QQQ's $0.089 June 2010, DNP's monthly $0.065) and spent extra requests on funds and preferreds for no change. So it runs only when `adj_close` is broken, and its result is kept only if it has fewer broken rows (none in the analysis window), dividends on the same dates (amounts may be corrected: a 100x unit mix-up on BKZHF, a dividend on a 3:1 split day on SGIPF) and no fewer bars. Of 141 broken symbols, 76 were repaired; rejections: dividend dates changed 35 (mostly mutual funds), still broken 29, fewer bars 1.
  - **A lasting 10x move is not always a data break.** On penny stocks it is as often a real collapse (ABOS $2.29 → $0.21) as an unrecorded reverse split (QH). Cutting the history at the shift would hide a real crash from momentum and 52-week numbers, so `level_shift` is flagged only; screens exclude it with `price_issues` "is null".
  - **Multi-day bad prints.** Many 10x spikes last several days before snapping back (FDCT Dec 2024), so the reversal is looked for within 5 bars, not 1.
  - **Rate limit:** repair makes 1-4 extra Yahoo requests per broken symbol. Every request after the first inside a fetch is metered and waits for a unit of the same rate budget (`BaseFetcher._tick`, `_RequestMeter`); verified at a test limit of 20/min (the limiter paused exactly at 20 per minute).
- **Open:** CHRD-style old + new company stitching is flagged but not split into two histories; a reverse split Yahoo never recorded (QH) is flagged but not corrected.

### §20 Gaps and stale runs — Fixed

- **`stale`** — 10 or more bars in a row with an unchanged close and zero volume (nothing traded). Series with zero volume on ≥ 95% of bars are skipped: those are mutual funds' NAV series (~22,700 symbols), where a flat price is real.
  - **Count:** 2,681 symbols over the full history; 1,059 analysis symbols with a stale run in their window.
  - **Analysis:** stale bars are dropped from the window, so volatility, RSI and RS see only real trading days (AAGH: 150 carried days dropped, RS rank 91 → 24). Trade-off: for such a stock the 252-bar "52-week" window then spans more than a calendar year.
  - **Where we differ:** the review counted runs of 30+; we flag from 10 (setting `OHLCV_STALE_RUN_DAYS`) — at 30 only 440 window symbols are caught, at 10 996.
- **`gap`** — more than 31 days between two bars: **1,006 symbols** (mostly mutual funds), flagged only. 336 analysis symbols have one in their window.

## Not yet reviewed

§2, §6, §8-§11, §15-§19, §21, and the open parts of §7, §14 (steps 2-4), §20 (signals, currency, targets, EV), §22 (delisted history), §25. Grouped as above.

## Known gaps / limitations

- **No fiscal-Q4 quarter from EDGAR for many companies** (e.g. Agilent): filers rarely tag a separate 3-month Q4, so EDGAR's Q4 row is often missing, TTM sums break there, and `quarter_sum_flag` stays NULL. Possible follow-up: derive Q4 = fiscal year − Q1 − Q2 − Q3.
- Round 1 counts are from the 2026-11-08 databases (prices as of 2026-11-06); round 2 counts from the 2026-11-11 re-fetch and analysis run. All checked on 2026-11-11.
- The cleaning thresholds (beta 10, payout 1,000%, gross margin 100%) come from the review and were not tuned further.
