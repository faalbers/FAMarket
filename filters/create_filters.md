# Filter briefs for /make_filters

This file feeds the `/make_filters` skill. Each entry below is one filter to build:
plain-English instructions in your own words. The skill copies the `instructions:`
text **verbatim** into the saved filter's `ai_instructions` field — so this file is
the permanent record of what each filter was asked to do.

**Format** (everything above the first `---` is guidance, not a brief):

```
---
filter: <name_in_snake_case>
instructions:
<free text, as many lines as you want>
```

**Rules**

- **One goal per filter.** "Growth AND safe income AND cheap" fights itself —
  split it into two filters instead.
- **Don't rewrite a brief after its filter is built** — the saved filter keeps the
  old text as provenance. Instead, add a dated **"Update (…)"** section under the
  original text and rerun `/make_filters` (choose "all"); it saves a new `_v2`
  version, never overwrites. Where an Update contradicts the original text, the
  Update wins.
- Numbers are welcome but optional ("bigger than 1 billion", "yield above 3%") —
  the skill picks sensible defaults for anything you leave out.

**Answer these in the brief if you care about them** — anything you skip, the
skill either asks about (max 2-3 questions) or uses a sensible default:

- **Goal** — growth, income, value/cheap, momentum, or safety?
- **Not yet profitable OK?** — yes/no (matters a lot for young companies)
- **Size** — micro / small / mid / large caps? A floor or a band?
- **Risk** — calm stocks only, or is volatility fine?
- **How many results** — a broad discovery list (~hundreds) or a strict
  shortlist (~tens)?
- **Dividends** — required, nice-to-have, or ignore?
- **What kind of companies** — normal stocks only, or also banks / insurance /
  REITs / funds?
- **How you'll pick from the results** — what should "best" mean when sorting?

---
filter: quality_compounders
instructions:
I want to find stocks to invest in long term, like for 10 years. These stocks need to be proven as constantly growing in price and the need to have a healthy growing business. I need to make sure they do not have troubling financials. I am just focused on them as growth and not income.
They also can not be too risky or volatile. But a bit of risk is OK. Also add a way to prioratize industries that have been doing really well and look like they have good growth potential.

Update (2026-07-28) — checklist answers added after a brief review; where this
differs from the text above, this wins:
- **Proven price growth** = strong recent strength (RS Rank 70+) AND trading above
  the 200-day average.
- **Profitability**: must already be profitable and healthy — no pre-profit names.
- **Growth pace**: steady 8%+ a year (revenue and EPS, history or forecast) is
  enough — this screen is quality over speed.
- **Size**: $2B market cap and bigger. **Kinds**: normal stocks only — on purpose:
  mixing in banks/insurance would strip the quality metrics (ROIC, margins,
  liquidity checks) down to the few both share. Financial compounders deserve
  their own separate filter if ever wanted.
- **Risk**: moderate — average daily moves up to ~6% are OK.
- **Results**: a shortlist (roughly 20-100 names).
- **Dividends**: ignore completely.
- **"Prioritize strong industries" means SORTING, not filtering**: sort by quality
  first, tiebreak on momentum (momentum surfaces the hot industries).
---
filter: emerging_dominators
instructions:
I want to find companies that could become the next dominant player in their industry, like NVDA was early in its growth story, but not limited to tech — any sector. These are companies with explosive revenue growth that is accelerating, a strong competitive position versus their peers, and a market that is already starting to recognise them. They don't need to be profitable yet but their unit economics should be improving. They should be big enough to be real but small enough to still have massive room to grow.

Update (2026-07-28) — checklist answers added after a brief review; where this
differs from the text above, this wins:
- **Size**: $1B-$80B market cap (big enough to be real, small enough to 10x).
- **"Explosive growth"** = revenue growing 20%+ a year (history or analyst
  forecast) AND faster than its own 3-year pace.
- **Competitive position** = margins above its industry (gross or operating).
- **Not yet profitable is fine** — never drop a company only because a margin or
  history number has no data yet.
- **Risk**: volatility is expected and accepted — no calm-stock requirement.
- **Results**: a broad discovery list is fine (~100-300 names).
- **Dividends**: ignore. **Kinds**: normal stocks only.
- **Sorting**: growth first, tiebreak momentum; avoid overall / quality / value
  scores (they bury early-stage names).
