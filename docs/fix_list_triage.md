# Fix list triage

Internal tracker for working through `docs/fix_list_for_data_system.md` on branch `feature/data-system-fixes`. Results for the reviewer go in `docs/fix_list_for_data_system_done.md`.

## Waiting for your input

- **Group H part 4 (relative strength vs the industry) done 2026-11-22:** the analysis run checked. One fix after it (3-year values missed price flags older than 2 years); your next run applies it. Then: the revenue-per-share seams fix, Group I.
- **Possible follow-ups from H part 4** (not started, your call): residual (beta-adjusted) momentum; a stickiness rule for near-equal ETFs; RS vs industry in the momentum score.
- **Group H part 3c (REIT payout on FFO) done 2026-11-21:** the analysis run checked (stored = test build).
- **Possible follow-ups from H part 3c** (not started, your call): score REITs on `ffo_payout` instead of the net-income payout (REIT band 80-95%); P/FFO for REITs; `div_outgrowing_earnings` still uses net-income growth for REITs.
- **Group H part 3b (growth phase) done 2026-11-21:** the analysis run checked (stored = test build).
- **NEW, important — revenue-per-share seams in `financials.db` (found in 3b, pre-existing):** 295 of 1,956 listed ≥ $2B companies have a > 2.5x one-year jump in revenue per diluted share (both years > $100M revenue). Causes seen:
  - **share counts in thousands vs units** (×1,000-1,000,000 jumps: Kohl's 2022, Teva 2019, PACCAR 2018, Bruker 2021, Nomura 2023);
  - **currency / ADS basis switch** between sources (Alibaba: USD per ordinary share → CNY per ADS, 2023);
  - **pre-split ordinary counts next to ADS counts** (Toyota);
  - **a narrower revenue line on old rows** (American Express 2019-21: ~$28B vs ~$53B).
  - They also reach `revenue_growth_trend`, EPS fills and the Peer Strength per-share measures. The robust trend absorbs one odd year, so labels mostly hold. Fix belongs at import (Group A style), not as an analysis mask.
- **Group H part 3a (stock category) done 2026-11-21:** the analysis run checked (stored = test build). Next: 3b growth phase labels (track record from SEC years, not Yahoo's first-trade date — Yahoo resets it), 3c REIT FFO payout, part 4 relative strength vs the industry; then Group I.
- **Small existing issue found (H part 2):** `ps` is stored as infinity for 2 symbols (BTQ, APMCF: revenue near zero) — not valid JSON for the UI; a ratio guard like the peer-median one would fix it.
- **Possible follow-ups from H part 1** (not started, your call): Yahoo's EBITDA below operating income (210 listed companies) also affects `debt_to_ebitda`, `ev_ebitda` and `ebitda_negative` — the health check's operating-EBITDA fix could be used there too; share the one-row-per-year table between Piotroski and the health check (~3 s a run).
- **Score validation (§18 step 5)** waits for data: `score_history.db` started 2026-11-15; test later returns per score decile once a few months of runs exist.
- **Possible follow-ups from G part 1** (not started, your call): variable-dividend payers (Cal-Maine, Argentine banks) can read as cuts; specials of semi-annual payers are not detected; more `fair_value_tail` causes could be handled per model (Graham for distressed companies).
- **Possible follow-ups from F** (not started, your call): a change-of-basis rule for loss years (Talen's pre-bankruptcy EPS rows are kept); a two-year-trough rule for growth trends (UAL / DAL COVID recovery).
- **Possible follow-ups from E** (not started, your call): cap per-quarter earnings surprise at ±100%; a target-upside metric (would need the §20 target clean-up first); watch Yahoo's silent throttling at the end of long runs.
- **Possible follow-ups from D** (not started, your call): delisted-company history (§22, deferred); a liveness signal for warrants / units / SPACs (no price history); more industry overrides as misfits turn up.
- **Possible follow-ups from C** (not started, your call): split CHRD-style stitched histories (old + new company); correct reverse splits Yahoo never recorded (QH) instead of only flagging them; show `price_flags` on the price chart.
- **SEC refresh waves** (decided 2026-11-11, open to change): ~45 days after each calendar quarter-end the weekly run gets ~1 hour longer. Can be spread over several weeks if it bothers you.
- **Possible follow-ups from B** (not started, your call): fallback tags for 104 banks with no `NoninterestExpense`; `SalesRevenueGoodsNet` / bank revenue tags for older KO, JNJ, WFC years; a derived fiscal-Q4 quarter (Agilent and others have no 3-month Q4 in SEC data).

## Groups

| # | Group | Items | Status |
|---|---|---|---|
| A | Import cleaning rules | §5, §7 (ranges), §24, §25.5 | ✅ done 2026-11-11 · §20 EBITDA moved to F/G |
| B | EDGAR backfill rework | §1, §4, §12, §13, §14, §22 filed date, §23 | ✅ done 2026-11-11 · re-fetched + analysis run · §14 steps 2-4 → F |
| C | Price-history validation | §3, §20 (stale runs, gaps) | ✅ done 2026-11-12 · repair-when-broken + `price_flags` + analysis window cleanup + `price_issues` |
| D | Symbol state & identity | §15, §6, §25, §22 delisted history · + preferred tickers carrying old EDGAR rows (found in B) | ✅ done 2026-11-12 · delisted mark + monthly re-checks + recovery pass, type corrections, CIK / company_id, industry overrides · §22 deferred |
| E | Signals consistency | §11, §7 (dates, TTM lag, currency), §20 (surprise, currency, insiders, targets) | ✅ done 2026-11-14 · past dates NULL, earnings-driven financials refresh, surprise near zero, revenue chain check · currency / insiders / targets recorded (don't reach the screens) |
| F | Analysis foundations | §2, §10, §16, §20 (EV, EBITDA definition), §14 steps 2-4 (split basis via `filed_date`, Talen) | ✅ done 2026-11-14 · all-class market cap, EPS on one split basis, robust growth (Theil-Sen, `_growth_tau` replaces `_growth_r2`/`_cv`), EV + minority / preferred, normalized EBITDA · Talen loss rows open |
| G | Analysis built on F | §9, §17, §19, §18 | part 1 ✅ done 2026-11-15 (§9 regular dividends, §17 fair values, second lines) — analysis run checked · part 2 ✅ done 2026-11-15 (§19: Piotroski, peer universe, negative-denominator ratios) — analysis run checked · part 3 ✅ done 2026-11-15 (§18: input counts, fixed ranking universe, trend + forecast-checked growth, score history) — analysis run checked · score validation waits for history |
| H | New calculations | §21 · + §19 relative strength vs the industry | part 1 ✅ done 2026-11-16 (health check, peak check, dividend warning) — analysis run checked · part 2 ✅ done 2026-11-16 (Peer Strength) — analysis run checked · part 3a ✅ done 2026-11-21 (stock category) — analysis run checked · part 3b ✅ done 2026-11-21 (growth phase) — analysis run checked · part 3c ✅ done 2026-11-21 (REIT payout on FFO) — analysis run checked · part 4 ✅ done 2026-11-22 (relative strength vs the industry) — analysis run checked |
| I | Indices | §8 — touches FAPortfolio's `indices.db` contract | open |

**Order:** B before F (share counts, EPS); F before G; §15 before §18.

## Notes carried forward

- **§4 / §12 resolved:** `form` is used for flows only (never quarterly reports); balance-sheet dates follow the accepted year-ends. See the done file.
- **Group B lessons:** the SEC can re-point a ticker at a new company record (history guard added); preferred-share tickers sit outside the EDGAR scope but carry old EDGAR rows (→ D).
- **Backups for this project:** `backups/data_system_fixes/` — `2026-11-11_18-09-23_refetch_edgar` (before the full re-fetch), `2026-11-11_20-34-23_refetch_edgar` (before the repair pass).
