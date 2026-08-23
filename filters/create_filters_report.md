# Filter build report — 2026-08-23

All **13 filters** in `create_filters.md` were rebuilt from scratch (the `filters/`
folder was empty, so every name was new — no versioning was needed).

Every filter was structurally validated **and** dry-run against `analysis.db`
(38,374 symbols, 291 columns) before saving. **All 13 pass with zero errors and
zero warnings.**

Each `.filt` carries its own writeup in the **Comment** field — what it does, how
to tweak it, how to sort it. This report adds the reasoning behind the build.

---

## Summary

| Filter | Types | Blocks | Matches | You asked for |
|---|---|---:|---:|---|
| `quality_compounders` | standard | 11 | **76** | ~20-100 ✅ |
| `emerging_dominators` | standard | 6 | **106** | ~100-300 ✅ |
| `dividend_growers` | standard, bank, insurance | 8 | **53** | ~50-200 ✅ |
| `reit_income` | reit | 6 | **30** | ~30-150 ✅ |
| `defensive_anchors` | standard | 9 | **20** | ~20-100 ✅ |
| `undervalued_quality` | standard | 10 | **25** | ~20-150 ✅ |
| `small_cap_winners` | standard | 5 | **113** | ~50-200 ✅ |
| `garp_movers` | standard, bank, insurance | 8 | **149** | ~30-150 ✅ |
| `trend_leaders` | standard | 8 | **335** | "whatever the market gives" |
| `estimate_upgrades` | standard, bank, insurance, reit | 9 | **165** | ~30-200 ✅ |
| `insider_conviction` | standard, bank, insurance, reit | 3 | **311** | ~50-300 ✅ |
| `buyback_compounders` | standard | 7 | **127** | ~30-150 ✅ |
| `financial_compounders` | bank, insurance | 8 | **41** | ~20-150 ✅ |

---

## Decisions you were asked to make

Only **three** briefs were ambiguous enough to need you. Everything else was
already answered by the briefs and their 2026-07-28 / 2026-08-13 Update sections.

**garp_movers** — your blocks as written returned 227, above the ~30-150 you
wanted. Every threshold in that brief was explicit, so one had to give. You chose
to raise the momentum floor from **RS 60 to RS 70** → **149**. All other numbers
stayed exactly as briefed.

**estimate_upgrades** — as literally written ("raised" = any uptick) it returned
639. You chose to define "raised" as a **meaningful 7%+ upgrade** in both the
1-month and 3-month windows → **165**. Your "beat 3 of 4 quarters" rule was left
untouched at 75%.

**insider_conviction** — "any net buying, RS 40" returned 730. You chose to
strengthen **both** sides: net buying above **5%** AND **RS 60+** → **311**.

---

## Numbers I chose (the briefs left these open)

Where a brief said "solid liquidity" or "reasonable" without a number, the choice
was mine. These are the ones worth knowing about, because they are the dials to
turn first:

- **defensive_anchors** — `current_ratio ≥ 1.2`. At the textbook 1.5 the screen
  returned only 14 names; at 1.2 it returns 20. Your explicit asks (10+ year
  streak, $10B+, ATR ≤3, D/E <1, safe Altman, P/E <25) were all left as written.
- **buyback_compounders** — `fcf_margin ≥ 8`, `roic ≥ 12` for "healthy cash flow
  and solid returns". At 5/10 the list was 186; at 8/12 it is 127.
- **financial_compounders** — `pb ≤ 2.0` for "reasonable", `rs_rank ≥ 50` for
  "decent". P/B is the sensitive one: 1.5 gives 21 names, 2.0 gives 41, 2.5
  gives 58.
- **insider_conviction** / **garp_movers** / **estimate_upgrades** — see above,
  you decided these.

---

## The N/A traps that were handled

**NULL fails every test except "is null".** A missing number is not a zero — it
silently drops the company. These fallbacks are why the counts above are honest:

- **`eps_cagr` needs a forward-growth fallback.** A company crossing from loss to
  profit has no computable EPS growth rate. Without the fallback,
  `quality_compounders` would have quietly deleted every turnaround story.
  Applied in `quality_compounders`.
- **`altman_z` is blank for asset-light software** — the formula wants a
  manufacturer's balance sheet. `is null` fallbacks added in
  `quality_compounders` and `defensive_anchors`, so tech is judged by the other
  health blocks rather than dropped.
