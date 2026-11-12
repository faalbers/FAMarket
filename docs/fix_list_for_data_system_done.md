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

## Not yet reviewed

§1, §2, §3, §4, §6, §8-§19, §21-§23, and the open parts of §7, §20, §25. Grouped as above. One known point to research: §4/§12 propose classifying EDGAR rows by `fp` / `form`, but an earlier check on Apple's data found these fields unreliable for balance-sheet dates (the same date appears in a 10-Q comparative and a 10-K). The EDGAR code says so in its module notes.

## Known gaps / limitations

- Counts are from the 2026-11-08 databases (prices as of 2026-11-06), checked on 2026-11-11.
- `analysis.db` effects are pending the next full analysis run.
- The cleaning thresholds (beta 10, payout 1,000%, gross margin 100%) come from the review and were not tuned further.
