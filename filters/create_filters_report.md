# Filter build report — 2026-08-13

All **13** briefs in `create_filters.md` were built from scratch (the folder was
empty, so every filter is a fresh `<name>.filt`, no versioning needed).

Every filter passed `validate_payload` with **zero errors** and was dry-run
against the live `analysis.db` before saving. Counts below are matches at build
time.

**Context that shaped this build:** the valuation engine changed earlier the same
day (growth now fades to terminal instead of holding a flat cap), so `fair_value`
and `margin_of_safety` mean something stricter than when most briefs were
written. Only `undervalued_quality` depends on those columns, and its brief
already carried a dated Update telling the build to recalibrate — which it did.

---

## Results at a glance

| Filter | Matches | Target | Verdict |
|---|---:|---|---|
| quality_compounders | 73 | 20–100 | on target |
| emerging_dominators | 135 | 100–300 | on target |
| dividend_growers | 47 | 50–200 | just under |
| reit_income | 31 | 30–150 | on target |
| defensive_anchors | **15** | 20–100 | short by design |
| undervalued_quality | **14** | 20–150 | short by design |
| small_cap_winners | 134 | 50–200 | on target |
| garp_movers | 210 | 30–150 | slightly over |
| trend_leaders | 342 | ~50–300 | brief says "whatever the market gives" |
| estimate_upgrades | 286 | 30–200 | slightly over |
| insider_conviction | 298 | 50–300 | on target |
| buyback_compounders | 149 | 30–150 | on target |
| financial_compounders | 65 | 20–150 | on target |

---

## Two bugs caught by the dry run

**1. `ps` and `pb` had no peer-comparison columns — now fixed at the source.**
Only 11 metrics got `_vs_sector` / `_vs_industry` columns, and P/S and P/B were
not among them. Blocks written as "P/S below its industry" returned **zero rows**
— silently, because a missing column makes the engine return all-False.

Rather than work around it, `ps`, `pb`, `p_fcf` and `ev_revenue` were **added to
`PEER_COMPARABLE_METRICS`** and the universe re-analysed. Both filters now use
the real peer comparison their briefs asked for. Banks/insurers/REITs gained
`pb_vs_industry` on 609/618, 149/155 and 268/291 rows respectively — their core
valuation multiple, previously peer-blind.

Fixing it surfaced a **second, larger bug**: peer comparison ignored the
`positive_only` flag the scoring layer already declares, so **2,266 loss-making
companies carried a `pe_vs_industry` of −180% at the median** (worst
−68,000,000%) — reported as a deep discount when a negative P/E means the company
is losing money, and dragging the peer medians with them. `peers.py` now reads
that same declaration, so the two layers cannot disagree. All 2,266 now read
NULL, as do 1,003 negative-book-value rows and 36 negative-revenue rows.
(`ev_revenue` deliberately keeps its negatives — those are negative *enterprise
value* on positive revenue, i.e. net cash above market cap, which is genuinely
cheap.) 21 stored infinities were also converted to NULL.

**2. The knowledge doc was stale.** It predated the valuation work and was
missing 18 filterable metrics (the whole `fair_value` family, the bear flags,
`wacc`/`roic_vs_wacc`, `ocf_to_ni`, the `growth_trend` window) and described the
DCF as using a flat 15% growth cap. It has been refreshed as part of this build.

---

## Per-filter notes

Each filter's own `comment` field carries the full **What it does / How to tweak /
How to sort** writeup and travels with the file. Summarised here:

### quality_compounders — 73 matches
Long-term quality holds. ROIC ≥ 10% is the core test; growth bars sit at 8% with
forward-estimate OR-fallbacks so a loss-to-profit transition can't drop a name.
Debt and Altman tests carry `is null` fallbacks so debt-free companies aren't
punished for having no debt data.
**Brief answers used:** no pre-profit names, 8%+ growth, $2B+, standard only,
ATR ≤ 6, dividends ignored, "prioritise strong industries" handled by sorting.
**Sort:** `quality_score`, tiebreak `momentum_score`. Avoid `value_score` —
compounders rarely look cheap.

### emerging_dominators — 135 matches
Explosive, still-accelerating revenue with margins ahead of industry. The margin
block has an `is null` fallback because the brief explicitly allows pre-profit
names.
**Brief answers used:** $1B–$80B, 20%+ revenue growth AND accelerating, pre-profit
fine, volatility accepted, broad list.
**Sort:** `growth_score`, tiebreak `momentum_score`. Avoid overall/quality/value —
all three bury early-stage names.

