# Fix list triage

Internal tracker for working through `docs/fix_list_for_data_system.md` on branch `feature/data-system-fixes`. Results for the reviewer go in `docs/fix_list_for_data_system_done.md`.

## Waiting for your input

- **Next group:** B — EDGAR backfill rework. Start the discussion.
- **Analysis run:** after Group A's cleanup, a full analysis run fills `roe` / `pb` / `debt_to_equity` for partnerships (BEP, WES, …). Your call when.
- **EBITDA definition** (§20, parked for F/G): keep reported EBITDA or switch `debt_to_ebitda` / `ev_ebitda` to Yahoo's `normalized_ebitda`.

## Groups

| # | Group | Items | Status |
|---|---|---|---|
| A | Import cleaning rules | §5, §7 (ranges), §24, §25.5 | ✅ done 2026-11-11 · §20 EBITDA moved to F/G |
| B | EDGAR backfill rework | §1, §4, §12, §13, §14, §22 filed date, §23 | next — needs one EDGAR re-fetch |
| C | Price-history validation | §3, §20 (stale runs, gaps) | open |
| D | Symbol state & identity | §15, §6, §25, §22 delisted history | open |
| E | Signals consistency | §11, §7 (dates, TTM lag, currency), §20 (surprise, currency, insiders, targets) | open |
| F | Analysis foundations | §2, §10, §16, §20 (EV, EBITDA definition) | open |
| G | Analysis built on F | §9, §17, §19, §18 | open |
| H | New calculations | §21 | open |
| I | Indices | §8 — touches FAPortfolio's `indices.db` contract | open |

**Order:** B before F (share counts, EPS); F before G; §15 before §18.

## Notes carried forward

- **§4 / §12:** the review proposes classifying EDGAR rows by `fp` / `form`. `data_layer/fetchers/edgar_fetcher.py` (module notes) records these as unreliable for balance-sheet dates, checked on AAPL. Research before adopting.
- **One EDGAR re-fetch** for all of Group B — agree every B item first, then re-fetch once.