---
filter: dividend_growers
instructions:
Find stocks that pay me a decent dividend today AND raise it every year — income
that grows. Safety over size: I would rather have 3% growing safely than 7% at risk.
- **Goal**: income (growing). **Dividends**: required — the whole point.
- **Yield**: roughly 2-6% — an unusually high yield is a warning, not a bonus.
- **Growth**: dividend raised ~5%+ a year, with a raise streak of 5+ years.
- **Safety first**: the dividend must be covered ~2x by free cash flow, and the
  payout ratio must sit in the comfortable 30-60% band. Profitable companies only.
- **Kinds**: normal stocks plus banks and insurance. **Size**: $2B and bigger.
- **Risk**: calm-ish stocks (no wild movers). **Results**: ~50-200 names.
- **Sorting**: income first, tiebreak quality.
---
filter: reit_income
instructions:
Find solid income REITs — real-estate trusts with a fat but sustainable payout.
This fills the type my other screens exclude.
- **Goal**: income. **Kinds**: REITs only. **Dividends**: required.
- **Yield**: roughly 4-10% (REITs are legally high payers; above ~10% smells like
  trouble).
- **Still growing**: revenue should not be shrinking.
- **Debt in check**: leverage within normal REIT norms, interest comfortably paid;
  never drop a REIT just because a debt metric has no data.
- **Size**: $1B and bigger. **Risk**: moderate. **Results**: ~30-150 names.
- **Sorting**: income first, tiebreak quality.
---
filter: defensive_anchors
instructions:
Find "sleep well" stocks in the Graham defensive spirit — big, calm, financially
bulletproof dividend payers that hold up in a crash. Boring on purpose.
- **Goal**: safety with income. **Dividends**: required, never-cut history preferred
  (10+ year streak or a spotless consistency record).
- **Size**: large caps, $10B and bigger. **Kinds**: normal stocks only.
- **Calm**: low volatility — this should be my least jumpy screen.
- **Bulletproof**: strong balance sheet (safe Altman zone, solid liquidity, debt
  below equity). Profitable only. Valuation sane (no bubble P/E).
- **Results**: a shortlist, ~20-100 names.
- **Sorting**: quality first, tiebreak income.
---
filter: undervalued_quality
instructions:
Find good businesses that are temporarily cheap — value WITH proof of life, so I
skip the value traps (cheap companies that deserve to be cheap).
- **Goal**: value. **Dividends**: ignore.
- **Cheap**: trading well below fair value (margin of safety ~30%+) AND cheaper
  than its own industry.
- **Good**: real profitability (ROIC ~10%+) and passing basic health checks —
  profitable companies only.
- **Proof of life**: price at least back above its 200-day average, so the market
  has stopped voting against it.
