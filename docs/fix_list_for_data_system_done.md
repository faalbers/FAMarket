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

## Round 4 — Group D: symbol state and identity (2026-11-12)

**How it works:** all rules live in `data_layer/symbols.py` and run on every fetch; one-time scripts brought the stored data in line (`scripts/fix_symbol_state.py`, `scripts/recover_stuck_symbols.py`), each after a one-off backup of the databases it changes. Results below are after the 2026-11-12 analysis run.

### §15 Dead securities stay active — Fixed, differently from proposed

- **Rule (`mark_delisted`, after every fetch):** a price-history symbol is `is_delisted = 1` (with `delisted_date` = its last bar) when that bar is more than **max(5, 3 × its own mean gap between bars over the last year)** trading sessions older than the session **it was last fetched in**. Clears itself when bars return. `is_active` is not touched. The analysis universe skips `is_delisted = 1`.
- **Check:** the review's SQL counted 3,090 funds, 633 warrants, 338 units, 161 SPACs, 116 stocks, 68 ETFs, 14 preferreds, 3 ADRs, 2 CEFs. After the fix: **3,125** price-history symbols left the analysis (2,928 funds, 106 stocks, 67 ETFs, 17 preferreds, 5 ADRs, 2 CEFs); `analysis.db` 38,564 → 35,587 rows, with 0 delisted symbols in it.
- **Where we differ / found more:**
  - **Root cause:** `reassess_state` only judged symbols fetched that run, and the price-staleness gate stops fetching a symbol whose prices stopped — so it was never judged again.
  - **Judged against each symbol's own fetch date, not the benchmark's latest day.** Otherwise a partial run marks every un-fetched symbol dead.
  - **Per-symbol threshold:** ~157 live symbols (mostly funds) price weekly or monthly; a flat 5 days would mark them dead between prints.
  - **Warrants, units and SPACs are not judged.** The fetcher never takes their price history, so the review's test counts all of them as dead (632 / 338 / 130). Yahoo's quote time is no substitute: it is stamped with the latest close even when nothing traded (PAL: last bar 2026-10-02, quote time 2026-11-06).
  - **Yahoo data outages.** Yahoo drops weeks of bars for many symbols at once — 150 symbols missing 2026-10-02..11-06, 47 in June, 42 in January. About 0.9% of live symbols had such a hole in the past year (mostly funds, typically 5-6 weeks). Real deaths cluster too (193 funds closed on 2025-09-23), so an outage cannot be told apart and the 5-session rule was kept; instead:
    - a run-log warning when 25+ symbols newly stop on the same date;
    - **a monthly re-check** replacing the permanent give-up: a symbol skipped as stale or no-data is fetched once every 30 days while its newest bar is under 6 months old, and a switched-off symbol is switched back on for one try on the same cadence for 6 months after `deactivated_date`.
  - **One-time recovery:** the old permanent give-ups had lost live symbols (a 5-week pause between runs before the 2026-11-08 staleness fix; Yahoo outages). Re-fetching all 5,871 given-up symbols brought **342** back with newer prices (319 traded in the last week): new listings Yahoo did not know at first, and stocks stuck behind the July 17 outage (Allurion, Mobile Global Esports). The 1,906 still without any data stay off, with no retries.

### §6 Mistyped securities — Fixed (one rule rejected)

- **Rules (`resolve_types_from_quotes`, after every quote fetch — Polygon discovery re-writes its own type each run):**
  - `mutual_fund` that Yahoo calls EQUITY → `adr` if the name says ADR, else `stock`.
  - `stock` with no Yahoo market cap whose name reads as a preferred or note (Preferred, Depositary Shares, Notes due, Senior / Subordinated Notes, Debentures, Pfd) → `preferred`.