### dividend_growers — 47 matches
Growing income with safety first. FCF-based `div_coverage` ≥ 2 is used rather than
trusting the payout ratio alone.
**Notable:** `div_payout_ratio` 30–60 is the tightest single block — it cut the
set from 168 to 68 on its own. Widen to 20–70 for a fuller list.
**Sort:** `income_score`, tiebreak `quality_score`. Never sort by raw yield — that
puts the riskiest payers on top.

### reit_income — 31 matches
The type the other screens exclude. Debt blocks carry `is null` fallbacks exactly
as the brief asked. The REIT universe is only ~291 rows, so counts stay modest.
**Sort:** `income_score`, tiebreak `quality_score`.

### defensive_anchors — 15 matches (short, on purpose)
$10B+, 10-year raise streak, Altman ≥ 3, debt below equity, lowest volatility of
any screen. Already loosened where the brief was vague (liquidity 1.5 → 1.0, ATR
3.0 → 3.5). What still binds is what you asked for explicitly.
**Widening lever, in order:** debt-to-equity 1.0 → 1.5 (many quality large caps
borrow cheaply and sit above 1.0), then market cap $10B → $5B.
**Sort:** `quality_score`, tiebreak `income_score`.

### undervalued_quality — 14 matches (short, on purpose)
Built to the brief's 2026-08-13 Update: margin of safety recalibrated to **15%**
(not 30 — the fade made the old number stricter), requires a **positive bear-case**
margin of safety, caps `bear_flag_count` at 1, and excludes guardrail-flagged
names. ROIC relaxed 10 → 8 (the brief says "~10%+").
**Widening lever:** drop the "cheaper than its own industry" P/E block — it roughly
thirds the list alone.
**Sort:** `margin_of_safety_bear`, tiebreak `quality_score`. Avoid `momentum_score`.

### small_cap_winners — 134 matches
O'Shaughnessy small-cap value+momentum. Cheapness is a true peer comparison:
P/S below the industry median, which is literally what the brief asked for.
(Was 67 while using the Score variant as a stand-in — that bar sat at the ~70th
percentile, while "below its industry" means the ~50th, so the count roughly
doubled when the real column arrived.)
**Sort:** `momentum_score`, tiebreak `growth_score`.

### garp_movers — 210 matches
Forward PEG ≤ 1.0 (tightened from 1.2 to pull the count toward target), 10%+
expected growth, 3+ analysts. Forward rather than trailing PEG on purpose.
**Sort:** `momentum_score`, tiebreak `value_score` or `forward_peg` ascending.

### trend_leaders — 342 matches
The full Minervini template, expressed with real column-to-column comparisons
(`ma_50 > ma_150 > ma_200`) rather than an approximation. No fundamentals at all.
Count swings with market conditions — the brief anticipates that.
**Sort:** `rs_rank`, tiebreak `momentum_score`. Avoid every fundamental score.

### estimate_upgrades — 286 matches
Revisions + post-earnings drift stacked. The 1-month revision bar was raised to
2% to pull the count down; the "3 of 4 quarters" beat rate was left at 75 because
the brief states it explicitly.
**Sort:** `momentum_score`, tiebreak `eps_revision_1m`.

### insider_conviction — 298 matches
Net insider buying, sized to where the research says the signal works. Tightened
to a **10%+ net-buy stake** and RS Rank ≥ 55 — at "any buying at all" the screen
returned 762 names, far past the brief's target.
**Sort:** `growth_score`, tiebreak `momentum_score`.

### buyback_compounders — 149 matches
Share count down 2%+ with the cash flow to fund it. Debt test has an `is null`
fallback so debt-free companies aren't punished.
**Sort:** `quality_score`, tiebreak `value_score`. Avoid `income_score` — a
buyback company is deliberately not a dividend company.

### financial_compounders — 65 matches
Banks and insurers judged by their own rules: ROE 12%+, book-value growth 7%+,
sane P/B, leverage capped. "Reasonable OR below peers" is now a real either/or —
P/B at or under 2.5, or below its industry median. Count unchanged at 65: the
2.5 ceiling already admits most of this group, so the peer branch only matters
for the expensive tail.
**Sort:** `quality_score`, tiebreak `momentum_score`.

---

## How to read the sort guidance

The **primary sort** should match the filter's thesis — sorting a growth screen by
`value_score` buries exactly the names it was built to find. The **tiebreaker**
adds a second dimension without overriding the thesis.

`overall_score` is rarely the right sort for a themed screen: it blends five
categories, so it dilutes whatever made the screen specific.

For `undervalued_quality` specifically, `margin_of_safety_bear` is the strongest
single sort available — it asks "is this still cheap even in the pessimistic
case?", which is a much better value-trap test than the base number alone.
