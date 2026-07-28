# Filters Report — /make_filters run 2026-07-28 (11 new filters)

Built the 11 new briefs from `filters/create_filters.md` — scope "only new"
(the existing quality_compounders / emerging_dominators v2s were left alone;
their rebuild is summarized at the bottom). Every brief pre-answered the
checklist, so **zero clarifying questions** were needed. All filters passed
`scripts/validate_filt.py` and were dry-run before saving.

**Where a saved threshold differs from the brief, it is called out in bold
and explained in the filter's own comment** — always because the brief's
stated result-size target and its example numbers couldn't both hold.

---

## Income

### dividend_growers — 51 matches (target 50-200 ✓)
- Yield 2-6% band, dividend growth 5%+ (5y, 3y fallback), 5+ year streak,
  FCF coverage 2x, payout 30-60%, profitable, calm-ish (ATR ≤ 5.5).
- Types: standard + bank + insurance.
- **Sort**: `income_score`, tiebreak `quality_score`. Avoid momentum/growth sorts.

### reit_income — 47 matches (target 30-150 ✓)
- REITs only: yield 4-10%, revenue not shrinking, debt/EBITDA ≤ 7,
  interest coverage ≥ 1.8 (both with missing-data passes), consistency ≥ 80.
- **Sort**: `income_score`, tiebreak `quality_score`. Avoid value-only sorting
  (cheapest REITs are cheap for a reason).

### defensive_anchors — 17 matches (target ~20-100, strict by design)
- $10B+, ATR ≤ 3 (the calmest names), yield ≥ 2%, 10-year streak or
  near-spotless record, Altman safe, health checklist ≥ 4/7, P/E ≤ 28.
- **Deviation**: brief said debt BELOW equity — D/E ≤ 1.0 left only 8 names
  (staples/utility megacaps carry structural debt), saved at **≤ 1.5**;
  current-ratio floor **0.8** for the same reason. Both noted in the comment.
- **Sort**: `quality_score`, tiebreak `income_score`.

## Value

### undervalued_quality — 17 matches (target ~20-150, strict by design)
- Margin of safety ≥ **20** (brief ~30 → only 8 names; softened), cheaper P/E
  than its industry, PEG ≤ 1.5, ROIC ≥ **8** (brief ~10), health ≥ 5/7,
  within 5% of the 200-day line, profitable, $1B+.
- **Sort**: `value_score`, tiebreak `quality_score`. Avoid momentum sorting.

### small_cap_winners — 103 matches (target 50-200 ✓)
- $300M-$2B, P/S cheaper than at least half its industry peers (**peer-score
  variant** — a raw `ps_vs_industry` column doesn't exist; the validator
  caught this before saving), RS ≥ 70, revenue growing, 100k+ volume.
- Pre-profit names pass by design (sales-based valuation).
- **Sort**: `momentum_score`, tiebreak `growth_score`. Avoid quality sorting
  (small caps score structurally lower on polish).

### garp_movers — 196 matches (target ~30-150, slightly broad)
- Forward PEG ≤ **0.9** + RS ≥ **70** (tightened from the brief's 1.0/60 —
  still 196 names; the comment shows how to tighten further), forward EPS
  growth ≥ 10%, above MA200, 3+ analysts, profitable, $500M+.
- Types: standard + bank + insurance.
- **Sort**: `momentum_score`, tiebreak `value_score`.

## Momentum

### trend_leaders — 341 matches (brief: "whatever the market gives" ✓)
- The full Minervini stack via P-mode: price > MA50, MA50 > MA150 > MA200,
  within 25% of the 52w high, 30%+ off the low, RS ≥ 80, 100k+ volume.
- The count breathes with the market — that's information, not looseness.
- **Sort**: `rs_rank`, tiebreak `momentum_score`. Avoid value sorting.

### estimate_upgrades — 262 matches (target ~30-200, slightly broad)
- EPS revisions positive over 1m AND 3m, breadth ≥ **2** (brief: >0),
  analysts ≥ **5** (brief: 3), beat rate ≥ 75%, positive avg surprise,
  above MA200. Types: all four company types.
- **Sort**: `momentum_score`, tiebreak raw `eps_revision_1m`.

## Signals

### insider_conviction — 473 matches (broad on purpose — a signal scan)
- Net insider buying ≥ **4%** of insider-held shares (brief: ">0" — that was
  ~1000 names; even 4%+ stays broad because the distribution is fat-tailed),
  $300M-$10B, RS ≥ 45. Banks/REITs included (signal strongest in banks).
- The comment says it plainly: expect hundreds; **the sort does the picking**.
- **Sort**: `growth_score`, tiebreak `momentum_score` — or raw
  `insider_net_buy_pct` descending for the conviction view.

### buyback_compounders — 150 matches (target 30-150 ✓)
- Share count down ≥ **3%**/yr (brief ~2%+; 3% keeps focus), FCF margin ≥ 5%,
  ROIC ≥ 8%, debt/EBITDA ≤ 4 (missing-data pass), profitable, $1B+.
- **Sort**: `quality_score`, tiebreak `value_score`; raw `share_count_chg_1y`
  ascending shows the most aggressive shrinkers.

### financial_compounders — 65 matches (target 20-150 ✓)
- Banks + insurers only: ROE ≥ 12% backed by ROA ≥ 0.8% (so the ROE isn't
  pure leverage), book value growing 7%+ (5y/forward fallbacks), P/B ≤ 2.5
  or cheaper than peers (**peer-score variant** — `pb_vs_industry` doesn't
  exist; validator caught it), above MA200, RS ≥ 50.
- Known validator warning: `roa 0.8` triggers the fraction heuristic — false
  alarm, bank ROA genuinely lives below 1 percent-point.
- **Sort**: `quality_score` (peer-relative, works for financials), tiebreak
  `momentum_score`. Avoid value-only (cheapest banks = brewing credit trouble).

---

## Earlier the same day: v2 rebuild of the original pair

- **quality_compounders_v2** — 49 matches. Added Beneish M-Score clean-books
  gate + quick_health ≥ 5 (both new params since v1).
- **emerging_dominators_v2** — 140 matches. Added the missing-margin-data
  pass on the margins-vs-industry block (the BEAM bug class).

## Files

15 `.filt` files in `filters/`, all passing `python -m scripts.validate_filt --all`.
Each carries its verbatim brief in `ai_instructions` and the full usage
writeup (what it does / how to tweak / how to sort) in `comment`.