- **Size**: $1B and bigger. **Kinds**: normal stocks only. **Risk**: moderate.
- **Results**: ~20-150 names. **Sorting**: value first, tiebreak quality.
---
filter: small_cap_winners
instructions:
Find small companies that are cheap on sales AND already winning — the
O'Shaughnessy small-cap growth & value combo (the best long-run track record of
the classic screens).
- **Goal**: growth + value combo. **Dividends**: ignore.
- **Size**: small caps, roughly $300M-$2B.
- **Cheap on sales**: price-to-sales below its industry (sales-based on purpose —
  small companies often aren't profitable yet, and that is FINE here).
- **Already winning**: strong recent price strength (RS Rank 70+), revenue growing.
- **Tradeable**: enough daily volume to get in and out.
- **Risk**: volatile is expected. **Kinds**: normal stocks only.
- **Results**: ~50-200 names. **Sorting**: momentum first, tiebreak growth.
---
filter: garp_movers
instructions:
Find growth at a discount that the market has started to notice — the "value on
the move" recipe (PEG with estimated growth plus momentum).
- **Goal**: growth at a reasonable price. **Dividends**: ignore.
- **The core test**: forward PEG around 1 or below — paying less than the growth
  rate for next year's expected earnings growth (10%+).
- **Moving**: decent price strength already (RS Rank 60+), above the 200-day line.
- **Trustworthy estimates**: at least 3 analysts covering. Profitable companies.
- **Size**: $500M and bigger. **Kinds**: normal stocks plus banks and insurance.
- **Risk**: moderate. **Results**: ~30-150 names.
- **Sorting**: momentum first, tiebreak value.
---
filter: trend_leaders
instructions:
Find stocks in a confirmed strong uptrend — the full Minervini trend template,
pure price action, no fundamentals at all.
- **Goal**: momentum. **Dividends / profits**: ignore completely — price only.
- **The template**: price above the 50-day line; 50-day above 150-day above
  200-day (the full stack); within 25% of the 52-week high; at least 30% above
  the 52-week low; RS Rank 80+.
- **Tradeable**: enough daily volume. **Size**: $300M and bigger.
- **Kinds**: normal stocks only. **Risk**: volatile is fine — that is the game.
- **Results**: whatever the market gives (~50-300 depending on conditions).
- **Sorting**: RS Rank first, tiebreak momentum score.
---
filter: estimate_upgrades
instructions:
Find stocks where the analysts keep raising their numbers — the earnings-momentum
screen (revisions plus a habit of beating estimates).
- **Goal**: earnings momentum. **Dividends**: ignore.
- **Upgrades in progress**: consensus EPS estimate raised over BOTH the last month
  and the last 3 months, with more analysts raising than cutting.
- **A habit of beating**: beat estimates in at least 3 of the last 4 quarters,
  positive average surprise.
- **Trustworthy**: at least 3 analysts. **Confirmation**: above the 200-day line.
- **Size**: $500M and bigger. **Kinds**: normal stocks, banks, insurance and REITs.
- **Risk**: moderate. **Results**: ~30-200 names.
- **Sorting**: momentum first, tiebreak the 1-month EPS revision itself.
---
filter: insider_conviction
instructions:
Find companies where the insiders are putting their own money in — net insider
BUYING (the research says buying is the informative side, and the signal is
strongest in smaller companies).
- **Goal**: follow the smart money. **Dividends**: ignore.
- **The signal**: insiders net buying over the last ~6 months.
- **Where it works**: small and mid caps, roughly $300M-$10B.
- **Not a falling knife**: some minimum price strength so I'm not catching
  collapses insiders are averaging into.
- **Kinds**: normal stocks, banks, insurance and REITs (the signal is actually
  strongest in banks). **Risk**: volatile OK. Pre-profit OK.
- **Results**: ~50-300 names. **Sorting**: growth first, tiebreak momentum.
---
filter: buyback_compounders
instructions:
Find companies steadily shrinking their own share count with real cash — the
net-buyback signal (quietly one of the best-evidenced return signals; dividends
alone predict little, buybacks do).
- **Goal**: shareholder-yield quality. **Dividends**: nice but not required.
- **The signal**: share count DOWN at least ~2% over the last year — real net
  buybacks, not buybacks eaten by stock-comp dilution.
- **Funded properly**: healthy free-cash-flow margin and solid returns on capital;
  debt under control (don't punish debt-free companies for missing debt data).
- **Profitable companies only.** **Size**: $1B and bigger. **Kinds**: normal
  stocks only. **Risk**: moderate.
- **Results**: ~30-150 names. **Sorting**: quality first, tiebreak value.
---
filter: financial_compounders
instructions:
Find quality compounders among banks and insurers — the group my
quality_compounders screen excludes on purpose, judged by THEIR rules: return on
equity and growing book value, not margins and current ratios.
- **Goal**: long-term growth in financials. **Kinds**: banks and insurance only.
- **Quality, their way**: ROE 12%+ (without crazy leverage doing the work).
- **Compounding, their way**: book value growing ~7%+ a year (earnings retained
  into book is how financials compound); forward earnings growth as a fallback.
- **Not overpriced**: price-to-book reasonable or below industry peers.
- **Proven**: above the 200-day line, decent RS Rank.
- **Dividends**: ignore for this screen. **Size**: $1B and bigger. **Risk**: moderate.
- **Results**: ~20-150 names. **Sorting**: quality first, tiebreak momentum.
---
