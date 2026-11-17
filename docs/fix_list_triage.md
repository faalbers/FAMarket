# Fix list triage

Internal tracker for working through `docs/fix_list_for_data_system.md` on branch `feature/data-system-fixes`. Results for the reviewer go in `docs/fix_list_for_data_system_done.md`.

## Waiting for your input

- **Group H part 1 (health check, peak check, dividend warning) done, uncommitted:** analysis run checked 2026-11-16 (stored = test build). Next: part 2 Peer Strength, part 3 labels + REIT FFO payout, part 4 relative strength vs the industry; then Group I.
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
| H | New calculations | §21 · + §19 relative strength vs the industry | part 1 ✅ done 2026-11-16 (health check, peak check, dividend warning) — analysis run checked · parts 2-4 open |
| I | Indices | §8 — touches FAPortfolio's `indices.db` contract | open |

**Order:** B before F (share counts, EPS); F before G; §15 before §18.

## Notes carried forward

- **§4 / §12 resolved:** `form` is used for flows only (never quarterly reports); balance-sheet dates follow the accepted year-ends. See the done file.
- **Group B lessons:** the SEC can re-point a ticker at a new company record (history guard added); preferred-share tickers sit outside the EDGAR scope but carry old EDGAR rows (→ D).
- **Backups for this project:** `backups/data_system_fixes/` — `2026-11-11_18-09-23_refetch_edgar` (before the full re-fetch), `2026-11-11_20-34-23_refetch_edgar` (before the repair pass).
