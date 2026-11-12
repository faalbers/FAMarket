# Fix list for the data system

Defects found in the data system's output databases that are best fixed where the data is produced, not worked around downstream, plus calculations and data worth adding there. Found by quality scans and research from 2026-11-09 on, with prices as of 2026-11-06; each later item carries its own date.

**Structure:**
- **§1-§8:** the first scans, ordered by impact.
- **§9-§15:** later defects, in the order they were found.
- **§16-§19:** `analysis.db` columns set aside because of how they are computed, each with a full method to replace them.
- **§20:** smaller items.
- **§21-§22:** calculations and data to add.
- **§23 on:** later defects, in the order they were found.

Each item is self-contained:
- **Made today** — how the data is built now, quoting the database's own reference file (`databases/<name>.md`, section named), so each fix reads as "change this step";
- what is wrong, with examples;
- **how to fix** it;
- a **check** — SQL to run directly against the named database file to confirm the problem, and later that the fix worked. Queries that span two files use `ATTACH`. Counts drift with every refresh; the examples are the stable test cases.

## Database files referred to

- **`symbols.db`** — table `symbols`: the master universe, `security_type`, `is_active` / `is_validated`. → issues 6, 15, 25
- **`ohlcv.db`** — table `ohlcv`: daily bars, `adj_close`, `dividends`, `splits`. → issues 3, 20
- **`financials.db`** — table `financials`: annual + quarterly statements, Yahoo (`source = 'yfinance'`) and SEC EDGAR (`source = 'edgar'`). → issues 1, 4, 7, 10, 12, 13, 14, 20, 22, 23, 24
- **`quotes.db`** — table `quotes`: Yahoo quote/profile snapshot (`marketCap`, `sharesOutstanding`, `recommendationMean`, `beta`, …). → issues 2, 5, 7, 20, 25
- **`signals.db`** — tables `estimates`, `earnings_surprise`, `ownership`: analyst consensus and event dates. → issues 5, 7, 11, 20
- **`analysis.db`** — table `analysis`: computed metrics per symbol (`market_cap`, `pe`, `eps_ttm`, …). → issues 2, 7, 9, 10, 16, 17, 18, 19, 21, 24, 25
- **`indices.db`** — table `sector_industry_index`: self-built sector/industry indices. → issue 8
- **`macro.db`** — no issues found (FRED series matched BLS, the Fed and Yahoo's `^TNX`).

## 1. Empty Yahoo annual rows block the EDGAR backfill — `financials.db`

- **Made today** (`financials.md` → "Source 1" quirk and "Source 2"): the EDGAR backfill is *additive only* — written only where Yahoo doesn't already own that (`period_end`, `freq`). A completely empty Yahoo row still counts as owned.
- **What:** Yahoo lists a fiscal year it has no numbers for (every value NULL). Because the EDGAR backfill only writes periods Yahoo doesn't "own", that year stays empty.
- **Impact:** ~6,000 of ~7,500 live stocks/ADRs miss one annual year at the hand-over, usually FY2021. Growth rates, averages and trend fits across that year break.
- **Examples:** AAPL has no FY2021 (`2021-09-30` row all NULL); MSFT lacks FY2021 and FY2022.
- **Quarterly too** (found 2026-11-09): the same empty Yahoo rows sit at the quarterly hand-over, from late 2024 on. ~5,200 live stocks/ADRs have ~11,000 empty Yahoo quarterly rows after their first filled quarter, so trailing-twelve-month sums across them break. Examples: A (Agilent) quarters `2024-10-31`, `2025-01-31`, `2025-04-30` all NULL; AA (Alcoa) `2024-09-30` and `2024-12-31` all NULL.
- **Fix:** change the ownership test in the EDGAR backfill. A Yahoo row with no values counts as "not owned", so EDGAR fills it (or drop empty Yahoo rows on write). Applies to annual and quarterly rows alike.
  - **Needs a one-time EDGAR re-run:** EDGAR is fetched once per company (`financials.md` → "Source 2"), so companies already fetched won't pick up the change otherwise.
- **Check:**
  ```sql
  -- examples: expect real numbers, not NULL, once fixed
  SELECT symbol, period_end, source, total_revenue, net_income
  FROM financials WHERE symbol IN ('AAPL','MSFT') AND freq = 'annual'
    AND period_end BETWEEN '2020-01-01' AND '2023-12-31' ORDER BY symbol, period_end;
  -- scale: Yahoo rows with no values, per frequency (should drop to ~0)
  SELECT freq, count(*), count(DISTINCT symbol) FROM financials
  WHERE source = 'yfinance'
    AND total_revenue IS NULL AND net_income IS NULL AND total_assets IS NULL
  GROUP BY freq;
  -- quarterly examples: expect numbers once fixed
  SELECT symbol, period_end, source, total_revenue, net_income FROM financials
  WHERE symbol IN ('A', 'AA') AND freq = 'quarterly'
    AND period_end BETWEEN '2024-07-01' AND '2025-06-30' ORDER BY symbol, period_end;
  ```

## 2. Market cap counts one share class only — `analysis.db` (with `quotes.db`)

- **Made today** (`analysis.md` → "Global conventions"): market cap = price × shares, where shares = Yahoo `sharesOutstanding`, else the latest `ordinary_shares_number`, else `share_issued`. `eps_ttm` = TTM net income ÷ the same shares (§2 there).
- **What:** Yahoo's `sharesOutstanding` covers only that ticker's class, so `market_cap` counts one class. Every multiple built on it (P/E, P/S, P/B, EV/EBITDA, …) is off; so is `eps_ttm`, which divides company-wide net income by one class's shares. (`financials.md` → "Known limitations": the statement share counts *are* per company.)
- **Examples:** GOOGL $1.68T and GOOG $1.58T vs ~$3.5T real; BRK-B $0.80T vs $1.22T; META $1.35T vs $1.56T. GOOGL `eps_ttm` $41.48 vs ~$19 real. ~800 listed stocks differ from `quotes.marketCap` by > 10%.
- **Fix:** change the market-cap rule.
  - **Company-level ratios** (P/E, P/S, P/B, P/FCF, EV): use Yahoo's `quotes.marketCap`, which covers all classes. Fall back to the current chain only when it is missing.
  - **Per-share figures** (`eps_ttm`): divide by the company's total shares (`marketCap` ÷ price), not one class's count.
  - **Keep one class's value only for questions about that ticker**, e.g. liquidity or float.
- **Check:**
  ```sql
  ATTACH 'quotes.db' AS q;
  SELECT a.symbol, round(a.market_cap / 1e9) AS analysis_bn, round(qq.marketCap / 1e9) AS yahoo_bn,
         round(a.eps_ttm, 2) AS eps_ttm, qq.trailingEps
  FROM analysis a JOIN q.quotes qq USING (symbol)
  WHERE a.symbol IN ('GOOGL', 'GOOG', 'BRK-B', 'META', 'MSFT');   -- MSFT is a single-class control
  ```

## 3. Price history defects — `ohlcv.db`

- **Made today** (`ohlcv.md` → "How it is retrieved"): each fetch fully replaces a symbol's rows with what Yahoo returned, with no validation step. The defects below come from Yahoo and pass straight through.
- **Event booked twice:** TDS 2005-05-16 has both a $34.30 dividend and a 1.951 split for one event — every earlier `adj_close` is ~40x too low.
- **Old and new company stitched together:** CHRD 2020-11-20 jumps $0.12 → $31 (Oasis Petroleum's bankruptcy exit) — a fake 258x return.
- **Likely unrecorded reverse split:** QH 2026-04-27, $0.12 → $3 with no split row.
- **Broken `adj_close` on ~150 symbols, mostly OTC stocks** (152 by the check below, 2026-11-11): negative (PLTYF: close $0.02, adj −0.33) or absurd (PSBAF: close $0.17, adj 232 million).
- **~145 splits look unadjusted** (price jumps by the split ratio around the split date); ~2,600 one-day moves ≥ 10x, almost all micro/OTC.
- **Fix:** add a validation step right after each fetch, before the rows replace the old ones.
  - **Reject or flag:** `adj_close` ≤ 0, `adj_close` > `close`, and one-day moves ≥ 10x.
  - **Check:** that no ex-date carries both a dividend and a split.
  - **Store the result** as a per-row flag column, or a separate `price_flags` table (symbol, date, check, note), so analyses can skip bad windows.
- **Check:**
  ```sql
  -- the three named cases
  SELECT symbol, date, close, adj_close, dividends, splits FROM ohlcv
  WHERE (symbol = 'TDS'  AND date BETWEEN '2005-05-12' AND '2005-05-18')
     OR (symbol = 'CHRD' AND date BETWEEN '2020-11-18' AND '2020-11-23')
     OR (symbol = 'QH'   AND date BETWEEN '2026-04-23' AND '2026-04-28')
  ORDER BY symbol, date;
  -- dividend and split on the same day (expect none)
  SELECT symbol, date, dividends, splits FROM ohlcv WHERE dividends > 0 AND splits > 0;
  -- broken adjusted prices (full scan of a large file: takes a while)
  SELECT count(DISTINCT symbol) FROM ohlcv WHERE adj_close <= 0 OR adj_close > close * 1.001;
  ```

## 4. EDGAR backfill defects — `financials.db`

- **Made today** (`financials.md` → "Source 2"):
  - each column is mapped from US-GAAP tags, "several tags tried in order";
  - `freq` is decided by the fact's duration (~1 year = annual, ~1 quarter = quarterly);
  - a restated figure keeps the latest filing's value;
  - EDGAR is fetched **once per company**, so any mapping fix needs a one-time re-fetch of all companies.
- **Wrong revenue tag:** AAT 2018 revenue ~$40M; the 10-K says $330.9M (net income $19.7M matches). Probably a narrower tag picked first.
- **Fake extra annual rows:** a real fiscal year copied under a wrong `period_end` (JBTM `2013-03-31` duplicates FY2013); ~2,400 companies (2,411 by the check below, 2026-11-11).
- **Quarters don't add up:** four quarters ≠ the annual figure in ~8% of EDGAR years (Yahoo ~2%).
- **Gross profit ≠ revenue − cost of revenue** in ~7% of EDGAR rows (cost of revenue likely partial).
- **Other:** ~250 sudden ≥ 500x jumps (units), ~535 positive `capital_expenditure` (sign flip missed), ~90 rows with `period_end` in the future (counts from the check below, 2026-11-11).
- **Fix:**
  - **Annual rows:** keep only facts whose duration ends on the fiscal-year end. Companyfacts gives each fact's `fy`, `fp` (FY / Q1-Q4) and `form` (10-K / 10-Q) — use `fp = 'FY'` from a 10-K for annual.
  - **Revenue tag order:** put the broadest tags first (§13 lists the order).
  - **Cross-check:** compare the four quarters with the annual figure, and flag a year where they differ by > 2%.
  - **Dates:** reject `period_end` after the filing's `filed` date.
  - **Capex sign:** flip a positive capex to negative, as the sign convention says.
- **Check:**
  ```sql
  -- AAT 2018: expect revenue ~330.9M
  SELECT symbol, period_end, freq, source, total_revenue, net_income FROM financials
  WHERE symbol = 'AAT' AND period_end LIKE '2018%' ORDER BY freq, period_end;
  -- JBTM: expect one annual row per fiscal year (no 2013-03-31)
  SELECT period_end, source, total_revenue, net_income FROM financials
  WHERE symbol = 'JBTM' AND freq = 'annual' AND period_end BETWEEN '2012-01-01' AND '2014-12-31';
  -- annual rows less than 10 months apart (fake duplicates)
  SELECT count(DISTINCT a.symbol) FROM financials a JOIN financials b
    ON a.symbol = b.symbol AND a.freq = 'annual' AND b.freq = 'annual'
   AND b.period_end > a.period_end AND b.period_end < date(a.period_end, '+10 months');
  -- future dates and positive capex (expect 0)
  SELECT sum(period_end > date('now')), sum(capital_expenditure > 0) FROM financials;
  ```

## 5. "Missing" stored as 0 instead of NULL — `signals.db`, `quotes.db`

- **Made today:**
  - `signals.md` → "How it is retrieved": "stored raw, as Yahoo returns it".
  - `quotes.md` → "How it is retrieved": the cleaning step turns blanks, `"N/A"`, `"-"`, NaN and infinities into NULL — but not Yahoo's placeholder zeros.
- **`signals.db` / `estimates`:** `rev_avg = 0` (and low/high 0) with 0 analysts — ~13k rows, ~3.7k companies (2026-11-11).
- **`quotes.db` / `quotes`:** `recommendationMean = 0` where the scale is 1–5.
- **Fix:** extend the cleaning step with two placeholder rules, matching the "NULL = not applicable" convention in `symbols.md`:
  - **`estimates`:** store NULL for `rev_*` / `eps_*` when the analyst count is 0 or missing.
  - **`quotes`:** store NULL for `recommendationMean` = 0 (outside its 1-5 scale).
- **Check:**
  ```sql
  -- in signals.db (expect 0)
  SELECT count(*), count(DISTINCT symbol) FROM estimates
  WHERE rev_avg = 0 AND coalesce(rev_num_analysts, 0) = 0;
  -- in quotes.db (expect 0)
  SELECT count(*) FROM quotes WHERE recommendationMean = 0;
  ```

## 6. Mistyped securities — `symbols.db`

- **Made today** (`symbols.md` → "Table `symbols`", `security_type`): mapped from Polygon's type code (`CS` → stock, `PFD` → preferred, …). Rows from the SEC lists arrive untyped and are typed later from Yahoo's `quoteType`. Rows from the SEC mutual-fund list are typed `mutual_fund` by source.
- **~170 preferreds / baby bonds typed `stock`** — they share the issuer's name and have no market cap (ACGLN Arch Capital, AGNCL AGNC, ADAMG Adamas Trust).
- **19 foreign stocks typed `mutual_fund`** — Yahoo calls them EQUITY (ASBHY Asahi, SBCLY Sumitomo Bakelite, TKMSF TKMS).
- **Fix:** add a re-typing pass after the quote fetch.
  - **Preferreds typed `stock`:** a live `stock` with no `quotes.marketCap` that shares its `longName` with a live stock that has one, or whose name contains "Preferred", "Depositary Shares", "Notes due", "% Senior" → `preferred`.
  - **Foreign stocks typed `mutual_fund`:** a `mutual_fund` row whose `quotes.quote_type` is `EQUITY` → `stock`. Yahoo is right here: these came from the SEC fund list.
- **Check:**
  ```sql
  -- in symbols.db: expect preferred for the first three, stock for the last three
  SELECT symbol, security_type FROM symbols
  WHERE symbol IN ('ACGLN', 'AGNCL', 'ADAMG', 'ASBHY', 'SBCLY', 'TKMSF');
  ```

## 7. Stale or inconsistent snapshot fields — `signals.db`, `quotes.db`, `analysis.db`, `financials.db`

- **Made today:**
  - `signals.md`: refetched weekly; `next_earnings_date` "may be past dates if Yahoo hasn't updated".
  - `quotes.md`: values stored "as Yahoo returns them. Nothing is recalculated".
  - `analysis.md` → "Known limitations": a missing quarter makes TTM metrics NULL.
- **`next_earnings_date` in the past** for ~2,000-3,000 companies right after earnings season (the count depends on the day) — the last report, not the next.
  - **Fix:** when the stored date is before the fetch date, store NULL (or move it to a `last_earnings_date` column), and refetch that symbol a few days later.
- **TTM a quarter behind Yahoo** right after earnings season: `financials` lacks the quarter just reported (MSFT, XOM in Nov 2026).
  - **Fix:** schedule a financials refetch for each company a few days after its earnings date (from `ownership.next_earnings_date` before it passes).
- **Currency labels:** YPF's revenue estimate is ~2,000x its statements (one of the currency labels is wrong). See also §20, currency mismatch.
- **Absurd Yahoo values passed through:** ~140 `|beta| > 10` across all securities (~85 among listed stocks; ELOX −169,205), payout ratios > 1,000%, gross margins > 100%.
  - **Fix:** add range rules to the `quotes` cleaning step: `|beta|` > 10, `payoutRatio` > 10 (fraction), `grossMargins` > 1 → NULL. `analysis.md` already NULLs WACC for `|beta|` > 10, so the rule exists, just later.
- **Check:**
  ```sql
  -- in signals.db: past next-earnings dates (use the latest price date instead of 'now' if preferred)
  SELECT count(*) FROM ownership WHERE next_earnings_date < date('now');
  -- in financials.db: newest quarter on file (Sep-2026 quarter missing for MSFT/XOM in Nov 2026)
  SELECT symbol, max(period_end) FROM financials
  WHERE symbol IN ('MSFT', 'XOM') AND freq = 'quarterly' AND net_income IS NOT NULL GROUP BY symbol;
  -- in quotes.db: absurd values (expect 0 once nulled)
  SELECT sum(abs(beta) > 10), sum(payoutRatio > 10), sum(grossMargins > 1) FROM quotes;
  -- in signals.db: YPF revenue estimate vs latest annual revenue, with both currency labels
  ATTACH 'financials.db' AS f;
  ATTACH 'quotes.db' AS q;
  SELECT e.rev_avg, e.currency,
         (SELECT total_revenue FROM f.financials WHERE symbol = 'YPF' AND freq = 'annual'
            AND total_revenue IS NOT NULL ORDER BY period_end DESC LIMIT 1) AS last_annual_revenue,
         (SELECT financialCurrency FROM q.quotes WHERE symbol = 'YPF') AS statement_currency
  FROM estimates e WHERE e.symbol = 'YPF' AND e.horizon = '0y';
  ```

## 8. Self-built indices — `indices.db` (work in progress)

- **Made today** (`indices.md` → "How the levels are calculated", "Who is included"):
  - weights use statement share counts (`ordinary_shares_number`, else `share_issued`), so a member with no share count in a period can't be weighted;
  - "Breadth before start": an **industry series starts once 2 members can be weighted** (a sector once 20).
- **Early industry history is dominated by small caps** where big members lack share counts: Internet Content 2013 shows +256% because Google, Meta and Baidu had no share counts in 2012–13 and could not be weighted.
- **Uranium** falls to ~3% of its 2010 start by 2015 while the URA ETF fell to ~12% and Cameco to ~34% — likely the same cause.
- Sector series track the SPDR ETFs within ~1pp/yr.
- **Fix:**
  - **Change the breadth-before-start rule** from "2 members" to a coverage test: start an industry series once the weightable members cover most of the industry's market cap at that time. Today's market cap of those members is a workable stand-in, e.g. ≥ 60%.
  - **Record each series' coverage** in `index_meta`.
  - **Old share counts:** filling them (§14 does this for `diluted_average_shares`; `ordinary_shares_number` needs the same check for 2008-2013) lets more big members be weighted early.
- **Check:**
  ```sql
  -- in financials.db: share counts that the 2012-13 weights need (expect values, not NULL)
  SELECT symbol, period_end, ordinary_shares_number, share_issued FROM financials
  WHERE symbol IN ('GOOGL', 'META', 'BIDU') AND freq = 'quarterly'
    AND period_end BETWEEN '2012-06-30' AND '2013-12-31' ORDER BY symbol, period_end;
  -- in indices.db: Internet Content 2013 return (Google +58%, Meta +105% that year)
  SELECT date, level FROM sector_industry_index
  WHERE kind = 'industry' AND label = 'Communication Services | Internet Content & Information'
    AND date IN ('2012-12-31', '2013-12-31');
  ```

## 9. Dividend fields miss cuts, reset on specials, include specials — `analysis.db`

- **Made today** (`analysis.md` → "6. Dividends"):
  - `div_rate_ttm` / `div_yield_ttm` = every dividend with an ex-date in the last 365 days, specials included;
  - the trend metrics (`div_cagr_*`, `div_growth_trend`, `_vol`, `_r2`, `_cv`) and the streak use **each complete calendar year's highest single payment**;
  - `div_consecutive_years` = trailing years in which that highest payment did not fall.
  - **This one rule explains all the problems below.** A special is the year's highest payment. A switch to monthly payments lowers the highest payment with no cut. A cut late in a year, or in the current year, doesn't show until a full calendar year has passed.
- **`div_consecutive_years` (the "not cut" streak) misses recent cuts.** Found 2026-11-10 among companies ≥ $2B: 14 with a clear cut in the last ~2 years still show a streak ≥ 2. Verified: Alexandria Real Estate cut 45% in Dec 2025 ($1.32 → $0.72), streak shows 15; Whirlpool cut from $1.75 to $0.90 in Aug 2025, streak shows 29. Others in the same state: Conagra (18), Campbell's (12), Crown Castle (11), Telus (10), BXP, Dow, LyondellBasell, Organon, Healthcare Realty.
- **`div_consecutive_years` also resets when nothing was cut — after a special dividend.** Found 2026-11-10 comparing with Schwab SCHD holdings: T. Rowe Price shows 3 (its 2021 highest payment was the $3.00 special; 2022's highest was a $1.20 regular → read as a fall), Old Republic 2, American Financial 0, CNA 2, MSC Industrial 4 — all pay every year, most for decades. Same root as the bullet above: the highest single payment of a year includes specials, so the streak errs both ways.
- **Fix** — replace the "highest single payment per calendar year" rule:
  1. **Remove specials first** (method in §21).
  2. **Compare yearly pay *rates*** (regular payment × payments per year), not single payments or calendar-year totals.
  3. **Streak = years in a row the rate did not fall.** Also run the cut check of §21 on the last ~2 years, so a recent cut ends the streak at once.
  4. **Use regular payments only** for the trend metrics and `div_yield_ttm`, or add `reg_*` twins next to them.
  5. **Add `years_paid`** (unbroken years with any dividend) — what "income stock" questions usually need.
  - **Full methods:** special-dividend filter, years paid, cut check — §21.
- **Watch out when fixing:** we tried four ways to rebuild a 10-year cut history from the payment rows alone — each was wrong for ~5-10% of companies (missing payment rows, specials, shifting dates, currency on ADRs). What worked: drop specials first, compare **yearly pay rates** not single payments, and treat a **pause** (gap > 2x the normal spacing, then a lower rate) as a cut. Dividend announcements (declared amount + frequency), if the data system can get them, would be far more reliable than payment rows.
- **`div_yield_ttm` and `div_cagr_*` include special dividends.** Dillard's pays ~$0.30 quarterly plus $25-30 specials; its TTM yield shows 5.2%, almost all one-off.
- **A switch in payment frequency looks like a cut in single payments** (UDR: $0.435 quarterly → $0.145 monthly, same yearly amount; STAG: monthly → quarterly). Under the highest-single-payment rule, UDR's switch reads as a 67% fall. Any cut logic should compare yearly pay rates.
- **Check:**
  ```sql
  -- in ohlcv.db: the payments behind the cut (expect 1.32 then 0.72 from 2025-12)
  SELECT date, dividends FROM ohlcv WHERE symbol = 'ARE' AND dividends > 0 AND date >= '2025-01-01';
  -- in analysis.db: the streak (expect 0 after the cut; shows 15)
  SELECT symbol, div_consecutive_years, div_yield_ttm FROM analysis WHERE symbol IN ('ARE', 'WHR', 'DDS');
  -- in analysis.db: reset by a special (expect decades; shows 3 / 2 / 0)
  SELECT symbol, div_consecutive_years FROM analysis WHERE symbol IN ('TROW', 'ORI', 'AFG');
  -- in ohlcv.db: T. Rowe's yearly totals (2021 = regular + special)
  SELECT substr(date, 1, 4) AS yr, round(sum(dividends), 2) FROM ohlcv
  WHERE symbol = 'TROW' AND dividends > 0 AND date >= '2019' GROUP BY yr;
  ```

## 10. EPS history mixes split bases — `financials.db`, and growth built on it — `analysis.db`

- **Made today:**
  - `financials.md` → "Conventions": per-share values and share counts are stored **as filed**, but "a later filing may already have restated older values, so a series can mix bases". Yahoo rows also come restated to today's basis (NVIDIA FY2023 shows ~25.1B shares, the post-2024-split count).
  - `analysis.md` → "5. Growth": the EPS series is "split-adjusted with `ohlcv.splits`" — every row is multiplied by the later splits. A row that was **already restated** gets adjusted a second time.
- **Annual `diluted_eps` is on a different split basis from row to row.** NVIDIA (4:1 split 2021, 10:1 split 2024): FY2019 6.63 (as reported), FY2020 1.13 and FY2021 1.73 (adjusted for the 2021 split only), FY2023 0.174 onward (adjusted for both). Growth computed across these rows is meaningless.
- **`eps_growth_trend` and everything using it is inflated for companies that split.** Monster 77%/yr (net income grew ~7%/yr; 2:1 split 2023), Intuitive Surgical 60% (~24%; 3:1 in 2021), Tetra Tech 30% (~10%; 4:1 in 2023). `peg` (= `pe` / `eps_growth_trend`) inherits it - Monster shows 0.55 at a P/E of 42.
- **Fix** — find each row's basis before adjusting it, instead of assuming "as filed":
  1. **Implied share count:** `net_income ÷ diluted_eps`. This is the count on that row's own basis.
  2. **Compare it with today's-basis count** — the newest `diluted_average_shares`, or the count rolled back through the later splits.
     - **Already on today's basis:** the ratio is ~1. Don't adjust.
     - **As filed:** the ratio ≈ the product of the later splits. Adjust by that product.
     - **Neither:** a change of basis or a bad value (§14). Store NULL.
  3. **Store the adjusted series** as `diluted_eps_adj` / `diluted_average_shares_adj`, keeping the as-filed columns.
  4. **Base EPS growth on the adjusted series**, or simpler and more robust: growth of total net income combined with growth of the adjusted share count (§16).
  - **Edge cases:** a near-zero or negative EPS makes the implied count meaningless. Then use the split dates alone and mark the row as unchecked.
- **Check:**
  ```sql
  -- in financials.db: NVDA EPS rows (expect one basis; see three)
  SELECT period_end, diluted_eps, net_income FROM financials
  WHERE symbol = 'NVDA' AND freq = 'annual' AND period_end >= '2019-01-01' ORDER BY period_end;
  -- in analysis.db: the inflated trend (expect ~7 for MNST)
  SELECT symbol, eps_growth_trend, peg, pe FROM analysis WHERE symbol IN ('MNST', 'ISRG', 'TTEK');
  ```

## 11. Last-year revenue on the current-year estimate row is often wrong — `signals.db`

Found 2026-11-10 (prices 2026-11-06) while building the growth categorizer. It comes straight from Yahoo's revenue estimate table.

- **`estimates.rev_year_ago` on the `horizon = '0y'` row should equal the last reported fiscal year's revenue.** For 172 of 1,898 companies (≥ $2B, stocks + ADRs) it is more than 10% off the last annual `total_revenue` in `financials.db`.
- **Three causes, mixed together:**
  - **Wrong scale** — off by more than 2x (51 companies), mostly foreign filers: Sony 0.06x (0.8 vs 12.5 trillion yen), Takeda, Coca-Cola FEMSA, Argentine stocks (YPF 1,400x, Pampa).
  - **Different revenue definition** — financials and asset managers (Apollo 0.14x, AGNC 0.38x, Ares 0.76x): Yahoo's estimate revenue is not the statement's total revenue.
  - **Timing** — ratios of 0.85-0.9 can be a fiscal year that just ended and is not yet rolled into the estimate.
- **The `+1y` row is usually consistent** — its `rev_year_ago` equals the `0y` row's `rev_avg` for 1,920 of 1,929. **Not for 9:** MUFG, SMFG, Mizuho, KT, SK Telecom (two scales inside one row: +45-120%/yr either way), Apple (the `0y` row a year stale), CBT, KEP, PKX.
- **Safe use until fixed** — treat the forecast as missing when the `+1y` row's `rev_year_ago` differs from the `0y` row's `rev_avg` by more than 2%; otherwise, when `0y.rev_year_ago` is within 10% of the last annual statement's revenue, use growth per year over two years = (`+1y.rev_avg` ÷ `0y.rev_year_ago`)^0.5 − 1; else one year from the `+1y` row alone = `+1y.rev_avg` ÷ `+1y.rev_year_ago` − 1.
- **Made today:**
  - `signals.md` → "How it is retrieved": stored raw, as Yahoo returns it.
  - `analysis.md` → "9. Analyst…": `forward_rev_growth` is built on these rows, so it inherits the wrong scale.
- **Fix** — add a consistency step after the signals fetch, before `analysis.db` uses the rows:
  1. **Chain check.** The `+1y` row's `rev_year_ago` must equal the `0y` row's `rev_avg` within ~2%. Otherwise flag both rows; don't use them for growth.
  2. **Anchor check.** The `0y` row's `rev_year_ago` must be within ~10% of the last annual `total_revenue` in `financials.db`, in the same currency.
     - **Off by about a power of ten** (×1,000, ×0.001, …): rescale.
     - **Otherwise:** flag it as a different definition (financials, asset managers) and use only the one-year growth inside the `+1y` row.
- **Check:**
  ```sql
  -- in signals.db: Sony's year-ago revenue (expect ~1.25e13 yen; shows 8.0e11)
  SELECT symbol, horizon, rev_year_ago, rev_avg, currency FROM estimates
  WHERE symbol IN ('SONY', 'TAK') AND horizon IN ('0y', '+1y');
  -- in financials.db: the statement it should match
  SELECT symbol, period_end, total_revenue FROM financials
  WHERE symbol IN ('SONY', 'TAK') AND freq = 'annual' ORDER BY period_end DESC LIMIT 4;
  ```

## 12. EDGAR quarter-end rows stored as `annual` — `financials.db`

Found 2026-11-10 (prices 2026-11-06). It made a quality check count three extra "years" per year and throw good December rows away as duplicates.

- **EDGAR rows at quarter-end dates carry `freq = 'annual'`.** They hold a quarter's balance sheet (assets, liabilities, cash, shares) and sometimes running year-to-date net income, but no revenue. 3,450 such rows at 1,226 live stocks/ADRs. Amazon: rows at every March, June and September 2019-2026.
- **Impact:** anything that treats every `annual` row as a fiscal year sees three extra "years" per year — wrong year counts, false duplicates, wrong year-over-year changes for balance-sheet items.
- **Made today** (`financials.md` → "Source 2"): "`freq` is decided by the reported duration: ~1 year = annual, ~1 quarter = quarterly".
  - **Why balance sheets slip through:** balance-sheet facts (assets, cash, shares) are *instants* with no duration, so this rule can't classify them, and they land in `annual`.
  - **Year-to-date flows** (6/9-month net income) are said to be dropped, but some still arrive on these rows (Amazon).
- **Fix** — classify instant facts by the filing they come from:
  - **Companyfacts tells you:** each fact carries `fp` (FY, Q1, Q2, Q3) and `form` (10-K, 10-Q).
  - **Annual:** an instant from a 10-K with `fp = 'FY'`, dated on the fiscal-year end.
  - **Quarterly:** an instant from a 10-Q.
  - **Year-to-date flows:** drop any duration fact that is neither ~3 nor ~12 months.
  - **Needs a one-time EDGAR re-fetch** (EDGAR is fetched once per company).
- **Also unmasked — wrong old EDGAR values** (check before use): Voya FY2020 revenue $1.75B (next year $5.8B), McGrath RentCorp FY2020 $0.13B (next year $0.64B) - likely a different revenue tag; Talen Energy diluted shares ~0 before 2024 (post-bankruptcy), so per-share math explodes.
- **Check:**
  ```sql
  -- in financials.db: Amazon's quarter-end 'annual' rows (expect only December rows)
  SELECT period_end, source, total_revenue, net_income, total_assets FROM financials
  WHERE symbol = 'AMZN' AND freq = 'annual' AND period_end >= '2024-01-01' ORDER BY period_end;
  ```

## 13. Wrong EDGAR revenue on old annual rows (mostly FY2020) — `financials.db`

Found 2026-11-11 (prices 2026-11-06), measuring growth trends: the wrong first year inflated growth for dozens of large companies.

- **Many EDGAR annual rows hold a far-too-small `total_revenue`** — most visible at FY2020, the last EDGAR year before the FY2021 hole. Mostly banks, REITs and insurers: MetLife FY2020 $1.35B (real total ~$68B), American Tower $0.50B (real ~$8B), Regions, Essex, UDR, Rexford, KeyCorp, Capital One, United Rentals, Voya, McGrath. Earlier years of the same companies show the same small values (MetLife 2018-2020 all ~$1.3B), so it looks like the wrong XBRL tag (a sub-line such as one revenue component) rather than one bad filing. Net income looks right on those rows.
- **Size:** of 1,321 companies with revenue and assets in every year of the last-6-year window, 45 have a first-year revenue-to-assets ratio 10x+ off the later years and 79 more are 3-10x off (the 3-10x band also holds real COVID-year drops).
- **Impact:** any growth or margin computed across the EDGAR → Yahoo seam is wrong; for these companies, EDGAR revenue history is unusable as is.
- **Made today** (`financials.md` → "Source 2"): `total_revenue` is mapped from US-GAAP tags "tried in order". When an early tag in that list is a sub-line (one revenue component), it wins. EDGAR is fetched once per company, so a corrected order needs a one-time re-fetch.
- **How to fix:**
  1. **Pick the broadest revenue tag first.** Try, in order: `Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax`, `RevenueFromContractWithCustomerIncludingAssessedTax`, `SalesRevenueNet`. For banks, when none of these exists, use `InterestAndDividendIncomeOperating` + `NoninterestIncome`. For insurers, look for `Revenues` before any premium line.
  2. **Use the full-year fact only:** its duration must be ~12 months and must end on the fiscal-year end.
  3. **Test each EDGAR year at the seam.** Compare its revenue with the first Yahoo year after it, and do the same for `total_assets`. If revenue differs by more than 3x while assets moved less than 1.5x, the revenue is a sub-line. Try the next tag, or store NULL.
- **Check:**
  ```sql
  -- in financials.db: MetLife / American Tower revenue around the seam (expect ~6.8e10 / ~8e9 for 2020)
  SELECT symbol, period_end, total_revenue, net_income, total_assets FROM financials
  WHERE symbol IN ('MET', 'AMT') AND freq = 'annual' AND period_end BETWEEN '2018-01-01' AND '2023-12-31'
  ORDER BY symbol, period_end;
  ```

## 14. Old share counts are never filled — `financials.db`

Found 2026-11-11 (prices 2026-11-06).

- **Made today:**
  - `financials.md` → "Source 2": EDGAR fills "a curated subset of columns". The share columns in it are `ordinary_shares_number` and `share_issued` (point-in-time). **`diluted_average_shares` is not in the mapping**, and neither is `basic_average_shares`. Isolated one-off share-count spikes are already dropped as tagging noise.
  - `analysis.md` → "5. Growth": `share_count_chg_1y` uses diluted average shares, so it only works in the Yahoo years.
- **What:** `diluted_average_shares` is empty on every EDGAR annual row. Fill rate: **0% for fiscal years before 2021** (34,243 annual rows with revenue), ~85-89% from 2022 on (Yahoo rows).
- **Impact:**
  - **Short history.** The share-count trend (buybacks vs dilution) and per-share growth cover only the ~4 Yahoo years.
  - **No clean fallback.** Before 2022 the only per-share figure is `diluted_eps`, which mixes split bases (§10).
- **How to fix:**
  1. **Add the count to the EDGAR column mapping.**
     - `diluted_average_shares` ← us-gaap `WeightedAverageNumberOfDilutedSharesOutstanding`;
     - `basic_average_shares` ← `WeightedAverageNumberOfSharesOutstandingBasic`, also the fallback.
     - **Annual rows:** the fact whose period is ~12 months and ends on the fiscal-year end.
     - **Quarterly rows:** the ~3-month fact.
     - **Re-fetch:** EDGAR is fetched once per company, so this needs a one-time re-fetch.
  2. **Find each row's split basis; don't assume it.** EDGAR keeps the latest filing's value, which may already be restated for a later split, and Yahoo rows already sit on today's basis (NVIDIA FY2023: ~25.1B shares).
     - Use the implied-share test of §10: `net_income ÷ diluted_eps` vs today's count.
     - **Tested on NVIDIA:** factor ~39 for FY2017-2019 (as filed, before the 4:1 and 10:1 splits), ~10 for FY2020-2021 (restated for the 4:1 only), 1 from FY2023.
     - Multiply by the factor that matches the product of the later splits in `ohlcv.splits`.
  3. **Store** the result as `diluted_average_shares_adj` and `diluted_eps_adj`, keeping the as-filed columns. This fixes §10 at the root.
  4. **Watch for a change of basis that is not a split:**
     - **Kinds:** a bankruptcy restart (Talen: 0.45M shares in 2021-22, then 56M after 2023); a new listing or SPAC merger; one share class counted in some years and all classes in others (UWM Holdings 93M → 1,600M; Plains GP 239M → 704M).
     - **Test that worked:** the count moves to a level ≥ 3x away and **stays there**, meaning every later year vs every earlier year. Then mark the years before the jump as another basis, so no trend is computed across it. In the 2026-11-06 data this hit 13 companies.
     - **A single far-off year that comes back is a bad value, not a new basis:** Travelers 232M, 23,100M, 228M. Store NULL for that year.
- **Check:**
  ```sql
  -- in financials.db: fill rate per fiscal year (expect values before 2021 once fixed)
  SELECT substr(period_end, 1, 4) AS yr, count(*) AS n,
         round(100.0 * avg(diluted_average_shares IS NOT NULL), 1) AS pct_filled
  FROM financials WHERE freq = 'annual' AND total_revenue > 0 AND period_end >= '2015'
  GROUP BY yr ORDER BY yr;
  -- NVIDIA: one split basis from 2019 on (~24-25e9 shares every year)
  SELECT period_end, diluted_average_shares, diluted_eps FROM financials
  WHERE symbol = 'NVDA' AND freq = 'annual' AND period_end >= '2019' ORDER BY period_end;
  ```

## 15. Dead securities stay `is_active = 1` — `symbols.db`

Found 2026-11-09, re-counted 2026-11-11 (prices 2026-11-06).

- **Made today** (`symbols.md` → "Table `symbols`"; `quotes.md` → "How it is retrieved"):
  - **`is_active`** flips to 0 only "when a fetch returned no data at all and had no errors". Yahoo keeps serving old history for most dead symbols (`ohlcv.md`: "Delisted symbols keep whatever history Yahoo still serves"), so the flip rarely happens.
  - **`is_validated`** for mutual funds, SPACs, warrants and units needs only "a quote". `quotes` rows of symbols no longer fetched "stay with their old values", so that test never fails.
  - **For stocks/ETFs** it needs a price bar within 8 weeks, so a dead stock stays validated for up to 8 weeks.
  - **`in_polygon` = 0** ("likely delisted") is not a usable signal: it is 0 for every mutual fund and ~2k normal stocks that came from the SEC lists.
- **What:** securities that stopped trading keep `is_active = 1` and `is_validated = 1`.
- **Size:** with "no price bar in the 5 trading days before the latest price date" as the test, the active set holds 3,090 mutual funds, 633 warrants, 338 units, 161 SPACs, 116 stocks, 68 ETFs, 14 preferreds, 3 ADRs and 2 closed-end funds that are dead.
- **Impact:** every count, average, ranking and screen includes dead securities, unless each user rebuilds the test.
- **How to fix:**
  1. **After each price fetch**, take the 5th-latest trading day of a market benchmark (`^GSPC`).
  2. **Mark a symbol delisted** when its `last_active_date` is older than that day. Five days, so a one-day halt or a holiday does not count.
  3. **Store** `is_delisted = 1` and `delisted_date` = its last bar date, or set `is_active = 0`.
  4. **Clear the mark automatically** when a new bar arrives.
  5. **Keep the row and its history** (§22).
  - **Trade-off:** a few thinly traded OTC stocks with no trade for a week get marked. They clear themselves on their next trade.
- **Check:**
  ```sql
  -- in symbols.db with ohlcv.db attached: active symbols with no bar in the last 5 trading days (expect ~0)
  ATTACH 'ohlcv.db' AS o;
  WITH d AS (SELECT date FROM o.ohlcv WHERE symbol = '^GSPC' ORDER BY date DESC LIMIT 1 OFFSET 4)
  SELECT security_type, count(*) FROM symbols, d
  WHERE is_active = 1 AND is_validated = 1 AND security_type != 'index'
    AND (last_active_date IS NULL OR last_active_date < d.date)
  GROUP BY security_type;
  ```

## 16. Growth and trend columns break on one odd year — `analysis.db`

- **Made today** (`analysis.md` → "5. Growth", and "6. Dividends" for the `div_` versions): four series — revenue, EPS (split-adjusted, see §10), FCF, book value — each get:
  - `_cagr_1y/3y/5y` (endpoints);
  - `_growth_trend` (log-linear fit over up to 5 annual points);
  - `_growth_vol` (residual volatility around a *linear* fit, % of mean);
  - `_growth_r2`, `_growth_cv`;
  - `revenue_accel` / `eps_accel` = `yoy_q` − `cagr_3y`;
  - and in "3. Profitability": margin `_trend_3y` = 3-year change.
- **What:**
  - **`<x>_cagr_*`** use only the first and last year.
  - **`<x>_growth_trend`** is an ordinary least-squares line on log values over at most 5 points.
  - **`_growth_vol`, `_growth_r2`, `_growth_cv`** are not robust either.
  - **`_trend_3y`** is an endpoint change, and **`_accel`** is built on these.
  - **EPS-based growth** is also inflated by mixed split bases (§10).
- **Evidence:**
  - **Simulated series with a known growth rate** (2,000 per case), median error in percentage points per year:
    - one bad **last** year → CAGR 10.8, ordinary fit 6.3, the robust method described below **0.75**;
    - one bad **middle** year → ordinary fit 1.86, robust 0.38.
  - **Real revenue** (~1,400 companies, predicting the next 3 years without look-ahead): the robust slope gave the lowest error (7.07 pp, vs 8.41 for "last year's growth"). It mainly avoids the big misses.
  - **The FY2020 problem:** about 1 in 5 companies ≥ $2B start a 6-year window at an odd FY2020. That is either the COVID trough or wrong EDGAR revenue (§13). Endpoint math then inflates growth: Capital One 28%/yr vs ~12% without that year, United Airlines 16% vs 8%.
- **How to compute (complete recipe):**
  1. **Series.** Annual values with their real `period_end` dates, last ~6 fiscal years counted back from the newest annual row. Leave out rows known to be bad. **Do not fill missing years** — the method uses real dates, so FY2021 gaps need no filling. Need **≥ 4 points**.
  2. **Scale.** t = `period_end` as a fractional year (2025-12-31 → 2025.997).
     - **Growth series** (revenue, net income, assets, dividends): y = ln(value). All values must be > 0, else NULL.
     - **Level series** (margins, ratios): y = the value itself.
  3. **Slope = Theil-Sen.** Take the median of (y_j − y_i) / (t_j − t_i) over every pair i < j. The intercept is median(y − slope × t).
     - **Growth % per year** = 100 × (e^slope − 1).
     - **Level series:** the slope is in units per year, e.g. margin points per year.
  4. **Direction consistency = Mann-Kendall.**
     - S = Σ sign(y_j − y_i) over all pairs; tau = S ÷ (n(n−1)/2), from −1 to 1.
     - p-value: z = (S − sign(S)) ÷ √Var, with Var = [n(n−1)(2n+5) − Σ over tied groups of c(c−1)(2c+5)] ÷ 18; two-sided p = erfc(|z| ÷ √2).
  5. **Steadiness** (replaces `_growth_vol` / `_cv` / `_r2`) = 1.4826 × the median absolute deviation of the residuals (y − line). In log units for growth series.
  6. **Odd years.** Residual r = y − line.
     - **Outlier:** |r − median(r)| > 4 × max(1.4826 × MAD(r), floor).
     - **Floor:** 0.03 for growth series; 10% of the median |y| for level series. Without a floor, a perfectly smooth series flags every tiny wobble.
     - **Naming:** an outlier at the first or last point is an **edge**; in the middle it is a **spike**.
     - **Step:** a year-to-year change per year (Δy ÷ Δt) that is itself an outlier by the same test, and is not just the way into or out of a spike. Steps are acquisitions and definition changes.
     - **Organic growth:** the Theil-Sen slope over the pairs that do not cross a step, if ≥ 3 such pairs exist.
  7. **First-year rule.** If the first point is an edge, drop it and refit once (as long as ≥ 4 points remain).
     - 2026-11-06 data: 407 of ~2,000 companies ≥ $2B had a first-year edge, mostly FY2020.
     - It changed the growth/income category of 34 companies, 32 of them losing a growth label they had not earned.
  8. **Store per series:** growth (or slope), number of points, span in years, tau, p, steadiness, odd-year flags, organic growth.
  - **Earnings: use total net income, not EPS** (split bases, §10). NULL when any year in the window is a loss.
  - **Per-share growth** = (1 + net income growth) ÷ (1 + share-count growth) − 1. Share-count growth comes from the same method on diluted shares (§14), and is NULL across a change of basis.
  - **Margins:** the level trend describes the past but does not forecast. Next year's margin was closer to "same as this year" (2.10 pp off) than to the trend line (2.94).
  - **Why these settings:** k = 4 and the floors came from a sweep. They flag 0.2% of clean series and find 100% of 2x outliers and ~92% of 1.5x steps. The first try (k = 3, 2% floor) flagged 15% of clean series.
- **Replace:**
  - `_growth_trend` → the robust growth;
  - `_growth_vol` / `_cv` / `_r2` → steadiness + tau;
  - `_trend_3y` / `_accel` → level-trend slope.
  - Keep `_cagr_*` only if it is labelled as endpoint growth.
- **Check:** net-income growth per year should come out near 7% for MNST, 24% for ISRG and 10% for TTEK. The EPS trend shows 77 / 60 / 30.
  ```sql
  -- in analysis.db: today's EPS-based growth (shows ~77 / ~60 / ~30)
  SELECT symbol, eps_growth_trend, eps_cagr_3y, revenue_growth_trend FROM analysis
  WHERE symbol IN ('MNST', 'ISRG', 'TTEK');
  -- in financials.db: the net income the robust growth should be computed from instead
  SELECT symbol, period_end, net_income FROM financials
  WHERE symbol IN ('MNST', 'ISRG', 'TTEK') AND freq = 'annual' AND net_income IS NOT NULL
    AND period_end >= '2019' ORDER BY symbol, period_end;
  ```

## 17. Fair-value models are built on the weak growth — `analysis.db`

- **Made today** (`analysis.md` → "8. Intrinsic value"):
  - **Lynch** = EPS × min(EPS trend growth %, 25).
  - **DCF** grows TTM FCF at `fcf_growth_trend` (capped at 35%), fading to 2.5% terminal growth, discounted at WACC.
  - **DDM:** the same structure on `div_rate_ttm` with `div_growth_trend`.
  - **`fair_value`** = the median of the positive models suited to the `screen_type`.
  - **Bear/bull scenarios:** growth ∓ `growth_vol`.
  - **`peg`** (`analysis.md` → "2. Valuation") = `pe` ÷ `eps_growth_trend`.
- **What:** every intrinsic value inherits the growth problems of §9, §10 and §16.
  - **`intrinsic_value_lynch`** = EPS × the EPS trend growth, which is split-inflated (Monster 77%/yr; the 25% cap limits but doesn't remove it).
  - **`intrinsic_value_dcf`** grows FCF at `fcf_growth_trend` (ordinary fit).
  - **`intrinsic_value_ddm`** uses `div_rate_ttm` (specials included) and `div_growth_trend` (built on the highest single payment per year, §9).
  - **The bear/bull scenarios** shift growth by the non-robust `growth_vol`.
  - **What is built on these:** `fair_value` is their median, `margin_of_safety` comes from it, and `peg` = P/E ÷ EPS trend growth.
- **Evidence:** 6.7% of stocks/ADRs with a fair value show it above 5x the price (2026-11-06 data; extremes GMEX 2,871x, CISS, PARA, CABO ~400x). The 75th percentile is ~1.45x. Monster's `peg` shows 0.55 at a P/E of 42.
- **How to fix:**
  1. **Lynch:** use per-share net-income growth (§16), not the EPS trend.
  2. **DCF:** use the robust FCF growth (§16) when every FCF year is > 0. Otherwise grow FCF with robust revenue growth at the latest FCF margin, or skip the DCF.
  3. **DDM:** use regular-dividend growth with specials removed (§21).
  4. **PEG** = P/E ÷ per-share net-income growth.
  5. **Bear/bull:** growth ∓ the robust steadiness, converted to % per year, instead of `growth_vol`.
  6. **Flag fair values above 3x the price** as model tails, not bargains.
- **Check:** after the fix, the share of fair values > 3x the price should drop sharply, and Monster's PEG should be ~4-6, not 0.55.
  ```sql
  -- in analysis.db: stocks / ADRs with a fair value above 3x and 5x the price (expect far fewer once fixed)
  SELECT count(*) AS with_fair_value, sum(fair_value > 3 * price) AS above_3x, sum(fair_value > 5 * price) AS above_5x
  FROM analysis WHERE fair_value > 0 AND price > 0 AND security_type IN ('stock', 'adr');
  -- in analysis.db: Monster's PEG (expect ~4-6 once fixed; shows ~0.55)
  SELECT symbol, pe, peg, eps_growth_trend, intrinsic_value_lynch, fair_value, price FROM analysis WHERE symbol = 'MNST';
  ```

## 18. Scores are always filled and can't be compared between runs — `analysis.db`

- **Made today** (`analysis.md` → "11. Rankings and scores", "Known limitations"):
  - **Goodness** = a 0-100 rule per metric, of three kinds: **peer** rules (percentile within the industry if ≥ 5 members, else sector, else the whole table), **universe** rules (percentile across the whole table) and **absolute** rules.
  - **Category scores** = the weighted average of the available goodness values, with weights renormalized. `overall_score` renormalizes over the categories present.
  - **`rs_rank`** is a percentile within the same `security_type`.
  - **The `.md` itself says:** "Percentiles depend on the universe … not comparable across runs."
- **What:**
  - **Always filled.** A score from 2 inputs looks the same as one from 30. `overall_score` is filled for 95.6% of rows, including funds that only have momentum and income.
  - **Not comparable between runs.** The peer and universe percentiles are taken over every row in the table on that run (OTC, micro caps and recently dead symbols included, §15). They move between refreshes even when the company didn't change. `rs_rank` is within its security type, but includes OTC and dead symbols too.
  - **Polluted inputs.** Some inputs are the problem columns of §16 and §17 (`peg`, `margin_of_safety`, growth trends).
  - **High analyst forecasts are rewarded.** `growth_score` counts `forward_eps_growth` (weight 0.5) as "higher = better", and `value_score` uses `forward_pe`. Research says the opposite holds at the top: stocks with the **highest** growth forecasts tend to do **worse**, because forecasts for fast growers are too optimistic and get cut later (La Porta, *Journal of Finance* 1996; Bordalo, Gennaioli, La Porta and Shleifer, *Journal of Finance* 2019). Forecast bias also grows with the horizon, and revenue forecasts are less biased than earnings forecasts.
- **How to fix:**
  1. **Count the inputs.** Store `<category>_inputs` (used / possible) next to each score. Store NULL when less than about half the category's weight is present.
  2. **Rank against a fixed, documented universe:**
     - stocks + ADRs ranked separately from funds;
     - live (§15), exchange-listed (no OTC), one ticker per company;
     - store the universe size with each run.
  3. **Rebuild the scores after §16 and §17**, so they no longer use the broken inputs.
  4. **Use forecasts one way only.** A forecast *below* the past growth rate pulls growth down (tested downstream: halfway between the past rate and the forecast); a forecast *above* it adds nothing. Prefer the revenue forecast over the EPS forecast, and 1-2 year horizons over long-term growth.
  5. **Validate before treating scores as signals.** For example, compare later returns per score decile across past refreshes. Not done yet — the scores' accuracy is unconfirmed.
- **Check:**
  ```sql
  -- in analysis.db: scores filled per security type (expect NULL where too few inputs once fixed)
  SELECT security_type, count(*) AS n, round(100.0 * avg(overall_score IS NOT NULL), 1) AS pct_overall_filled
  FROM analysis GROUP BY security_type ORDER BY n DESC;
  ```

## 19. Smaller metric fixes — `analysis.db`

- **Made today:**
  - `analysis.md` → "4. Financial health": `quick_health_score`.
  - "10. Peer comparison": the median "over all rows in the table with that sector/industry tag", NULL below 3 values.
  - "2. Valuation": "negative multiples are kept here … peer comparisons and scores ignore non-positive … debt ratios".
  - "11. Rankings": `rs_rank`.
- **`quick_health_score` is not Piotroski.** It is a home-made 7-check score (revenue, cost, gross profit, assets vs liabilities, cash ratio, OCF up), but the name invites reading it as the F-Score.
  - **Add the real `piotroski_f` (0-9)** from the two newest annual rows: this year (a) and the year before (b), 300-430 days apart.
  - **The 9 tests, one point each:**
    1. ROA_a > 0 (ROA = net income ÷ total assets);
    2. operating cash flow_a > 0;
    3. ROA_a > ROA_b;
    4. operating cash flow_a > net income_a;
    5. long-term debt ÷ total assets: a ≤ b (missing debt = 0);
    6. current ratio: a > b;
    7. `ordinary_shares_number`: a ≤ b (no dilution);
    8. gross margin: a > b;
    9. revenue ÷ total assets: a > b.
  - **Store NULL** unless all nine are computable. Total assets, current liabilities and revenue must be > 0.
- **`<metric>_vs_sector` / `_vs_industry` medians cover every row** (OTC, micro caps).
  - **Fix:** take the median over the same fixed universe as §18. Need at least 5 companies in the group, else fall back to the sector.
  - **Also add the health metrics:** debt/EBITDA, interest coverage, margins.
- **`debt_to_ebitda` is negative when EBITDA ≤ 0.** 183 stocks/ADRs ≥ $2B show it, and it reads as "very little debt". Keeping it negative is a deliberate convention (`analysis.md` → "2. Valuation"), and the scores already skip it — but a reader or a simple filter (`debt_to_ebitda < 3`) does not.
  - **Fix:** store NULL plus a flag such as `ebitda_negative`, or keep the value and add the flag.
  - **Same for every ratio with a negative denominator**, e.g. EV/EBITDA and net debt/EBITDA.
- **`rs_rank` is relative to all stocks only.** Add relative strength against the stock's own industry (§21).
- **Check:**
  ```sql
  -- in analysis.db: negative debt / EBITDA among stocks / ADRs >= $2B (expect 0 once NULL or flagged)
  SELECT count(*) FROM analysis
  WHERE debt_to_ebitda < 0 AND market_cap >= 2e9 AND security_type IN ('stock', 'adr');
  -- in analysis.db: no real Piotroski column yet (expect a piotroski_f column once added)
  SELECT symbol, quick_health_score FROM analysis WHERE symbol IN ('MSFT', 'AAPL');
  ```

## 20. Smaller data items — several files

**Made today:** `ohlcv`, `quotes` and `signals` all store what Yahoo returns, with little or no validation (`ohlcv.md`, `quotes.md`, `signals.md` → "How it is retrieved"). Yahoo's ratios and EBITDA are used as-is or as a fallback in `analysis.db` (`quotes.md` → "How it is used"). So each fix below is either a cleaning rule at import or a recomputation in `analysis.db`.

- **`ohlcv.db` — gaps and stale runs.** (`ohlcv.md`: "only real trading days … no gap filling", and "some Yahoo histories have gaps".)
  - **What:** ~90 symbols miss a month or more of bars. ~1,600 stocks show 30+ identical closes in a row, mostly rarely traded OTC: the price was carried, not traded.
  - **Fix:** flag runs of an identical close with zero volume as non-trading days, and record known gaps, so volatility and momentum can skip them.
- **`signals.db` — `surprise_percent` is meaningless near a zero estimate.**
  - **What:** ~300 companies show surprises over 1,000% (MBI: estimate −$0.03 → "+3,533%").
  - **Fix:** keep the raw row, but in `analysis.db` ("9. Analyst…") leave quarters with |estimate| < $0.05 out of `earnings_surprise_avg` / `_last`, and base `earnings_beat_rate` on `eps_difference`.
- **`signals.db` / `quotes.db` — estimate currency ≠ statement currency.**
  - **What:** ~245 companies (Eni: USD estimates, EUR statements). YPF in §7 is the extreme case.
  - **Fix:** store a currency per value and convert to one, or at least a mismatch flag.
- **`signals.db` — insider holdings above shares outstanding.**
  - **What:** ~110 companies, mostly multi-class (LEN-B).
  - **Fix:** compare against shares across all classes.
- **`quotes.db` — analyst targets far from the price.**
  - **What:** ~9% are above 5x or below 0.2x the price, mostly penny stocks. Some look like a reverse split not applied to the target (SBFM: target $1,120 vs price $2.71).
  - **Fix:** adjust targets by splits after the target date, and flag what remains.
- **`quotes.db` — `enterpriseValue` doesn't add up** for ~10% (Yahoo's own debt and cash definitions).
  - **Fix:** `analysis.db` already computes its own EV ("2. Valuation": market cap + total debt − cash). Extend that formula with minority interest and preferred equity, use the all-class market cap (§2), and use it wherever Yahoo's `enterpriseValue` / `enterpriseToEbitda` serve as a fallback.
- **`financials.db` — Yahoo EBITDA below its own parts.**
  - **What:** Welltower 2.31B, vs net income + D&A + interest = 3.73B.
  - **Fix:** check EBITDA ≥ operating income + D&A, roughly, and recompute from the parts when it fails.
- **Check:**
  ```sql
  -- in signals.db: surprises over 1,000% (the stored value is a fraction: 10 = 1,000%)
  SELECT count(*), count(DISTINCT symbol) FROM earnings_surprise WHERE abs(surprise_percent) > 10;
  -- in signals.db with quotes.db attached: estimate currency different from statement currency
  ATTACH 'quotes.db' AS q;
  SELECT count(DISTINCT e.symbol) FROM estimates e JOIN q.quotes qq USING (symbol)
  WHERE e.horizon = '0y' AND e.currency IS NOT NULL AND qq.financialCurrency IS NOT NULL
    AND e.currency != qq.financialCurrency;
  -- in quotes.db: analyst targets above 5x or below 0.2x the price (~9% of those with a target)
  SELECT sum(targetMeanPrice > 5 * currentPrice OR targetMeanPrice < 0.2 * currentPrice) AS far_targets,
         count(targetMeanPrice) AS with_target FROM quotes WHERE currentPrice > 0;
  ```

## 21. Calculations worth adding — `analysis.db`

Each was built and tested downstream on the 2026-11-06 data. The methods below are complete. Where each would go in `analysis.md`:
- **Health check, Peer Strength, peak check → "4. Financial health".**
- **Category and growth phase labels → "5. Growth"** (or a new "Labels" section).
- **Dividends → "6. Dividends".**
- **REIT payout → "6. Dividends"**, next to `div_payout_ratio`.
- **Relative strength vs the industry → "11. Rankings"**, next to `rs_rank`.
- **Its benchmark map** — a small new table, e.g. `industry_benchmark` (sector, industry, ETF, fit), rebuilt now and then.

- **Regular dividends (specials removed).**
  - **Special payment:**
    - **Test:** a payment more than 2.5x the median of the **lower half** of all payments within ±400 days of it, and **isolated** (the payments before and after are not also that high).
    - **Why the lower half:** frequent specials can't raise the yardstick (W.R. Berkley).
    - **Why ±400 days:** a fast grower's recent raises aren't mistaken for specials.
    - **Why isolated:** two high payments in a row are a new normal, not a special. Example: STAG went from $0.124 monthly to $0.388 quarterly.
  - **Drop it, with one exception:** if dropping it leaves a gap longer than 1.5x the normal spacing, it was regular + special in one payment (CME's $7.45 = $1.30 regular + special). Keep it at the regular level instead.
  - **`reg_yield`** = regular payments in the last 12 months ÷ the last close.
  - **`reg_div_growth`** = robust growth (§16) of the six trailing-12-month regular totals. NULL if any of those years had none.
  - Dillard's shows a 5.2% TTM yield in the data, almost all specials.
- **`years_paid`** — unbroken calendar years with any dividend, counted back from the last full year. This is what "income stock" questions need. The no-cut streak is a different question (§9).
- **`recent_cut`** — needs at least 5 regular payments in the last 3 years.
  - **Rate now** = latest regular payment × payments per year. Payments per year = 365 ÷ the median spacing of the last 5 payments.
  - **Rate before** = median payment 1-2 years ago × its payments per year.
  - **Cut** when the rate now is below 92% of the rate before **and** the last 12 months' total is below 95% of the 12 months before. Both tests, so a shifted payment date or a switch to monthly payments (UDR) does not read as a cut.
  - **Catches** Alexandria (Dec 2025, −45%) and Whirlpool (Aug 2025, −49%).
- **REIT payout on FFO.**
  - FFO ≈ net income + depreciation & amortization − gains on property sales. Payout = dividends paid ÷ FFO.
  - **Rough:** the gain line is filled for only ~26% of REITs, so FFO errs high.
    - **Gains outside the property line:** Public Storage's FY2022 net income ($4.35B) holds a **$2.1B gain on the sale of its equity investment in PS Business Parks** (FY2022 10-K) — not a property sale, so `gain_on_sale_of_ppe` is 0 → "FFO" 5.24B vs ~3.1-3.3B in the years around it. NAREIT FFO excludes gains on sales of real estate *and* of investments in real-estate entities, so subtract `gain_on_sale_of_security` / `gain_on_sale_of_business` too where the company is a REIT.
    - **Write-downs:** Alexandria's FY2025 impairments push net income to −$1.43B → "FFO" −0.12B, though its operating cash flow was $1.41B.
  - **Better:** real-estate depreciation only, plus impairments, or NAREIT FFO as reported in the filings (EDGAR has no standard FFO tag; it is in each REIT's earnings release and 10-K MD&A). Payout on net income fails REITs wrongly.
  - **Until then, for growth over time:** operating cash flow per share is a cleaner stand-in than this rough FFO — steady for both cases above.
- **Relative strength vs the industry.**
  - **Measure:** the stock's total return (`adj_close`) over 1 and 3 years minus its industry benchmark ETF's return.
  - **Choosing the benchmark:**
    - **Yardstick:** an equal-weighted basket of the industry's 10 largest listed members, daily returns over 3 years.
    - **Candidates:** ETFs that target the industry or theme (no generic style or size funds).
    - **Qualifies:** correlation with the yardstick ≥ 0.70 **and** ≥ 0.05 above the sector's SPDR ETF.
    - **Ties:** the longer price history wins.
    - **Fallback:** the sector SPDR, flagged as a weak fit (~40 niche industries, e.g. Tobacco, Education, IT Services).
  - **Optional residual momentum:**
    - beta from weekly log returns over 3 years against the ETF;
    - residual 1-year return = stock 1-year log return − beta × ETF 1-year log return;
    - score = residual ÷ annualised residual volatility.
- **Peer strength for banks, insurers, REITs and brokers** (would go in `analysis.md` → "11. Rankings", as a replacement for the excluded scores on these types; the current scores use one metric set for every type).
  - **Scoring:**
    - For each metric, take the company's percentile (0-100) within its peers, flipped where lower is better. Strength = the mean of the percentiles present.
    - **Needs ≥ 2/3 of the type's metrics**, else NULL.
    - **Peers:** the same Yahoo industry when it holds ≥ 8 companies of that type, otherwise every company of the type.
    - **Universe:** live, exchange-listed, ≥ $2B, one ticker per company. Mortgage REITs are left out: they are lenders, so EBITDA means nothing for them.
  - **Bank** (higher = stronger unless noted):
    - ROA = net income ÷ total assets, and ROTCE = net income ÷ `tangible_book_value`, each the median of the newest 3 fiscal years;
    - pre-tax margin = pre-tax income ÷ total revenue (3-year median) — use the efficiency ratio instead once §23 is fixed;
    - tangible equity = `tangible_book_value` ÷ (total assets − goodwill and intangibles);
    - tangible book per share growth.
  - **Insurer:**
    - pre-tax margin (3-year median);
    - debt ÷ (debt + equity) — lower is stronger;
    - interest coverage = (pre-tax income + interest) ÷ interest. No interest counts as best **only if pre-tax income > 0**; otherwise NULL.
    - std-dev of yearly ROE over ≤ 10 years, needs ≥ 5 — lower is stronger;
    - book value per share growth.
  - **REIT:**
    - EBITDA margin (3-year median);
    - debt ÷ EBITDA — lower is stronger; EBITDA ≤ 0 = worst;
    - EBITDA ÷ interest — EBITDA ≤ 0 = 0; no interest = best;
    - operating cash flow per share growth (not the rough FFO above).
  - **Broker** (Yahoo's "Capital Markets", "Financial Conglomerates" and "Mortgage Finance": brokers, investment banks, advisory firms, mortgage lenders — and the bitcoin miners Yahoo files under "Capital Markets"):
    - ROTCE (3-year median);
    - pre-tax margin (3-year median);
    - std-dev of yearly ROE — lower is stronger;
    - tangible book per share growth.
    - These work whatever the capital structure: customer balances (Schwab, Interactive Brokers), partner equity (PJT, Lazard), advisory firms with little balance sheet. Mortgage lenders belong here, not with banks: the bank tangible-equity test counts their main asset (servicing rights) as intangible.
  - **Broker gate:** FAIL when equity ≤ 0 or a net loss in ≥ 2 of the last 3 years; warn when the latest year is a loss. A missing equity figure = "can't judge", not FAIL.
  - **Mortgage REITs: a gate, no peer score** (too few to rank — 5 at ≥ $2B):
    - **economic return** over up to 3 years = (change in common book value per share + dividends per share paid in between) ÷ starting book value per share. Book value per share = `common_stock_equity` ÷ year-end `ordinary_shares_number`; dividends from the actual payments in `ohlcv`.
      - The figures match what the companies report: Annaly $20.79 / $19.44 / $19.15 for 2022-2024. All equity over average shares instead put AGNC at $14.66 vs its ~$9.84, because of preferred stock.
    - **Rules:** warn when the economic return is < 0% or when dividends paid exceed net income over 3 years; FAIL when the economic return is below −20%.
    - **No leverage line:** liabilities ÷ equity is 5-8x for agency mREITs but 8-11x where statements consolidate securitisation trusts (Starwood). Yahoo's `total_debt` also leaves out repo borrowings (AGNC 0.1B).
  - **Per-share growth:** the robust growth of §16 (with the first-year rule) on value ÷ diluted average shares over ~6 years. NULL with < 4 points, or when the share count changes basis (§14).
  - **Tested downstream** against later stock risk: scores built from FY2022 or FY2023 statements gave the weaker score to the stock that was riskier afterwards (peer-adjusted volatility) in ~70% of pairs, against 50% for chance (p < 0.001). Brokers alone: 83-88% (p ≈ 0.01, small samples of 14-18).
  - **Tried and dropped, each useless in three samples:** bank ROA steadiness, insurer ROE, REIT dividend ÷ operating cash flow.
- **Health check (danger gate) for every company** — ok / warn / FAIL, judged on the latest clean fiscal year. Would go in `analysis.md` → "4. Financial health".
  - **Company type first,** from the Yahoo industry: utility (Utilities - Regulated Electric / Gas / Water, Utilities - Diversified), telecom (Telecom Services), bank (Banks - …), insurer (Insurance - …), REIT, mortgage REIT (REIT - Mortgage), broker (Capital Markets, Financial Conglomerates, Mortgage Finance), shell (Shell Companies), else ordinary.
  - **Ordinary companies — lines set by how steady the industry's profits are** (the S&P credit-rating idea):
    - **Steadiness** = the standard deviation of a company's yearly operating margin (up to 10 years, at least 5); the industry's median of that; industries split into thirds: steady / normal / swinging.
    - **Lines** (debt ÷ EBITDA at most, interest coverage at least): steady 5.0x / 2.0x · normal 3.5x / 3.0x · swinging 2.5x / 4.0x · utilities 6.5x / 2.0x · telecoms 4.0x / 2.5x (from rating-agency benchmarks).
    - **FAIL:** interest coverage below the line, or an operating loss. **Warn:** only debt ÷ EBITDA above the line (interest is paid; the debt may be the business model, e.g. a franchisor or a finance arm). EBITDA ≤ 0 with debt counts as the worst debt ratio.
  - **REIT:** FAIL when EBITDA ≤ 0 or EBITDA ÷ interest < 2x; warn when debt ÷ EBITDA > 6.5x or EBITDA ÷ interest < 3x (based on Moody's REIT downgrade triggers of 6.5x and 3.5x).
  - **Insurer:** FAIL when equity ≤ 0, a net loss in 2 of the last 3 years, or (pre-tax income + interest) ÷ interest < 2x; warn when debt > 35% of (debt + equity), coverage < 4x, or the latest year is a loss.
  - **Bank (warn only — loan quality and capital ratios are not in the data):** tangible equity < 4% of tangible assets, ROA < 0.5%, or a loss.
  - **Brokers and mortgage REITs:** the gates described above.
  - **A missing figure means "can't judge", never FAIL.**
  - **Tested downstream** against stock risk (price volatility, drawdown): the gate put the riskier stock on the worse label in ~64% of pairs (50% = chance); 62-63% for REITs, insurers and banks.
- **Peer Strength for ordinary companies, utilities and telecoms** — the same scoring as for the financial types above, with these measures (higher = stronger unless noted):
  - return on invested capital;
  - cash conversion: operating cash flow ÷ net income, 3-year average;
  - margin steadiness: standard deviation of yearly operating margin, up to 10 years (lower = stronger);
  - debt ÷ EBITDA (lower = stronger; EBITDA ≤ 0 with debt = worst);
  - interest coverage (no interest to pay = best);
  - the real Piotroski score (§19).
  - **Peers:** the industry when it holds ≥ 8 companies, otherwise the sector. Needs at least 4 of the 6 measures.
- **Cyclical peak check** (the "watch" mark) — a profit margin far above its own normal, which tends to fade over years. For ordinary companies, utilities and telecoms (a bank's or insurer's "margin" means something else).
  - **Margin:** the latest yearly operating margin; the pre-tax margin when operating income has fewer than 5 earlier years (some miners' SEC history lacks it).
  - **Normal:** the median of up to 10 earlier years. **Trend line:** a Theil-Sen line (§16) through those earlier years.
  - **Peak** when all three hold: margin − normal > 15 points, margin > 1.5 × normal, and margin − trend line > 15 points. The last test keeps out a margin that has climbed steadily for years.
  - **Tested downstream** on 223 past peaks: after 1 year about 11% of the gap to normal was given back, after 2 years about 29%. So it marks profits that may not last, not a coming collapse.
- **Category labels** — describe every company as growth / income / both / early / neither:
  - **growth:** the average of past revenue-per-share growth (§16) and the analysts' 1-2 year revenue forecast is at least 12% a year (either alone when the other is missing);
  - **income:** regular dividend yield (specials removed, above) at least 2.5%, with a dividend in each of the last 5 calendar years;
  - **both** when both hold;
  - **early:** a loss in the latest fiscal year and in more than half of the last up to 3 years, with revenue under $1 billion — replaces growth / neither (a big forecast from a tiny base is not an established grower);
  - **neither** otherwise.
  - **Tested downstream** against professional fund holdings: 74% of the Russell 1000 Growth-only stocks came out growth (plus 9% early); 70% of the Schwab US Dividend Equity ETF's holdings came out income.
- **Growth phase labels** — two parts, each a number and a word:
  - **Growth so far** = past revenue-per-share growth plus the track record behind it:
    - **long-running:** ≥ 8 years of revenue, robust growth ≥ 10% a year, rising in most years (Mann-Kendall tau ≥ 0.6);
    - **picked up:** a long record, but the recent rate is ≥ 5 points above the long-run rate;
    - **moderate:** a long record with slower long-run growth;
    - **recently listed:** trading < 6 years — a young company, a spin-off of an older business or an old company newly listed (the data can't tell which);
    - **short record:** an older company with < 8 years of statements in the data.
  - **Growth ahead** = the analysts' forecast compared with the past rate: starting (past < 5%, forecast ≥ 12%), speeding up, steady, slowing, slowing sharply (forecast below half the past rate). A change needs ≥ 5 points *and* ≥ 25% of the past rate.
  - Track record from the **full** revenue history (up to 12 years), not a short window — with gaps like the FY2021 hole (§1), a 6-year window can hold only 3 years.
- **"Dividend outgrowing earnings" warning** — regular-dividend growth more than 5 points a year above net-income growth, **and** either a payout ≥ 50% or shrinking earnings. A rising payout, not real growth (HP and Qualcomm raising dividends while earnings fell).
- **Also in this list:** robust trends and per-share growth (§16), the real Piotroski score (§19), the delisted flag (§15).

## 22. Data not collected yet

- **Filing dates on statements.**
  - **Made today:** `financials.md` → "Known limitations": "No filing date. Only `period_end` is stored."
  - **What:** `financials` has no filed date, so a backtest must guess when numbers became public (~45 days after `period_end` is the usual assumption).
  - **How:** the companyfacts API the EDGAR backfill already uses returns `filed` and the accession number (`accn`) with every fact, so the data is already in each response — it only needs storing. Store `filed_date` per row (the first filing for that period) and keep amendments apart. For Yahoo rows, take the date from the EDGAR filing of the same period.
- **History of delisted companies.**
  - **Made today:**
    - `symbols.md` → "Table `symbols`": discovery reads Polygon's **active** tickers only, plus the SEC's current lists. A company delisted before the system first ran is never added. Rows are never deleted, so later delistings do stay.
    - `ohlcv.md`: "Delisted symbols keep whatever history Yahoo still serves". `indices.md` → "Known limitations" names the survivorship bias.
  - **What:** companies that stopped trading before discovery are missing, so the history holds mostly survivors and old returns look better than they were.
  - **How:**
    - **Find them:** add Polygon's inactive tickers (its ticker list can be asked for `active=false`) and the SEC filer list (CIKs with old 10-K filings) to discovery.
    - **Store them:** keep their prices and statements with `delisted_date` (§15). Where known, also store the reason (merger, bankruptcy, going private) and, for a merger, the cash/stock terms, so the final return can be computed.
- **Check:**
  ```sql
  -- in financials.db: a filing-date column (expect a row once added; none today)
  SELECT name FROM pragma_table_info('financials') WHERE name LIKE '%filed%';
  -- in symbols.db: stocks whose last price is from before 2016 - i.e. long-gone companies kept in
  -- the universe (expect thousands once delisted history is collected; 0 on 2026-11-11)
  SELECT count(*) FROM symbols WHERE security_type = 'stock' AND last_active_date < '2016-01-01';
  ```

## 23. Banks have no total non-interest expense — `financials.db`

Found 2026-11-11 (prices 2026-11-06), while building a bank strength score: the bank **efficiency ratio** (non-interest expense ÷ revenue, the standard bank cost measure) can't be computed.

- **Made today** (`financials.md` → "Source 1", "Source 2"):
  - **Yahoo:** "all ~350 Yahoo line items are kept", but for banks Yahoo returns only some cost lines (`general_and_administrative_expense`, `selling_and_marketing_expense`, `professional_expense_and_contract_services_expense`, `other_non_interest_expense`), not their total.
  - **EDGAR:** "a curated subset of columns", and no expense total for banks is in it.
- **What:**
  - **Of 534 banks** (latest annual row), only 41 have `operating_expense`. The rest have only the pieces.
  - **The pieces don't add up to the total.** JPMorgan FY2025: the filled lines sum to **$79.55B**, while its annual report gives total non-interest expense of **$95.64B**. Occupancy, technology and other lines are missing.
- **How to fix:**
  1. **Add a `non_interest_expense` column**, filled from EDGAR us-gaap `NoninterestExpense` (the full-year fact for annual rows, the ~3-month fact for quarterly). Banks report it on every income statement.
  2. **For Yahoo-era rows,** fill it from the same EDGAR fact for the same period (EDGAR is additive only, so this needs the new column to be written even where Yahoo owns the row), or from Yahoo's own total where Yahoo returns one.
  3. **Then add `efficiency_ratio`** to `analysis.db` ("4. Financial health") = `non_interest_expense` ÷ `total_revenue` × 100 for `screen_type = 'bank'`. Yahoo's bank `total_revenue` is already net interest income + non-interest income. Lower = better; ~55-65% is typical.
  - **Needs a one-time EDGAR re-fetch** (EDGAR is fetched once per company).
- **Check:**
  ```sql
  -- in financials.db: JPMorgan FY2025 - filled lines sum to ~7.96e10; expect a total of ~9.564e10 once fixed
  SELECT symbol, period_end, operating_expense,
         general_and_administrative_expense + selling_and_marketing_expense
         + professional_expense_and_contract_services_expense + other_non_interest_expense AS filled_lines_sum
  FROM financials WHERE symbol = 'JPM' AND freq = 'annual' AND period_end = '2025-12-31';
  -- in financials.db with quotes.db attached: banks with any total expense on their latest annual row
  ATTACH 'quotes.db' AS q;
  SELECT count(*) AS banks, sum(f.operating_expense IS NOT NULL) AS with_total
  FROM financials f JOIN q.quotes qq ON qq.symbol = f.symbol
  WHERE f.freq = 'annual' AND qq.industry LIKE 'Banks%'
    AND f.period_end = (SELECT max(period_end) FROM financials g WHERE g.symbol = f.symbol
                        AND g.freq = 'annual' AND g.net_income IS NOT NULL);
  ```

## 24. Stockholders' equity stored as 0 for partnerships — `financials.db`, and ratios on it — `analysis.db`

Found 2026-11-11 (prices 2026-11-06): a broker health check read Lincoln International's equity as "≤ 0" and failed it.

- **Made today:**
  - `financials.md` → "Source 1": Yahoo's line items are kept as returned.
  - `symbols.md` → "Conventions": NULL means "not applicable", never zero.
  - `analysis.md` → "3. Profitability", "2. Valuation": ROE, P/B and debt/equity divide by `stockholders_equity`.
- **What:**
  - **Zero instead of a value.** Yahoo returns `stockholders_equity = 0` for partnerships, whose equity is "partners' capital", and for some new listings, while `common_stock_equity` (and `total_equity_gross_minority_interest`) hold the real figure.
  - **Size:** 236 annual rows (77 companies) and 228 quarterly rows (53), all from Yahoo.
  - **Examples:** Brookfield Renewable (BEP), Brookfield Infrastructure (BIP), Western Midstream (WES), XPLR Infrastructure (XIFR), Dynagas (DLNG); Lincoln International: 0.0 vs common equity $431M.
  - **Impact in `analysis.db`:** `roe`, `pb` and `debt_to_equity` are NULL for these companies — the equity figure exists, but every ratio on it is blank.
- **How to fix:** add a rule to the Yahoo import.
  - **When** `stockholders_equity` is 0 and `common_stock_equity` > 0: store `common_stock_equity` as `stockholders_equity`.
  - **When neither is usable:** store NULL, not 0.
  - **Then rebuild `analysis.db`**, so the equity-based ratios fill in.
- **Check:**
  ```sql
  -- in financials.db: zero equity with a positive common equity (expect 0 rows once fixed)
  SELECT source, freq, count(*) AS row_count, count(DISTINCT symbol) AS symbols
  FROM financials WHERE stockholders_equity = 0 AND common_stock_equity > 0 GROUP BY source, freq;
  -- in analysis.db: ratios blank for the examples (expect values once fixed)
  SELECT symbol, roe, pb, debt_to_equity FROM analysis WHERE symbol IN ('WES', 'BEP', 'BIP', 'XIFR', 'DLNG');
  ```

## 25. Company identity and industry tags — `symbols.db`, `quotes.db`

Found 2026-11-11 (prices 2026-11-06), building peer groups and reading the lists.

- **Made today:**
  - `symbols.md` → "Table `symbols`": `cik` is filled for "EDGAR rows only; stored as a float".
  - `quotes.md` → "Main Yahoo field groups": `sector` / `industry` are "Yahoo's taxonomy — the sector/industry used in every other database". `analysis.md` → "1. Identity": `screen_type` (bank / insurance / REIT) comes from the industry name.
- **What:**
  - **No company identifier for most symbols.** Only **1,804 of 7,330** live stocks and ADRs have a CIK. Without it, share classes (GOOG / GOOGL) and ADR + "F" share pairs (DTEGY / DTEGF) can only be grouped by name, which misses the ADR pairs.
  - **Some industry tags put companies in the wrong peer group:**
    - bitcoin miners as "Capital Markets" (Riot, MARA, TeraWulf);
    - self-storage REITs as "REIT - Industrial" (Public Storage, CubeSmart, Extra Space);
    - Sun Life as "Insurance - Diversified", next to mostly property-and-casualty insurers, while its closest peer Manulife is "Insurance - Life".
  - **Impact:** every peer comparison (`_vs_industry`, scores, Peer Strength), the industry benchmark, and the bank / insurer / REIT routing of `screen_type`.
  - **Also:** 133 live stocks / ADRs have no `longName` but do have a `shortName` (CubeSmart).
- **How to fix:**
  1. **CIK for every listed company.** Map each ticker through the SEC's `company_tickers.json` (already a discovery source) and store `cik` as an integer.
  2. **A `company_id`** = the CIK, so share classes and ADR / F-share pairs group without name matching.
  3. **SEC industry code as a second opinion.** The EDGAR submissions API (`data.sec.gov/submissions/CIK##########.json`) gives each filer's `sic` and `sicDescription`; store them and flag companies whose SIC and Yahoo industry disagree clearly.
  4. **A small, maintained override table** (symbol → industry) for confirmed misfits, applied before `screen_type` and every peer group.
  5. **Names:** fall back to `shortName` when `longName` is empty.
- **Check:**
  ```sql
  -- in symbols.db with quotes.db attached: live stocks / ADRs with a CIK (expect nearly all once fixed)
  ATTACH 'quotes.db' AS q;
  SELECT count(*) AS n, sum(s.cik IS NOT NULL) AS with_cik FROM symbols s JOIN q.quotes qq USING (symbol)
  WHERE s.is_active = 1 AND s.is_validated = 1 AND s.security_type IN ('stock', 'adr');
  -- in quotes.db: the misfiled examples
  SELECT symbol, industry FROM quotes WHERE symbol IN ('RIOT', 'MARA', 'WULF', 'PSA', 'CUBE', 'EXR', 'SLF', 'MFC');
  ```

## Known gaps / limitations

- Counts are from one snapshot; they drift with each refresh.
- Examples were checked by eye or against SEC / BLS / Fed sources where stated; the rest are measured patterns, not item-by-item proof.
- Yahoo-to-Yahoo comparisons (quotes vs ohlcv vs signals) are not independent checks.
