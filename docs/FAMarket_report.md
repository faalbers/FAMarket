# FAMarket Report — How Good Is the Screening System?

**What this is:** an honest review of FAMarket against published research on
what actually works (and doesn't) in stock screening.
Written 2026-07-28, based on web research into factor investing evidence.

**One-line verdict:** the system's *ingredients* are strongly evidence-backed —
better than most retail screeners — but it has **no feedback loop**: nothing
measures whether the filters actually pick winners. That is the biggest gap.

---

## 1. Scorecard

| Area | Grade | Why |
|---|---|---|
| Momentum / trend metrics | **A** | Matches the best-documented anomaly |
| Quality / profitability metrics | **A** | ROIC + margins = the proven "quality" factor |
| Earnings revisions + surprises | **A−** | Strong signal, but it has weakened over time |
| Screen-type gating (banks/REITs/funds) | **A** | Rare even in paid tools; prevents garbage results |
| Valuation metrics | **B+** | Good composite; P/B weak for modern companies |
| Insider / ownership signals | **B+** | Real signal, right direction (buys only) |
| Income metrics | **B** | FCF coverage is right; high yield alone is not |
| Classic technicals (RSI, MACD, Bollinger) | **C** | Little standalone evidence — fine as confirmation only |
| Performance feedback (does it work?) | **F** | Nothing tracks how filter picks perform afterwards |

---

## 2. What the system gets RIGHT (per the evidence)

**Momentum is the star — and you have it.**

- Momentum (buying recent winners) is the most robust anomaly in the
  literature, working across decades and across ~20 international markets.
- Your `rs_rank` (IBD-style weighted 12-month return) + the Minervini-style
  MA stack (`price_vs_ma_200`, MA50 > MA150 > MA200 via P-mode) is exactly
  the documented shape.
- `pct_from_52w_high` is its own proven anomaly (George & Hwang 2004):
  stocks **near** their 52-week high outperform — the opposite of the
  "buy the dip" instinct. Your momentum screens filtering `> -25` are right.

**Quality metrics match the "quality factor".**

- Novy-Marx showed gross profitability predicts returns about as well as
  the classic value factor. Your `gross_margin`, `roic`, margin **trends**
  and steadiness stats (`growth_r2`, `growth_cv`) sit right on this.
- Key research finding: quality and value are largely **independent** —
  combining them (as your `quality_score` + `value_score` do) is where the
  real edge is.

**Earnings revisions are one of the strongest short-horizon signals.**

- Upward analyst estimate revisions (`eps_revision_1m/3m`, breadth) are the
  engine behind the well-audited Zacks Rank.
- Earnings surprises (`earnings_surprise_avg/last`) exploit post-earnings
  drift — a real, long-documented effect.

**Buybacks — you have the right metric.**

- Research: net share **reduction** predicts better returns; heavy net
  issuance predicts worse. Dividends alone have little predictive power.
- Your `share_count_chg_1y < 0` filter is exactly the evidenced signal —
  it catches net issuance that a "gross buyback" headline would hide.

**Structural strengths most screeners don't have:**

- **Screen-type gating** — hiding P/E for REITs, EV/EBITDA for banks —
  prevents the classic beginner error of comparing invalid metrics.
- **Peer-relative variants** (`_vs_sector`, `_vs_industry`) — multiples are
  only meaningful within an industry; the research is unambiguous here.
- **N/A fallback discipline** (OR `is null` children) — avoids silently
  excluding healthy no-debt companies or young growers.
- **Forensic gates** (Beneish M, Altman Z) with correct absolute cutoffs
  and correct sector exclusions.

---

## 3. Where the evidence says "be careful"

**Anomalies fade once published.**

- Landmark study (McLean & Pontiff): anomaly returns drop **~58% after
  publication** as investors trade them away.
- Post-earnings drift specifically has weakened as markets got faster.
- Lesson: expect *modest* edges, not the returns old papers show.
  Combining several weak-but-real signals beats leaning on one.

**Classic technicals have weak standalone evidence.**

- Studies find RSI and MACD alone perform near random; MACD crossovers won
  ~40% of the time in tests.
- Your system already treats them as confirmation, not triggers — good.
  Just never build a filter where RSI/MACD is the main gate.

**High dividend yield is not a return signal.**

- Evidence: dividend yield alone has *negligible* predictive power;
  **total shareholder yield** (dividends + net buybacks) works much better.
- Your income screens are for income — fine. But don't expect high-yield
  filters to find outperformers; pair them with `div_coverage` (which you
  have, and which the evidence supports over payout ratio).

**P/B is fading for modern companies.**

- Book value misses intangibles (software, brands, R&D), so P/B works
  mainly for financials and real estate — exactly where your registry
  applies it. Just avoid leaning on P/B for standard stocks.

**`orphan_score` — the neglected-firm effect is the shakiest bet.**

- The original effect (low-coverage stocks outperform) is old, and later
  research finds it weaker or absorbed by size/liquidity effects.
- Keep it as an *idea generator*, not a conviction signal. The solvency
  gate you added is the right protection.

**`rs_rank` includes the most recent month.**

- Academic momentum skips the latest month (short-term reversal: last
  month's jumpers often pull back).
- IBD's version (yours) includes it and works fine for *screening* —
  but very recent spikes can rank a stock high right before a pullback.
  Recent research is mixed, so this is a nuance, not a flaw.

---

## 4. The big gap — nothing measures results

**The question "does this filter find winners?" has no answer today.**

- Filters are built from evidence, but never *verified* on your own data.
- Run files (`results/`) already store dated symbol lists — the raw
  material exists; nothing computes what happened next.

**Why a classic backtest is the wrong first step here:**

- Honest backtesting needs **point-in-time** data (what the fundamentals
  looked like *on that date*) and **delisted stocks** included.
- analysis.db is a snapshot — backtesting on it would bake in look-ahead
  and survivorship bias (studies show survivorship alone inflates returns
  by 1–4%/year). A misleading backtest is worse than none.

**The right first step: forward-testing (paper tracking).**

- Every Run Filter already saves a dated result. Add a small tool that,
  weeks/months later, computes each pick's return since the run date vs
  a benchmark (e.g. the sector index you already build in indices.db).
- No point-in-time problem — the snapshot WAS the data at pick time.
- After a few months you'd have a real answer per filter: hit rate,
  average return vs sector, best/worst picks.
- This turns Filter Fail's *threshold* feedback loop into a *performance*
  feedback loop — the missing half.

---

## 5. Improvements worth adding (ranked)

**1. Forward-performance tracking** (see above) — the highest-value addition
by far. Everything else is refinement; this is verification.

**2. Ranking mode instead of hard cutoffs.**
- Hard AND gates throw away a stock that misses one threshold by 1%.
- Research (e.g. Piotroski & So) shows *ranking* stocks jointly on
  value + quality dramatically outperforms pass/fail screens.
- Already parked in ROADMAP Future Ideas ("ranking/soft-score mode") —
  the evidence says it deserves promotion. The `_goodness` columns are
  ready-made inputs: a filter could rank by a weighted goodness blend
  instead of gating.

**3. Piotroski F-Score.**
- Nine binary checks (profitability, leverage, efficiency); decades of
  evidence, especially strong when combined with cheap valuation.
- You already have `quick_health_score` (7 checks, same spirit) — adding
  the actual F-Score would let screens use the tested version, and all
  inputs are already in financials.db.

**4. Gross profitability (GP / total assets).**
- The specific Novy-Marx ratio — not the same as gross *margin*
  (GP / revenue), which you have. One column, inputs already stored.

**5. Total shareholder yield.**
- `div_yield_ttm` + buyback yield in one number — the evidenced upgrade
  over dividend yield alone. You have both halves; combine them.

**6. Asset-growth check.**
- Firms growing total assets fastest tend to underperform ("investment"
  factor — in Fama-French's own 5-factor model). You store total_assets
  history; a 1y asset-growth column would flag empire-builders.

**7. A 12-1 momentum variant** (skip the latest month) alongside rs_rank —
cheap to add from data you already store, closes the §3 nuance.

---

## 6. Bottom line

- **Ingredient quality: strong.** The metric set covers all five factor
  families the literature supports (momentum, quality, value, revisions,
  shareholder yield) with correct per-type gating — genuinely better
  grounded than typical retail screeners.
- **Expectations: keep them modest.** Published edges shrink; screens
  narrow the field, they don't guarantee winners.
- **The one thing to build next: forward-performance tracking.** Until
  filter picks are measured against what happened afterwards, "how well
  does FAMarket find good stocks?" can only be answered by theory —
  after it, by your own data.

---

## Sources

**Momentum & 52-week high**
- [Quantpedia — Momentum factor effect in stocks](https://quantpedia.com/strategies/momentum-factor-effect-in-stocks)
- [Alpha Architect — The skip-month mystery](https://alphaarchitect.com/skip-month-mystery/)
- [George & Hwang (2004) — The 52-Week High and Momentum Investing](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.2004.00695.x)
- [52-week high momentum in international markets](https://www.sciencedirect.com/science/article/abs/pii/S0261560610001099)
- [Springer — Momentum: 30 years after Jegadeesh & Titman](https://link.springer.com/article/10.1007/s11408-022-00417-8)

**Quality & F-Score**
- [Novy-Marx — The Quality Dimension of Value Investing](https://www.ivey.uwo.ca/media/3775548/novy-marx.pdf)
- [Novy-Marx — Gross profitability premium](https://www.researchgate.net/publication/46466978_The_Other_Side_of_Value_Good_Growth_and_the_Gross_Profitability_Premium)
- [NBIM — The Quality Factor (QMJ evidence)](https://www.nbim.no/contentassets/0660d8c611f94980ab0d33930cb2534e/nbim_discussionnotes_3-15.pdf)
- [Quant Decoded — Why profitable firms deliver higher returns](https://quantdecoded.com/en/the-quality-factor-why-profitable-firms-deliver-higher-returns)

**Anomaly decay & earnings signals**
- [McLean & Pontiff — Does academic research destroy return predictability?](https://www.researchgate.net/publication/254926004_Does_Academic_Research_Destroy_Stock_Return_Predictability)
- [ScienceDirect — A review of post-earnings-announcement drift](https://www.sciencedirect.com/science/article/pii/S2214635020303750)
- [ScienceDirect — Reviving PEAD with machine learning](https://www.sciencedirect.com/science/article/abs/pii/S1544612325020057)
- [Quantpedia — Post-earnings announcement effect](https://quantpedia.com/strategies/post-earnings-announcement-effect)

**Technicals**
- [Revisiting the performance of MACD and RSI oscillators](https://www.researchgate.net/publication/276039141_Revisiting_the_Performance_of_MACD_and_RSI_Oscillators)
- [Lund University — Profitability of technical indicators (empirical)](https://lup.lub.lu.se/luur/download?func=downloadFile&recordOId=8905915&fileOId=8905916)

**Shareholder yield & dividends**
- [Morningstar — Why total shareholder yield matters more than dividends](https://www.morningstar.com/stocks/why-total-shareholder-yield-matters-more-than-dividends)
- [Morgan Stanley — Total shareholder return](https://www.morganstanley.com/im/publication/insights/articles/article_totalshareholderreturns.pdf)
- [O'Shaughnessy — Shareholder yield in an efficient market](https://osam.com/Commentary/shareholder-yield-a-differentiated-approach-to-an-efficient-market)

**Valuation & neglected firms**
- [CFA Institute — Price and enterprise value multiples](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/market-based-valuation-price-enterprise-value-multiples)
- [Wikipedia — Neglected firm effect](https://en.wikipedia.org/wiki/Neglected_firm_effect)
- [ScienceDirect — Analysts and anomalies](https://www.sciencedirect.com/science/article/abs/pii/S0165410119300448)

**Backtesting pitfalls**
- [LuxAlgo — Survivorship bias in backtesting](https://www.luxalgo.com/blog/survivorship-bias-in-backtesting-explained/)
- [QuantifiedStrategies — Survivorship bias and how to avoid it](https://www.quantifiedstrategies.com/survivorship-bias-backtesting/)
- [Quantpedia — In-sample vs out-of-sample analysis](https://quantpedia.com/in-sample-vs-out-of-sample-analysis-of-trading-strategies/)