- **`debt_to_ebitda` is blank for companies with no debt** — often the healthiest
  names. `is null` fallbacks in `quality_compounders` and
  `buyback_compounders`.
- **REIT debt metrics** — `reit_income` passes a REIT whose debt data is missing,
  exactly as the brief demanded.
- **Pre-profit names in `emerging_dominators`** — the margin test falls back to
  operating margin, then to `is null`, so a young company is never dropped for
  missing data.
- **5-year growth windows** — avoided in favour of 3-year with 1-year fallbacks,
  since 5-year history is thin for recent listings.

---

## Per-filter notes

### quality_compounders — 76 matches

Ten-year buy-and-hold: profitable now, compounding steadily, balance sheet sound.
RS 70+ and above MA200 for "proven in price"; revenue AND EPS at 8%+ with forward
fallbacks; ROIC 10%+; current ratio, debt/EBITDA and Altman for "no troubling
financials".

The heaviest cuts were `roic ≥ 10` (397→210) and the revenue growth block
(210→120). Deliberately **excludes banks and insurers** — mixing them in would
strip out ROIC, margins and liquidity, which is exactly why
`financial_compounders` exists separately.

**Sort by `quality_score`, tiebreak `momentum_score`.** The tiebreak is your
"prioritise strong industries" step — momentum surfaces the sectors being
rewarded now. **Avoid `value_score`** (compounders are never cheap) and
`income_score` (you ignore dividends here).

### emerging_dominators — 106 matches

The early-NVDA shape. The block that carries the thesis is **`revenue_accel > 0`**
— the latest quarter running faster than its own 3-year pace. That is what
separates a future dominator from a merely fast grower.

Worth knowing: **this list is naturally ~100 and barely responds to the momentum
gate.** Loosening RS from 60 to 40 adds only ~20 names. The 20%+ accelerating
growth requirement is what binds, so tweak growth, not RS, to resize it.

**Sort by `growth_score`, tiebreak `momentum_score`. Avoid `overall_score`,
`quality_score` and `value_score`** — all three reward mature, cheap, profitable
companies and would bury the early-stage names this screen exists to find.

### dividend_growers — 53 matches

Yield 2-6%, 5%+ dividend growth, 5+ year raise streak, and safety checked twice:
FCF covers the dividend 2x AND payout sits in 30-60%.

Those two safety blocks do nearly all the cutting (201→84→55). **`div_payout_ratio`
30-60 is the tightest single block** — widen to 20-70 if you want a fuller list.

**Sort by `income_score`, tiebreak `quality_score`** or `div_coverage`. **Trust
`div_coverage` over `div_payout_ratio`** — payout is accounting-based and can hide
a trap; coverage is cash-based.

### reit_income — 30 matches

Yield 4-10% (the REIT band — they are legally required to pay out most income),
revenue not shrinking, debt within REIT norms, and **both debt tests pass when the
data is missing**, as you insisted.

**The count is small because the pond is small**: there are only ~292 REITs in the
database and ~152 above $1B. Loosening the debt tests moves it by a handful.
Lowering `market_cap` to $500M is the real lever.

**Sort by `income_score`, tiebreak `quality_score`.**

### defensive_anchors — 20 matches

Graham-defensive: $10B+, 10+ year raise streak (or 95%+ consistency), ATR ≤3%,
Altman safe zone, D/E <1, P/E <25.

**This is deliberately your strictest screen and the scarcity is real** — very few
large caps are simultaneously calm, cheap, low-debt and long-raising. Biggest
lever is `current_ratio` (1.5→14 names, 1.2→20, 1.1→22); second is `pe` (25→20
names, 30→34).

**Sort by `quality_score`, tiebreak `income_score`. Avoid `growth_score`** — these
are not growth names.

### undervalued_quality — 25 matches

Your 2026-08-13 Update asked for a recalibration of the margin-of-safety
threshold. **The dry run's answer is that it barely matters:** 30% gives 22 names,
15% gives 25, 0% gives 27.

The reason is exactly what your Update predicted — **`margin_of_safety_bear > 0`
is doing the value-trap work** (it cuts 405→264 on its own), not the base number.
Settled on **15%**, the post-fade equivalent of "meaningfully cheap". What
actually binds is `roic ≥ 10` (176→77) and the MA200 proof-of-life test (55→32).
`bear_flag_count ≤ 1` and `valuation_guardrail_flag = 0` are both in, per the
Update.

