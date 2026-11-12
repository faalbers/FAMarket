# Fix list triage

Internal tracker for working through `docs/fix_list_for_data_system.md` on branch `feature/data-system-fixes`. Results for the reviewer go in `docs/fix_list_for_data_system_done.md`.

## Waiting for your input

- **Next group:** D — symbol state & identity. Start the discussion.
- **Possible follow-ups from C** (not started, your call): split CHRD-style stitched histories (old + new company); correct reverse splits Yahoo never recorded (QH) instead of only flagging them; show `price_flags` on the price chart.
- **SEC refresh waves** (decided 2026-11-11, open to change): ~45 days after each calendar quarter-end the weekly run gets ~1 hour longer. Can be spread over several weeks if it bothers you.
- **Possible follow-ups from B** (not started, your call): fallback tags for 104 banks with no `NoninterestExpense`; `SalesRevenueGoodsNet` / bank revenue tags for older KO, JNJ, WFC years; a derived fiscal-Q4 quarter (Agilent and others have no 3-month Q4 in SEC data).
- **EBITDA definition** (§20, parked for F/G): keep reported EBITDA or switch `debt_to_ebitda` / `ev_ebitda` to Yahoo's `normalized_ebitda`.

## Groups

| # | Group | Items | Status |
|---|---|---|---|
| A | Import cleaning rules | §5, §7 (ranges), §24, §25.5 | ✅ done 2026-11-11 · §20 EBITDA moved to F/G |
| B | EDGAR backfill rework | §1, §4, §12, §13, §14, §22 filed date, §23 | ✅ done 2026-11-11 · re-fetched + analysis run · §14 steps 2-4 → F |
| C | Price-history validation | §3, §20 (stale runs, gaps) | ✅ done 2026-11-12 · repair-when-broken + `price_flags` + analysis window cleanup + `price_issues` |
| D | Symbol state & identity | §15, §6, §25, §22 delisted history · + preferred tickers carrying old EDGAR rows (found in B) | open |
| E | Signals consistency | §11, §7 (dates, TTM lag, currency), §20 (surprise, currency, insiders, targets) | open |
| F | Analysis foundations | §2, §10, §16, §20 (EV, EBITDA definition), §14 steps 2-4 (split basis via `filed_date`, Talen) | open |
| G | Analysis built on F | §9, §17, §19, §18 | open |
| H | New calculations | §21 | open |
| I | Indices | §8 — touches FAPortfolio's `indices.db` contract | open |

**Order:** B before F (share counts, EPS); F before G; §15 before §18.

## Notes carried forward

- **§4 / §12 resolved:** `form` is used for flows only (never quarterly reports); balance-sheet dates follow the accepted year-ends. See the done file.
- **Group B lessons:** the SEC can re-point a ticker at a new company record (history guard added); preferred-share tickers sit outside the EDGAR scope but carry old EDGAR rows (→ D).
- **Backups for this project:** `backups/data_system_fixes/` — `2026-11-11_18-09-23_refetch_edgar` (before the full re-fetch), `2026-11-11_20-34-23_refetch_edgar` (before the repair pass).