- **Check:** ACGLN, AGNCL, ADAMG → `preferred`; ASBHY, SBCLY, TKMSF → `adr`. 157 retyped in total (122 → preferred, 22 → stock, 13 → adr). Preferred Bank (PFBC), the only name hit with a market cap, stays a stock.
- **Where we differ:** the review's second rule — "no market cap and the same `longName` as a stock that has one" — was **rejected**: it hits real stocks (Liberty Broadband Class C, ADRs with a foreign OTC twin, units).
- **Found more — company financials on non-company tickers.** 31,326 `financials` rows sat on preferreds, SPACs and others (fetched while mistyped as `stock`). The analysis computed company ratios from the issuer's statements and the **preferred's** price — 332 of 430 preferreds had P/E / ROE / P/B (U.S. Bancorp's USB-PA P/E 149). Rows of any symbol outside stock / reit / adr are now deleted after every type correction: **0 of 525 preferreds** carry those ratios after the run.

### §25 Company identity and industry tags — Fixed (SIC step skipped)

- **CIK for every listed company:** integer `cik` and `company_id` (= CIK) from the SEC ticker list on every EDGAR run. **Check:** live stocks / ADRs with a CIK **2,003 → 7,458 of 7,538**, now INTEGER.
- **`company_id` groups ADR + foreign OTC twins too** — DTEGY / DTEGF, AKZOY / AKZOF share one CIK in the SEC list, so no name matching is needed (the review expected it would be). 422 companies have 2+ listed lines. The sector index now collapses a company's listings by `company_id` first: 122 → 219 duplicate lines collapsed; sector series unchanged against the SPDR ETFs (Financials 7.8x vs XLF 7.7x, daily correlation 0.92-0.99).
- **SEC SIC as a second opinion — skipped (measured weak).** For the review's own examples the SIC code agrees with Yahoo or cannot separate them: RIOT / MARA / WULF are SIC 6199 "Finance Services"; PSA / CUBE / EXR **and Prologis** are all 6798 "REITs". Only Sun Life differs (6311 Life Insurance). ~7,000 extra SEC requests for that.
- **Industry override table** (`config/industry_overrides.py`, applied before `screen_type`, peers and the sector index):
  - **19 bitcoin / crypto miners → "Crypto Mining".** Found more: Yahoo's "Capital Markets" tag made `screen_type` route them to **bank** — bank metrics on miners. All 17 in the analysis are now `standard`. Galaxy Digital and Bit Digital left out (broader crypto finance / ether treasury).
  - **Sun Life → "Insurance - Life"**, beside Manulife.
  - **Self-storage REITs left in "REIT - Industrial"** — the data provider files them there on purpose, and a separate group would hold ~5 names, too few for peer statistics.
- §25.5 (names): done in round 1.

### §22 History of delisted companies — Deferred

- **Check:** 0 stocks with a last price before 2016, as the review says.
- **Why deferred:** it needs a new collection effort (Polygon's inactive tickers, old SEC filers), and Yahoo usually drops a delisted ticker's price history, so most added symbols would arrive without prices. Since round 4, every company that dies is kept with its full history (`is_delisted`, `delisted_date`), so the survivorship gap stops growing. Merger / bankruptcy terms are not collected.

## Round 5 — Group E: signals consistency (2026-11-14)

**Approach:** each item was traced to its consumers first. Only what reaches `analysis.db` was changed; the rest is recorded with the reason. Results below are after the 2026-11-14 fetch and analysis run.

### §7 Stale snapshot fields — open parts: Fixed (dates, TTM lag); YPF recorded

- **Past `next_earnings_date`:** 2,878 stored dates were already past (the last report). `days_to_next_earnings` is now NULL when the date has passed (`analysis_layer/signals.py`); the raw row is kept and the weekly fetch refreshes it. **Check:** negative values 2,737 → **0**.
- **TTM a quarter behind:** **462 of 4,657** companies had a reported quarter in their earnings history newer than their newest stored quarter (AAPL's September quarter, reported Oct 30). Cause: the Yahoo financials due-date gate waited until last quarter-end + 91 + 45 days. Now a company is also due as soon as `signals.earnings_surprise` shows a newer reported quarter (`staleness.financials_not_due`), and stays due each week until it arrives.
  - **Check:** AAPL now has 2026-09-30. 416 of 4,673 still behind — 390 of them were fetched that day and **Yahoo's statements did not have the quarter yet** (its free statements lag the report by days to weeks). The SEC refresh (round 2) comes due 45 days after quarter-end and fills the quarter for SEC filers.
  - **Where we differ:** the review proposed scheduling from `next_earnings_date` before it passes; the earnings history is more reliable (Yahoo's calendar date is often stale, as the first bullet shows).
- **YPF currency labels — recorded, no fix.** Both labels are wrong in opposite ways: the estimate says USD but holds pesos (36.7 trillion), the statements say ARS but hold dollars (18.3 billion). It does not reach the screens: price currency (USD) ≠ statement currency (ARS), so the existing currency guard already uses Yahoo's own ratios for YPF. One company, a source error.

### §11 Estimate rows inconsistent — Partly fixed (revenue chain only)

- **What reaches the screens:** `forward_rev_growth` / `forward_eps_growth` use only Yahoo's growth inside the `+1y` row, so the `0y` row's year-ago revenue (the review's anchor check) is never used. The problem reaches us only where the `+1y` row itself mixes two scales.
- **Rule:** `forward_rev_growth` is NULL when the `+1y` row's year-ago revenue is more than **1.5x** off the `0y` row's estimate either way (`analysis_layer/estimates.py`).
- **Check:** the chain is off by > 2% for 28 revenue rows, but only the scale breaks are absurd: MUFG +120%, SMFG +104%, Mizuho +82%. Exactly 7 values blanked (MUFG, SMFG, Mizuho and their OTC lines, NextSource Materials); AAPL (+8.1%) and GE (+11.7%) — fiscal-year timing — unchanged.
- **Where we differ:**
  - **The review's 2% chain threshold would blank good values** — most gaps are a fiscal year that just rolled.
  - **Not applied to EPS.** 75 EPS rows fail the chain, but Yahoo's `0y` and `+1y` EPS rows often use different EPS definitions (reported vs adjusted): Cadence's `+1y` row grows a consistent 17% (8.42 → 9.85) while its `0y` estimate is 4.85. The check would blank good forecasts (Cadence, HASI, APG).
  - **EPS growth from a loss** (1,301 of 4,185 forecasts, e.g. American Airlines −1.24 → +0.95 shown as +176%) is a percentage of a negative base, but the direction carries information; left as is.
  - **Rescaling (the review's "power of ten" fix)** is not needed for the screens — the `0y` year-ago value is not used.

### §20 Smaller data items — signals bullets

- **Surprise near a zero estimate — Fixed.** Quarters whose estimate was within $0.05 of zero are left out of `earnings_surprise_avg` / `_last`; `earnings_beat_rate` now counts `eps_difference` > 0 (the review's fix). **Check:** 366 surprise rows > 1,000% (209 of them with an estimate under $0.05); companies with \|average surprise\| > 500%: **167 → 80**; beat rate changed for 218 of 4,533 companies.
  - **Where we differ:** the remaining extremes are real misses against small but not tiny estimates (ABTC: estimate $0.075, actual −$1.76 → −2,442%) — 14% of all quarters lie beyond ±100%, so capping them was not done. Scores use percentile ranks, so the extremes do not distort rankings.
- **Estimate currency ≠ statement currency (259 companies) — recorded, no fix.** Measured: our `forward_pe` (price ÷ Yahoo's forward EPS) matches Yahoo's own `forwardPE` for all but 2 of ~3,400 companies — Yahoo's forward EPS is already in the price currency. Growth rates are currency-neutral. Nothing in the analysis compares an estimate amount with a statement amount.
- **Insider holdings above shares outstanding — recorded, no fix.** `insider_total_held` is not used; `insider_net_buy_pct` is Yahoo's own percentage.
- **Analyst targets far from the price (409 of 4,433) — recorded, no fix.** `targetMeanPrice` is not used by the analysis. To revisit if a target-upside metric is added.

### Found during the run

- **Yahoo throttles silently.** In a 3½-hour fetch, the last ~7 minutes of the signals step returned empty answers for 727 symbols that normally have data — no error, no 429. Stored data is kept (a no-data answer overwrites nothing) and abandonment needs 4 strikes in a row, so nothing was lost; the symbols update the next week.
- **A crash from round 4.** Symbols retyped from the SEC mutual-fund list (which has no names) entered the EDGAR CIK name lookup and crashed it on the missing name; fixed by skipping nameless symbols.

## Round 6 — Group F: analysis foundations (2026-11-14)

**Approach:** all four are calculation fixes in `analysis.db`; nothing in the data files changed. Tested first with a read-only build over all 7,561 stocks and ADRs, then checked after the 2026-11-14 analysis run: the stored values match the test build exactly (market cap, P/E, `eps_ttm`, EV/EBITDA, growth, PEG, Graham / Lynch / DCF values).

### §2 Market cap counts one share class only — Fixed

- **Rule:** one helper, `metrics.company_shares`: Yahoo `marketCap` ÷ `regularMarketPrice` (all classes, in the listing's own units), falling back to `impliedSharesOutstanding`, `sharesOutstanding`, then the latest statement count.
- **Used by** `market_cap`, every price ratio (P/E, P/S, P/B, P/FCF), EV, `eps_ttm`, and the fair-value models (`intrinsic_value._shares` → Graham, Lynch, DCF, bear / bull scenarios). The sector index keeps its own per-listing logic.
- **Check:** stocks / ADRs more than 10% off `quotes.marketCap`: **1,085 → 122**.
  - GOOG P/E 6.9 → **15.3** (Yahoo 15.4) · BRK-B 10.8 → **16.3** (16.5) · META 18.9 → **21.8** (21.3) · MSFT unchanged (single class, the control).
  - GOOG's fair values roughly halve (DCF $143 → $72, Graham $340 → $154): they were divided by one class's shares.
- **Where we differ:** `market_cap` itself is all-class too, not only the ratios. No column measures one class's liquidity or float today, so there was nothing to keep on the one-class count.

### §10 and §14 steps 2-4 EPS split basis — Fixed (computed, not stored); change of basis partly

- **Rule** (`metrics.eps_current_basis`, used by the analysis and the fundamentals chart): each EPS row's implied share count (`net_income ÷ diluted_eps`) is tested against candidate bases: as filed, partly restated (only the later splits), or fully restated (factor 1). The basis closest to the reference wins.
  - **Reference:** the newest testable row. When a split came after it, its basis is picked against today's all-class share count (Monster's 2026 split).
  - **More than 2.5x off every candidate → the row is dropped** (bad value or a change of basis).
  - **Not testable** (EPS within $0.01 of zero, or a loss): split dates alone. EDGAR rows are taken as filed, Yahoo rows as already restated.
- **Replaces** `metrics.split_adjust`, which multiplied every row (Yahoo's already-restated ones too) by the later splits.
- **Check:** NVDA `eps_growth_trend` 178% → **97%** (EPS $0.17 → $4.90 in five years); MNST 77% → **16%** (PEG 0.5 → 2.6); ISRG 17.5% → 21.8%. Companies with \|EPS trend\| > 100%: **82 → 26**.
- **Where we differ:**
  - **Not stored as `diluted_eps_adj` columns.** It is computed in the analysis from `filed_date` (round 2), so a new split needs no re-write of `financials.db`. The chart uses the same function.
  - **2.5x, not 1.5x.** The test only has to tell split bases apart, and every split factor in the data is ≥ 2. A wider margin leaves room for real share-count changes (buybacks, dilution) between a row and the reference.
  - **Change of basis (step 4): only for profitable years.** UWM's years test cleanly (no drop needed). **Talen's 2021-22 loss rows (−$2,198 and −$2,864 a share, the pre-bankruptcy basis) can't be tested and are kept.** No EPS trend is computed for Talen (a loss in the window), but its `eps_growth_tau` (−0.2) is computed across the jump. A level-shift rule for loss years is a possible follow-up.

### §16 Growth columns break on one odd year — Fixed

- **Rule** (`metrics._robust_trend`), per series (revenue, EPS, FCF, book value, dividends), last `GROWTH_TREND_YEARS + 1` years on **real dates**, **≥ 4 points**:
  - **slope:** Theil-Sen (`scipy.stats.theilslopes`) on ln(value), when all values are > 0; otherwise on value ÷ median \|value\|;
  - **direction:** Kendall's tau (`scipy.stats.kendalltau`);
  - **steadiness:** 1.4826 × median absolute deviation of the residuals;
  - **first-year rule:** a first point more than 4 × the robust spread from the line (floor 3% in log terms) is dropped once.
- **Columns:** `_growth_trend` = robust growth, `_growth_vol` = steadiness. **`_growth_r2` → `_growth_tau`** (same "higher = better" sense). **`_growth_cv` dropped.** `_cagr_*` stay as endpoint growth. Scoring weight `eps_growth_r2` → `eps_growth_tau`. Filter registry, scoring rules and hints updated (0 metrics without a hint).
- **Check:** simulated series, median error in percentage points a year:

  | Case | Before | After |
  |---|---|---|
  | One bad last year | 10.9 | 1.47 |
  | One bad first year | 10.78 | 0.73 |
  | Clean series | 0.51 | 0.59 |

- **Trade-off:** fewer values, since 3 points no longer make a trend. EPS trends 2,382 → **2,025**, revenue 5,701 → **5,538**.
- **Known limit — a two-year trough at the start.** UAL and DAL still show ~31% / ~27% revenue trends (COVID years 2020-21). A second drop was tried and had no effect, since one low year then looks normal next to the other; reverted. Their wide steadiness spread flags the trend, and it fades as the window moves on.

### §20 Smaller data items — EV and EBITDA definition: Fixed (EV), Fixed differently (EBITDA)

- **EV** = all-class market cap + total debt − cash **+ minority interest + preferred equity** (each 0 when missing).
- **EBITDA** for `ev_ebitda` and `debt_to_ebitda`: Yahoo's **`normalized_ebitda`** (one-off charges removed), falling back to reported EBITDA.
- **Check:** EV/EBITDA more than 25% off Yahoo's `enterpriseToEbitda`: 1,971 → **1,687**. The rest is mostly Yahoo's own EV and EBITDA definitions (the review: Yahoo's `enterpriseValue` doesn't add up for ~10%).
- **Where we differ:** the review proposed recomputing EBITDA from its parts when it falls below operating income + D&A. Normalized EBITDA was preferred: Yahoo already removes the one-off charges behind the low rows (write-downs, e.g. Welltower FY2025), and it is filled for the same 7,145 companies as reported EBITDA. Group A found 2,351 annual rows where reported EBITDA is < 80% of normalized.

### Checked after the run

- **Sector indices:** daily-return correlation with the SPDR sector funds over two years 0.83-0.99, the same as before or slightly higher. The index uses its own share logic, so Group F doesn't change it.
- **FAPortfolio push:** `indices.db.gz` pushed and read back OK.
- **10 fair values differ from the test build** (DTM, EQT, ET, GNE, HE, MPC, NFG, OKE, VST, IDXG): utilities and pipelines, which the real run values as regulated. The test build didn't use that flag. Not a Group F effect.

## Round 7 — Group G, part 1: dividends and fair values (2026-11-15)

**Approach:** §9 and §17 together, since the dividend model needs §9's regular dividends. The review's §21 methods (special filter, recent cut, years paid) were used, with changes where the data disagreed. Tested with a read-only build over all 7,561 stocks and ADRs, plus a run of the dividend logic over all 30,087 payers including funds (0 errors). Results below are from that test build; after the 2026-11-15 analysis run the stored values match it exactly (see "Checked after the run").

### §9 Dividend fields — Fixed (with §21's dividend methods)

- **Module:** `analysis_layer/dividends.py`, used by `metrics._income_block`.
  - **Pay rate** of each payment = amount × payments per year (1, 2, 3, 4, 6, 12, 26 or 52, from its spacing). Compared as rates, not single payments, so a frequency switch reads as nothing (UDR quarterly → monthly).
  - **Special:** pay rate > 2.5 × the median of the lower half of the rates within ±400 days (the review's test).
  - **Yearly rate** = median pay rate of a calendar year's regular payments.
  - **Recent cut:** the review's two-test rule (rate now < 92% of 1-2 years ago and last 12 months < 95% of the 12 before).
- **Columns:**
  - **new `div_rate` / `div_yield`:** the current regular rate (and ÷ price). They feed `income_score` and the DDM.
  - **`div_rate_ttm` / `div_yield_ttm` kept** as "everything paid in 12 months, specials included".
  - **new `div_years_paid`** and **`div_recent_cut`**.
  - **trends, streak and consistency** now run on the yearly regular rates.
- **Check (the review's examples):**

  | Symbol | Before | After |
  |---|---|---|
  | ARE streak | 15 | 0 (recent cut) |
  | WHR streak | 29 | 0 (cut, then suspended in 2026: `div_rate` 0, as Yahoo shows) |
  | TROW / ORI / AFG streak | 3 / 2 / 0 | 29 / 29 / 23 |
  | CNA / MSM streak | 2 / 4 | 14 / 22 |
  | DDS yield | 5.0% (TTM) | 0.19% (`div_yield`) |
  | UDR / STAG streak | 15 / 11 | 16 / 14 (frequency switches not read as cuts) |

  - Conagra, Campbell's, Crown Castle, Telus, BXP, Dow, LyondellBasell, Healthcare Realty: streak → 0 (recent cut). Organon → 0 through its 2025 yearly rate (the cut is more than 18 months old, so `div_recent_cut` is 0).
- **Universe (2,531 stock / ADR payers):** 786 have at least one special (2.1% of payments); 141 recent cuts. Streak of companies ≥ $2B: 956 unchanged, 449 longer, 160 shorter.
- **Second source:** our current regular rate matches Yahoo's own forward `dividendRate` within 10% for **87%** of large US payers; the old TTM sum matched 73%.
- **Where we differ from the review:**
  - **The isolation test was replaced.** The review keeps "two high payments in a row" as a new normal. W.R. Berkley and American Financial pay specials several quarters in a row (WRB 2024: three of four payments), so a pair is not enough. A high payment now counts as a new normal only when **no lower payment follows within 400 days** (STAG's switch to quarterly).
  - **The special test runs on pay rates, not amounts.** On amounts, UDR's quarterly payments before its switch to monthly were 3x the monthly ones and read as specials.
  - **No special test for semi-annual / annual payers.** Their interim and final payments differ by design (Harmony, Eni), and the test flagged the finals. Their yearly rate is the trailing-year sum instead.
  - **Tolerance in the streak:** 2%, because split-adjusted amounts are rounded (EFSC 0.053 → 0.052 cost it 8 years). Companies reporting in another currency get 10%: their dividends arrive converted to dollars, and Manulife / Capital Power lost their streaks to a weaker Canadian dollar in 2022.
  - **A missing calendar year ends the streak** (it used to be skipped).
  - **`reg_div_growth` from TTM totals was not added.** The growth columns use the yearly rates instead, which don't move with how many payments fall in a window.
  - **Suspension:** a payer whose next payment is more than 2x its spacing + 30 days overdue has `div_rate` 0. That's 25 payers; for 12 of them Yahoo still shows a rate (mostly irregular payers, e.g. Banco Macro).

### §17 Fair-value models on weak growth — Fixed (steps 1-5), step 6 as a flag; the tails are not growth

- **Step 1 + 4 — Lynch and PEG on per-share net income:** years with net income and a diluted share count but no reported EPS are now filled with net income ÷ shares, using the same split-basis test (`metrics._eps_from_shares`). **MNST:** EPS trend 16.4% → **8.1%**, PEG 2.6 → **5.2** (the review expected 4-6), Lynch $18 → $9.
- **Step 2 — DCF with a negative FCF year:** `revenue_growth_trend` instead of 0% (DCF values filled 2,436 → 2,465).
- **Step 3 — DDM on regular dividends:** `div_rate` with the regular-dividend growth. Dillard's fair value $515 → $365, CME $346 → $182.
- **Step 5 — bear / bull:** the low / high end of the trend growth's own 80% confidence range (the Theil-Sen slope interval, Sen 1968), not trend ∓ `growth_vol`.
  - **Why:** `growth_vol` measures how far single years sit off the line, not how sure the growth rate is. MNST's four tidy EPS years gave bear ≈ bull.
  - **Result:** zero-width ranges 278 → 166. The remaining ones are mostly growth beyond a model's cap on both sides (NVDA).
- **Step 6 — new flag `fair_value_tail`** (fair value > 3x price). It does not change any value.
- **Check (the review's SQL):** fair values above 3x the price **341 → 338 (10.0%)**, above 5x **183 → 184**. **Not the sharp drop the review expected,** because the tails are not growth-driven. Of the 341 above 3x:
  - **Graham 139:** price far below book — distressed companies, and Fannie Mae / Freddie Mac.
  - **DDM 77:** high dividends about to be cut, or specials (now fixed for the specials).
  - **DCF 121:** mostly tiny or distressed companies with a large past cash flow; 36 had a high growth input.
  - **Lynch 4.**
  - 128 of the 341 are under $100M market cap. `fair_value_tail` marks them all for screens.
- **Found more — company second lines.** 181 lines have no Yahoo market cap of their own while another listing of the same company does: 36 Fannie Mae / Freddie Mac preferreds typed as stocks (names without "preferred"), Canadian preferreds, units, warrants, rights. They got the company's P/E on their own price (Fannie preferreds: P/E ~1.3). Now they get no market cap, price ratios, `eps_ttm` or fair values; ADRs are exempt (WDS). **Where we differ from round 4:** they are not retyped; the rule needs no name matching.

### Checks

- **Hints:** 124 filter bases, 0 without a hint.
- **Type check:** no new pyright errors in the changed modules; `api` / `services` 0 errors.
- **Run time:** the new dividend block is ~2x faster than the old one (12-14 s vs 27 s per 3,000 payers). The first version took 230 s for all payers; a vectorised special test brought it down.

### Checked after the run

- **Stored = test build:** all 7,561 stocks / ADRs match within 2%, with the same NULLs, on `div_yield`, `div_rate`, the streak, `div_years_paid`, `div_recent_cut`, PEG, EPS trend, every fair value and scenario, market cap, P/E and `fair_value_tail`.
- **Run:** 0 errors, 0 warnings; ~11 minutes; peak RAM 8.3 GB. No private helper columns reached `analysis.db`.
- **Saved filters:** dividend growers, REIT income and defensive anchors (now on `div_yield` / `div_rate`) pass `validate_filt`.
- **Sector indices:** daily-return correlation with the SPDR sector funds 0.81-0.99, the same as before or slightly higher.
- **FAPortfolio push:** `indices.db.gz` pushed and read back OK.

## Round 8 — Group G, part 2: smaller metric fixes, §19 (2026-11-15)

**Approach:** each item checked on the data first. Tested with a read-only build over all 7,561 stocks and ADRs; every column outside the ones below is unchanged. After the 2026-11-15 analysis run the stored values match the test build exactly (see "Checked after the run").

### `quick_health_score` is not Piotroski — Fixed (added `piotroski_f`)

- **New `piotroski_f`** (0-9), the review's nine tests on the two newest fiscal years, 300-430 days apart. `quick_health_score` is kept and its docs now say it is our own checklist.
- **Where we differ from the review:**
  - **Opening and average assets, as in the paper** (Piotroski 2000): ROA and asset turnover divide by the year's opening total assets, leverage by the average, when a third year is on file. Otherwise year-end assets, as the review wrote. The two forms differ by 2+ points for 191 of ~4,700 companies.
  - **Standard companies only**, like `quick_health_score`: the gross-margin and current-ratio tests assume an ordinary producing company. Most banks and insurers have no current assets or gross profit anyway.
- **Check:** MSFT 6, AAPL 9, KO 7, NVDA 4, F 3. 4,510 companies have a score, 1,952 of the 2,566 above $2B. Spread: 0-2 for 6%, 8-9 for 10%.

### Peer medians cover every row — Fixed (one difference)

- **Peer universe:** the median is taken over exchange-listed stocks / ADRs with a market cap of their own (new column `peer_universe`, 5,551 of 7,561). Every row is still measured against it, OTC included. It also excludes the preferreds, units and closed-end funds that carried a sector tag (8,533 tagged rows before).
  - **Example:** Auto Manufacturers' median P/E was 7.9 with the OTC lines and 18.3 without. Tesla's `pe_vs_industry` 5,993% → 2,515%.
- **At least 5 members** (was 3).
- **Health metrics added:** `debt_to_ebitda` and `interest_coverage` have peer columns now. Margins were already in.
- **Where we differ:** **no fallback to the sector** for a small industry. `_vs_industry` stays NULL there, so it always means the industry. Only 4 small industries (19 stocks) are affected.
- The same universe is meant for the §18 scores.

### Ratios with a negative denominator — Fixed (NULL plus a flag)

- **NULL when the denominator is zero or negative:** `pe`, `forward_pe`, `p_fcf` (a loss), `pb`, `debt_to_equity`, `roe`, `equity_multiplier`, `roe_roa_gap` (negative equity), `ev_ebitda`, `debt_to_ebitda` (negative EBITDA), `div_payout_ratio` (a loss), and `peg` through `pe`. Yahoo's own multiples for foreign filers are NULL when negative.
- **New flag `ebitda_negative`** (1 when EBITDA ≤ 0). A filter with a "debt/EBITDA ≤ 4 or is null" branch would otherwise let these companies pass through the "is null" branch.
- **Found more — ROE on negative equity.** A loss over negative equity gives a positive ROE: 629 stocks, 36 of them above $2B (Roblox 97,200%). Now NULL.
- **Saved filters checked:** Quality Compounders and Buyback Compounders got an `ebitda_negative = 0 (or unknown)` block. Defensive Anchors' "P/E < 25" and Financial Compounders' "ROE ≥ 12" / "P/B ≤ 2" no longer let loss-makers or negative-equity companies in, with no change to the files.
- **Check (the review's SQL):** negative `debt_to_ebitda` among stocks / ADRs ≥ $2B: **163 → 0**. Values that stay are unchanged.
  - **Left as is:** 13 negative `ev_ebitda` values where the EV itself is negative (cash above market cap + debt), which is a real reading. One bank (FBNC) has a tiny negative debt figure.

### Relative strength vs the industry — Moved to Group H

The review points to §21 for it; it needs the industry benchmark map, so it is built with the other §21 calculations.

### Checks

- **Hints:** 127 filter bases, 0 without a hint.
- **Type check:** no new pyright errors.
- **Run time:** Piotroski adds ~5 s per run, the same as `quick_health_score`. The first version took 23 s; reading each year's row once brought it down. The peer-median helper is now vectorised.

### Checked after the run

- **Stored = test build:** all 7,561 stocks / ADRs match on `piotroski_f`, `ebitda_negative`, every changed ratio, the peer columns (including the new debt/EBITDA and interest-coverage ones) and `peer_universe` (5,551; no fund or preferred is in it).
- **The review's SQL:** 0 negative `debt_to_ebitda` among stocks / ADRs ≥ $2B.
- **Run:** 0 errors, 0 warnings; ~11 minutes; peak RAM 8.3 GB.
- **Saved filters:** Quality / Buyback Compounders, Defensive Anchors and Financial Compounders pass `validate_filt`.
- **Sector indices and FAPortfolio push:** built as before (4,326 constituents); `indices.db.gz` pushed and read back OK.

## Round 9 — Group G, part 3: scores, §18 (2026-11-15)

**Approach:** measured on the stored 2026-11-15 analysis first, then re-scored that same data with the new code (read-only). After the 2026-11-15 analysis run the stored scores match that test exactly (see "Checked after the run"). Steps 1-4 are done; step 5 is started.

### Step 1 — Count the inputs: Fixed

- **`<category>_inputs`** (% of the category's weight present) next to each score, and `overall_inputs`.
- **A score is NULL below 50%** of its weight (`SCORE_MIN_INPUT_SHARE`, adjustable in Settings). The same rule applies to `overall_score` over the category weights.
- **Found:** half of the stocks' value scores rested on less than half of their weight. Loss-makers had only P/S and P/B, and scored *cheaper* than profitable companies (median 55 vs 49). Value scores of listed loss-makers: 2,000 → 426.
- **Effect:** stocks with a value score 93% → 47%, quality 100% → 60%, growth 92% → 59%, overall 100% → 61%. Funds have only momentum and income (35% of the overall weight), so **funds no longer get an overall score**. Non-payers no longer get an income score (their two zeros were 26% of its weight).

### Step 2 — A fixed, documented universe: Fixed

- **Ranking pools** (`rank_pool`): companies (stocks + ADRs), funds (ETFs, mutual funds, closed-end funds), and each other type on its own.
- **Reference set** (`rank_universe`): listed companies with their own market cap (`peer_universe`, §19, so live, no OTC, one ticker per company); one share class per mutual fund (SEC series id); every row of the other types. Every percentile, including `rs_rank`, is taken against it. Rows outside it are still scored, placed in that distribution.
- **Universe size stored** in `analysis_meta` per run: 5,551 companies, 12,246 funds (6,104 mutual-fund series, 5,812 ETFs, 330 CEFs).
- **Found:** a stock's dividend-streak rank moved ~21 points (median) because 22,000 fund rows were in the ranking. `rs_rank` of listed companies moved only ~1 point.
- The absolute rules' fade-out reach is now measured on the reference set too; that moved momentum scores most (median 10 points), since funds' price moves are smaller than stocks'.

### Step 3 — Rebuild on the fixed inputs: Fixed

- **Growth score** uses the robust trends (§16) for revenue, EPS and FCF instead of the 3- and 5-year endpoint CAGRs.
- **Margin of safety** earns no score when `fair_value_tail` = 1 (§17).
  - **Where it ends:** tails' value score 77 → 72 (others 48). The rest is real: tails are cheap on P/E, P/B and P/FCF too (goodness 75-89).

### Step 4 — Forecasts one way only: Fixed

- **New `revenue_growth_checked` / `eps_growth_checked`:** the trend growth, pulled halfway to the next-year forecast when the forecast is lower; a higher forecast adds nothing. Revenue uses the revenue forecast, EPS the EPS forecast. Pulled down: 2,195 revenue and 622 EPS rows.
- `forward_eps_growth` is no longer a growth-score input; `forward_pe` left the value score.
- **Check:** the top decile by forecast EPS growth: growth score median 52 → 46.
- **Where we differ:** the 1-2 year horizon is the only one Yahoo gives us per stock (its long-term growth field is unusable), so no horizon choice was needed.

### Step 5 — Validate before treating scores as signals: Started, not done

- **Not possible yet:** no past scores were kept. Only 5 backups exist, all from the last 6 weeks.
- **New `score_history.db`:** one snapshot per full run (symbol, price, the six scores, `rank_universe`), ~3 MB per run. Later returns per score decile can be measured once there are a few months of runs.

### Checks

- **Saved filters:** result counts before / after, on the same data: 11 of 13 within ±3 names (Emerging Dominators 85 → 83, Quality Compounders 60 → 57, GARP Movers 114 → 118, Trend Leaders 300 → 310); none emptied.
- **Examples (overall):** AAPL 60 → 58, MSFT 61 → 59, NVDA 63 → 67, KO 62 → 60, TSLA 41 → 53 (no longer pulled down by a 3 income score for not paying a dividend).
- **Hints:** 133 filter bases, 0 without a hint. **Type check:** no new errors. **Run time:** scoring ~4.5 s.

### Checked after the run

- **Stored = test:** all 35,701 rows match on every category score, `overall_score`, the `_inputs` columns, `rs_rank`, the `_growth_checked` columns, `rank_pool` / `rank_universe`.
- **Universe sizes in `analysis_meta`:** 5,551 companies, 12,246 funds, 530 preferreds, 633 warrants, 340 units, 161 SPACs.
- **`score_history.db`:** the first snapshot is written, 32,482 rows for 2026-11-13 (2.8 MB).
- **Run:** 0 errors, 0 warnings; ~10 minutes; peak RAM 8.3 GB. All 13 saved filters pass `validate_filt`. `indices.db.gz` pushed to FAPortfolio and read back OK.

## Not yet reviewed

§8, §21 (other than its dividend methods), and the open parts of §20 not covered above. Grouped as above.

## Known gaps / limitations

- **No fiscal-Q4 quarter from EDGAR for many companies** (e.g. Agilent): filers rarely tag a separate 3-month Q4, so EDGAR's Q4 row is often missing, TTM sums break there, and `quarter_sum_flag` stays NULL. Possible follow-up: derive Q4 = fiscal year − Q1 − Q2 − Q3.
- Round 1 counts are from the 2026-11-08 databases (prices as of 2026-11-06); round 2 counts from the 2026-11-11 re-fetch and analysis run. All checked on 2026-11-11.
- The cleaning thresholds (beta 10, payout 1,000%, gross margin 100%) come from the review and were not tuned further.