**Sort by `margin_of_safety_bear`** (still cheap in the pessimistic case) **or
`value_score`, tiebreak `quality_score`**, then `bear_flag_count` ascending.
**Avoid `momentum_score`** — genuine value usually means out of favour.

### small_cap_winners — 113 matches

The O'Shaughnessy combo: $300M-$2B, P/S below industry, RS 70+, revenue growing,
100k volume. Sales-based on purpose — small companies often are not profitable
yet, and that is fine here. RS 70 does the heavy cutting (535→176).

**Sort by `momentum_score`, tiebreak `growth_score`. Avoid `quality_score`** —
small caps score badly on it by nature, so it just ranks them by maturity.

### garp_movers — 149 matches

Forward PEG ≤1.0 with 10%+ forward growth, 3+ analysts, profitable, above MA200,
ATR ≤5.5. **RS raised 60→70 at your direction** to bring 227 into range.

**Sort by `momentum_score`, tiebreak `value_score`.** Worth a look: sort by
`forward_peg` ascending for the purest GARP reading.

### trend_leaders — 335 matches

The full Minervini template as pure price action — the MA stack written as real
column-to-column comparisons (`price > ma_50 > ma_150 > ma_200`), within 25% of
the 52-week high, 30%+ above the low, RS 80+.

**335 is simply what the market is giving right now.** You said "whatever the
market gives", and this count is itself a market indicator — it swells in a broad
advance and collapses in a correction. One piece of the classic template is
missing: it also wants the 200-day line to be **rising**, which we do not store.

**Sort by `rs_rank`, tiebreak `momentum_score`. Avoid `value_score` and
`quality_score`** — this screen deliberately ignores fundamentals.

### estimate_upgrades — 165 matches

EPS estimates raised 7%+ over both the last month and 3 months, more analysts
raising than cutting, beat 3 of the last 4 quarters with a positive average
surprise, 3+ analysts, above MA200.

This stacks two documented effects: **estimate-revision momentum** and
**post-earnings-announcement drift**. Note the 3-month window contains the
1-month, so requiring both is a test of *sustained* upgrading rather than two
independent signals.

**Sort by `momentum_score`, tiebreak `eps_revision_1m`** (the freshest upgrade).
Worth a look: `days_to_next_earnings` ascending is a timing risk, not a quality
measure.

### insider_conviction — 311 matches

Deliberately the **shortest filter — only 3 blocks**. It is a single-signal screen;
piling on fundamentals would dilute the very thing being tested. Net insider
buying above 5%, $300M-$10B, RS 60+.

Includes banks and REITs on purpose — the research says the insider effect is
strongest in small caps and banks, and weakest in tech.

**Sort by `growth_score`, tiebreak `momentum_score`.** Worth a look: sort by
`insider_net_buy_pct` descending for the loudest votes of confidence.

### buyback_compounders — 127 matches

Share count down 2%+ (net of stock comp, so real shrinkage), funded by FCF margin
8%+ and ROIC 12%+, debt/EBITDA under 4 with the debt-free fallback, profitable,
$1B+.

**Sort by `quality_score`, tiebreak `value_score`. Avoid `momentum_score`** —
buyback compounding is a slow, quiet effect. Worth a look: sort by
`share_count_chg_1y` ascending (most negative first) for the raw signal.

### financial_compounders — 41 matches

Banks and insurers judged by **their** rules: ROE 12%+, equity multiplier under 15
so high ROE is earned rather than borrowed (the median financial runs ~8.9x), book
value growing 7%+ with forward EPS as fallback, P/B ≤2.0 or below industry.

Margins, ROIC, current ratio and debt/EBITDA are all meaningless here — debt is a
bank's raw material, not a risk to minimise. That is exactly why this is a
separate screen from `quality_compounders`.

**Sort by `quality_score`, tiebreak `momentum_score`.** Worth a look:
`book_value_cagr_3y` descending is the purest reading of financial compounding.
**Avoid `value_score`** — it leans on sales and cash-flow multiples that are hidden
for financials.

---

## If a filter returns something surprising

Use the Output page's **🔍 Filter Fail** action. It shows, per symbol and per
block, exactly what passed and failed with the actual values — the fastest way to
find out whether a threshold is wrong or the data is simply missing.
