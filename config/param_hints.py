"""
Hover-hint definitions for every screening parameter (Topics 4.2 & 5).

UI shows SHORT parameter names; the full meaning lives here and is shown as a
tooltip on hover (~0.5-1s delay) via Streamlit's `help=` argument. Editable by
hand or via Claude Code without touching any UI code.

Each hint is a structured dict:
  - name         : short label shown in the UI
  - category     : group it belongs to (Valuation/Technical/Income/...)
  - unit         : unit of the value AS STORED in analysis.db (see below)
  - what_it_is   : plain-language definition
  - how_to_use   : what good/bad values look like and the action they imply
  - vs_peers     : whether/why to compare against sector/industry median

`unit` convention — every param declares one so the UI can render and compare
correctly (append "%", align columns, format axes). Controlled vocabulary:
  - "%"  percentage. Stored AS A PERCENT NUMBER, not a fraction: 12.5 means
         12.5%, not 0.125. metrics.py multiplies yfinance's decimal margins/
         ratios (ROE, margins, yields, growth) by 100 on write, matching
         ROADMAP's "... x 100" yield formulas.
  - "x"  a multiple / ratio (P/E, P/S, EV/EBITDA, D/E) — unitless count of times.
  - "$"  currency per share or absolute (price, intrinsic value, EPS).
  - "yr" a count of years (consecutive dividend-growth years).
  - ""   unitless index or text classification (RSI 0-100, trend, crossover).

The UI renders titles bold with indented body text; use a list for body when
multiple points need explaining. Growth bases (revenue/eps/fcf/book_value) carry
ONE hint each — it covers all their windows (CAGR 1/3/5y, YoY quarter, trend
stats); the R² window is the only unitless one in an otherwise "%" family.

How the hint is turned into on-screen TEXT lives here too, in ONE place:
`hint_markdown()` (for Streamlit `help=` tooltips, the Parameter Reference page,
any `st.markdown`) and `hint_html()` (for the picker's inline info panel). Both
apply the same dyslexia-friendly shape — bold title line, a short "what it is"
sentence, bulleted "how to use", a "Peers:" line, generous white space. Pages
MUST call these rather than re-formatting the dict, so the style is defined once
and never re-commented per page.
"""

from __future__ import annotations

import html as _html

# param_key -> {"name": short label, "category": group, + 3 hint sections}
PARAM_HINTS: dict[str, dict] = {
    # ------------------------------------------------------------------ #
    # Price
    # ------------------------------------------------------------------ #
    "price": {
        "name": "Price",
        "category": "Price",
        "unit": "$",
        "what_it_is": "Adjusted close of the last completed trading session (never intraday).",
        "how_to_use": [
            "The reference price for every price-based metric in the system.",
            "Useful as a floor filter (e.g. > 5) to drop penny stocks.",
        ],
        "vs_peers": "No — raw price says nothing about value; use the multiples instead.",
    },
    # ------------------------------------------------------------------ #
    # Valuation
    # ------------------------------------------------------------------ #
    "pe": {
        "name": "P/E",
        "category": "Valuation",
        "unit": "x",
        "what_it_is": "Price divided by trailing 12-month earnings per share.",
        "how_to_use": [
            "Lower can mean cheaper, but very low may signal trouble.",
            "Negative means no earnings — read alongside growth and margins.",
        ],
        "vs_peers": "Yes — P/E is only meaningful relative to sector/industry.",
    },
    "forward_pe": {
        "name": "Fwd P/E",
        "category": "Valuation",
        "unit": "x",
        "what_it_is": "Price divided by analysts' expected next-12-month EPS (estimate data).",
        "how_to_use": [
            "Well below trailing P/E means analysts expect earnings to grow.",
            "It is a forecast — treat it as opinion, not fact.",
        ],
        "vs_peers": "Yes — compare within sector/industry, like trailing P/E.",
    },
    "peg": {
        "name": "PEG",
        "category": "Valuation",
        "unit": "x",
        "what_it_is": "Trailing P/E divided by the 3-year EPS growth rate (in %).",
        "how_to_use": [
            "Around 1 is fairly priced for its growth (Lynch's rule of thumb).",
            "Under 1 suggests growth at a discount; NULL when growth is zero or negative.",
        ],
        "vs_peers": "Somewhat — it already adjusts for growth, but norms still differ by sector.",
    },
    "pb": {
        "name": "P/B",
        "category": "Valuation",
        "unit": "x",
        "what_it_is": "Market cap divided by book value (shareholders' equity).",
        "how_to_use": [
            "The core multiple for banks, insurers and REITs.",
            "Under 1 means priced below accounting net worth — check why.",
        ],
        "vs_peers": "Yes — book intensity differs hugely between industries.",
    },
    "ps": {
        "name": "P/S",
        "category": "Valuation",
        "unit": "x",
        "what_it_is": "Market cap divided by trailing 12-month revenue.",
        "how_to_use": [
            "Works when earnings are negative (early growers, cyclical troughs).",
            "Only comparable between businesses with similar margins.",
        ],
        "vs_peers": "Yes — a software P/S and a grocer P/S live on different planets.",
    },
    "p_fcf": {
        "name": "P/FCF",
        "category": "Valuation",
        "unit": "x",
        "what_it_is": "Market cap divided by trailing 12-month free cash flow (operating cash flow + capex).",
        "how_to_use": [
            "Like P/E but on cash, which is harder to massage than earnings.",
            "Lower is cheaper; negative FCF makes it NULL.",
        ],
        "vs_peers": "Yes — capital intensity sets very different FCF norms per industry.",
    },
    "ev_ebitda": {
        "name": "EV/EBITDA",
        "category": "Valuation",
        "unit": "x",
        "what_it_is": "Enterprise value (market cap + debt − cash) over trailing EBITDA.",
        "how_to_use": [
            "Capital-structure neutral — good for comparing leveraged vs unleveraged firms.",
            "Roughly: under ~8 cheap, over ~15 expensive — but very sector-dependent.",
        ],
        "vs_peers": "Yes — always read against the sector/industry median.",
    },
    "ev_revenue": {
        "name": "EV/Rev",
        "category": "Valuation",
        "unit": "x",
        "what_it_is": "Enterprise value over trailing 12-month revenue.",
        "how_to_use": [
            "The EV twin of P/S; useful when EBITDA is negative.",
            "Lower is cheaper for comparable margin profiles.",
        ],
        "vs_peers": "Yes — only meaningful within the same industry's margin range.",
    },
    "eps_ttm": {
        "name": "EPS TTM",
        "category": "Valuation",
        "unit": "$",
        "what_it_is": "Trailing 12-month net income divided by shares outstanding.",
        "how_to_use": [
            "The raw earnings input behind P/E and the intrinsic-value models.",
            "Negative EPS disables P/E, PEG, Graham and Lynch values.",
        ],
        "vs_peers": "No — it's a per-share dollar amount; compare via P/E instead.",
    },
    # ------------------------------------------------------------------ #
    # Profitability
    # ------------------------------------------------------------------ #
    "roe": {
        "name": "ROE",
        "category": "Profitability",
        "unit": "%",
        "what_it_is": "Return on equity: trailing net income / shareholders' equity × 100.",
        "how_to_use": [
            "15%+ sustained is a quality marker; the key metric for banks/insurers.",
            "Very high ROE with high debt is leverage, not skill — check D/E.",
        ],
        "vs_peers": "Yes — normal ROE differs by industry; beat the median.",
    },
    "roa": {
        "name": "ROA",
        "category": "Profitability",
        "unit": "%",
        "what_it_is": "Return on assets: trailing net income / total assets × 100.",
        "how_to_use": [
            "Leverage-proof sibling of ROE; for banks ~1%+ is solid.",
            "Rising ROA over time means the asset base is working harder.",
        ],
        "vs_peers": "Yes — asset-light vs asset-heavy industries differ by an order of magnitude.",
    },
    "roic": {
        "name": "ROIC",
        "category": "Profitability",
        "unit": "%",
        "what_it_is": "Return on invested capital: after-tax operating profit (NOPAT) / invested capital × 100.",
        "how_to_use": [
            "The cleanest 'does this business create value' number.",
            "Above ~10% (the rough cost of capital) creates value; below destroys it.",
        ],
        "vs_peers": "Yes — but the 10% cost-of-capital bar is universal.",
    },
    "gross_margin": {
        "name": "Gross mgn",
        "category": "Profitability",
        "unit": "%",
        "what_it_is": "Gross profit / revenue × 100 — what's left after the direct cost of goods.",
        "how_to_use": [
            "High and stable gross margin signals pricing power.",
            "A falling trend is an early warning before it reaches the bottom line.",
        ],
        "vs_peers": "Yes — only comparable within the same industry.",
    },
    "operating_margin": {
        "name": "Op mgn",
        "category": "Profitability",
        "unit": "%",
        "what_it_is": "Operating income (EBIT) / revenue × 100 — profit after running costs, before interest and tax.",
        "how_to_use": [
            "The core measure of business efficiency.",
            "Compare its trend over years; expansion beats a high static number.",
        ],
        "vs_peers": "Yes — read against the industry median.",
    },
    "net_margin": {
        "name": "Net mgn",
        "category": "Profitability",
        "unit": "%",
        "what_it_is": "Net income / revenue × 100 — the share of sales that survives everything.",
        "how_to_use": [
            "The bottom-line margin; watch for one-off items distorting a single year.",
            "Pair with gross/operating margin to see where profit leaks.",
        ],
        "vs_peers": "Yes — industry norms range from ~2% (retail) to ~30% (software).",
    },
    "fcf_margin": {
        "name": "FCF mgn",
        "category": "Profitability",
        "unit": "%",
        "what_it_is": "Free cash flow / revenue × 100 — how much of each sales dollar becomes spendable cash.",
        "how_to_use": [
            "10%+ is strong for most industries.",
            "Net margin high but FCF margin low? Earnings may be ahead of cash — investigate.",
        ],
        "vs_peers": "Yes — capital intensity drives the achievable level.",
    },
    # ------------------------------------------------------------------ #
    # Balance Sheet
    # ------------------------------------------------------------------ #
    "debt_to_equity": {
        "name": "D/E",
        "category": "Balance Sheet",
        "unit": "x",
        "what_it_is": "Total debt divided by shareholders' equity (stored as a ratio, not percent).",
        "how_to_use": [
            "Under ~0.5 conservative, 1-2 typical, above ~2 leveraged.",
            "REITs run higher by design — judge them against REIT norms.",
        ],
        "vs_peers": "Yes — acceptable leverage is an industry convention.",
    },
    "debt_to_ebitda": {
        "name": "Debt/EBITDA",
        "category": "Balance Sheet",
        "unit": "x",
        "what_it_is": "Total debt divided by trailing EBITDA — years of cash earnings to repay all debt.",
        "how_to_use": [
            "Under 2 comfortable, 2-4 manageable, above 4-5 strained (lenders' own yardstick).",
            "More telling than D/E when equity is distorted by buybacks.",
        ],
        "vs_peers": "Yes — stable-cash-flow industries can safely carry more.",
    },
    "interest_coverage": {
        "name": "Int cover",
        "category": "Balance Sheet",
        "unit": "x",
        "what_it_is": "EBIT divided by interest expense — how many times profit covers the interest bill.",
        "how_to_use": [
            "Above ~5 comfortable; under ~2 means little room for a bad year.",
            "Falling coverage with rising rates is a squeeze in progress.",
        ],
        "vs_peers": "Somewhat — the absolute danger zone (<2) matters more than the median.",
    },
    "current_ratio": {
        "name": "Current",
        "category": "Balance Sheet",
        "unit": "x",
        "what_it_is": "Current assets / current liabilities — can it pay the next 12 months' bills?",
        "how_to_use": [
            "1.5-3 is healthy; under 1 means obligations exceed liquid-ish assets.",
            "Very high can also mean idle, unproductive capital.",
        ],
        "vs_peers": "Somewhat — fast-inventory businesses run safely below 1.",
    },
    "quick_ratio": {
        "name": "Quick",
        "category": "Balance Sheet",
        "unit": "x",
        "what_it_is": "The acid test: (cash + short-term investments + receivables) / current liabilities.",
        "how_to_use": [
            "Like the current ratio but ignores inventory that may not sell.",
            "Around 1 is the classic comfort line.",
        ],
        "vs_peers": "Somewhat — same caveats as the current ratio.",
    },
    "cash_ratio": {
        "name": "Cash ratio",
        "category": "Balance Sheet",
        "unit": "x",
        "what_it_is": "Cash and equivalents alone / current liabilities — the strictest liquidity test.",
        "how_to_use": [
            "Few companies hold 1+; 0.2-0.5 is normal.",
            "Use it to find fortress balance sheets, not to disqualify on a low value.",
        ],
        "vs_peers": "No — read it as an absolute safety cushion.",
    },
    "altman_z": {
        "name": "Altman Z",
        "category": "Balance Sheet",
        "unit": "",
        "what_it_is": "Bankruptcy-risk score blending working capital, retained earnings, EBIT, market cap and sales vs assets/liabilities.",
        "how_to_use": [
            "Above 3 = safe zone, 1.8-3 = grey zone, below 1.8 = distress zone.",
            "Designed for manufacturers — take it lightly for other business models.",
        ],
        "vs_peers": "No — the zone thresholds are absolute.",
    },
    # ------------------------------------------------------------------ #
    # Growth (one hint per base; covers all windows: CAGR 1/3/5y, YoY
    # quarter, growth volatility %, trend R² (0-1), variability CV %)
    # ------------------------------------------------------------------ #
    "revenue": {
        "name": "Rev growth",
        "category": "Growth",
        "unit": "%",
        "what_it_is": "Growth of annual total revenue. Windows: 1/3/5-year CAGR, latest quarter vs the same quarter last year (YoY), plus trend quality over ~5 years.",
        "how_to_use": [
            "CAGR = smoothed yearly pace; YoY quarter = what's happening right now.",
            "Volatility % and CV % low + R² near 1 = steady grower; high vol = lumpy or cyclical.",
            "R² is 0-1 (fit quality), not a percent.",
        ],
        "vs_peers": "Yes — growth is only impressive against the industry's pace.",
    },
    "eps": {
        "name": "EPS growth",
        "category": "Growth",
        "unit": "%",
        "what_it_is": "Growth of split-adjusted diluted EPS. Same windows as revenue growth (CAGR 1/3/5y, YoY quarter, trend stats).",
        "how_to_use": [
            "EPS growing faster than revenue = margin expansion or buybacks.",
            "Erratic EPS (high vol, low R²) makes P/E and PEG less trustworthy.",
        ],
        "vs_peers": "Yes — compare pace and steadiness within the industry.",
    },
    "fcf": {
        "name": "FCF growth",
        "category": "Growth",
        "unit": "%",
        "what_it_is": "Growth of free cash flow (operating cash flow + capex), derived from deep annual history. Same windows as revenue growth.",
        "how_to_use": [
            "The cash check on EPS growth — both should roughly agree over time.",
            "Feeds the DCF intrinsic value (5y CAGR, capped at 15%).",
        ],
        "vs_peers": "Yes — but steadiness (R², CV) matters more than raw pace.",
    },
    "book_value": {
        "name": "BV growth",
        "category": "Growth",
        "unit": "%",
        "what_it_is": "Growth of shareholders' equity (book value). Same windows as revenue growth.",
        "how_to_use": [
            "THE growth metric for banks and insurers (their earnings compound into book).",
            "Steady ~10%+ book growth plus reasonable P/B is the classic financial-sector screen.",
        ],
        "vs_peers": "Yes — compare within banks/insurance specifically.",
    },
    # ------------------------------------------------------------------ #
    # Income
    # ------------------------------------------------------------------ #
    "div_yield_ttm": {
        "name": "Yield TTM",
        "category": "Income",
        "unit": "%",
        "what_it_is": "Sum of dividends paid in the last 365 days / current price.",
        "how_to_use": [
            "Higher pays more income; unusually high can signal a falling price.",
            "Pair with payout ratio and consecutive growth years for safety.",
            "Income is an absolute goal — what counts is the actual yield you collect, "
            "not whether it's high 'for the sector'.",
        ],
        "vs_peers": "For screening, sector norms differ — but for income the absolute "
                    "level is what matters most (a sector-high 0.5% is still tiny).",
    },
    "div_rate_ttm": {
        "name": "Div rate",
        "category": "Income",
        "unit": "$",
        "what_it_is": "Dollars of dividend paid per share over the last 365 days. 0 for non-payers.",
        "how_to_use": [
            "The absolute payout behind the yield — yield = rate / price.",
            "Use `> 0` as the simplest 'pays a dividend' filter.",
        ],
        "vs_peers": "No — it's a per-share dollar amount; compare yields instead.",
    },
    "div_growth_5y": {
        "name": "Div gr 5y",
        "category": "Income",
        "unit": "%",
        "what_it_is": "CAGR of the annual dividend total over up to 5 complete calendar years (current partial year excluded).",
        "how_to_use": [
            "5-10%+ with a safe payout ratio is the dividend-growth sweet spot.",
            "Negative means the payout shrank — check for cuts.",
        ],
        "vs_peers": "Yes — utilities grow slowly from high yields; tech fast from low ones.",
    },
    "div_consecutive_years": {
        "name": "Div streak",
        "category": "Income",
        "unit": "yr",
        "what_it_is": "Number of consecutive complete years the annual dividend total increased.",
        "how_to_use": [
            "Long streaks (10/25+ = achiever/aristocrat territory) signal management commitment.",
            "Limited by our price-history depth — treat it as 'at least this many'.",
        ],
        "vs_peers": "No — a streak is meaningful on its own.",
    },
    "div_consistency": {
        "name": "Div consist",
        "category": "Income",
        "unit": "%",
        "what_it_is": "Share of year-over-year steps where the annual dividend held or grew (100 = never cut in our history).",
        "how_to_use": [
            "100 = no cuts; 80 = cut roughly one year in five.",
            "Softer than the streak: one old cut doesn't zero it out.",
        ],
        "vs_peers": "No — read it as an absolute reliability score.",
    },
    "div_payout_ratio": {
        "name": "Payout",
        "category": "Income",
        "unit": "%",
        "what_it_is": "Dividends paid / trailing net income × 100 — the slice of profit paid out.",
        "how_to_use": [
            "30-60% is the comfortable band for most companies.",
            "Above ~80% leaves little safety; above 100% is paid from reserves or debt.",
            "REITs are legally high payers — judge them separately.",
        ],
        "vs_peers": "Yes — sustainable levels are industry-specific.",
    },
    "div_coverage": {
        "name": "Div cover",
        "category": "Income",
        "unit": "x",
        "what_it_is": "Free cash flow / dividends paid — how many times cash flow covers the dividend.",
        "how_to_use": [
            "Above 2 comfortable, 1-2 watch, under 1 the dividend isn't cash-funded.",
            "The cash-based mirror of the payout ratio; trust it more.",
        ],
        "vs_peers": "No — coverage is an absolute safety measure.",
    },
    # ------------------------------------------------------------------ #
    # Technical
    # ------------------------------------------------------------------ #
    "price_vs_ma_50": {
        "name": "vs MA50",
        "category": "Technical",
        "unit": "%",
        "what_it_is": "How far price sits above (+) or below (−) its 50-day moving average, in percent.",
        "how_to_use": [
            "Positive = short-term uptrend; far above (>15-20%) = possibly extended.",
            "Classic momentum setups want price above MA50, MA50 above MA200.",
        ],
        "vs_peers": "No — it's self-relative.",
    },
    "price_vs_ma_150": {
        "name": "vs MA150",
        "category": "Technical",
        "unit": "%",
        "what_it_is": "How far price sits above (+) or below (−) its 150-day moving average, in percent.",
        "how_to_use": [
            "The medium-term trend check, between MA50 (fast) and MA200 (slow).",
            "Part of the Minervini-style trend template (price above a rising MA150).",
        ],
        "vs_peers": "No — it's self-relative.",
    },
    "price_vs_ma_200": {
        "name": "vs MA200",
        "category": "Technical",
        "unit": "%",
        "what_it_is": "How far price sits above (+) or below (−) its 200-day moving average, in percent.",
        "how_to_use": [
            "The classic bull/bear line: above = long-term uptrend.",
            "Many screens simply require > 0 here as a health gate.",
        ],
        "vs_peers": "No — it's self-relative.",
    },
    "ma_50": {
        "name": "MA50",
        "category": "Technical",
        "unit": "$",
        "what_it_is": "50-day moving average of adjusted close — the short-term trend line.",
        "how_to_use": [
            "Mainly useful via 'price vs MA50' or compared to MA150/MA200 (P-mode).",
            "MA50 above MA200 = golden-cross regime.",
        ],
        "vs_peers": "No — it's a price level.",
    },
    "ma_150": {
        "name": "MA150",
        "category": "Technical",
        "unit": "$",
        "what_it_is": "150-day moving average of adjusted close — the medium-term trend line.",
        "how_to_use": ["Use with P-mode comparisons (e.g. MA50 > MA150 > MA200 stacks a trend template)."],
        "vs_peers": "No — it's a price level.",
    },
    "ma_200": {
        "name": "MA200",
        "category": "Technical",
        "unit": "$",
        "what_it_is": "200-day moving average of adjusted close — the long-term trend line.",
        "how_to_use": ["The institutional reference line; price holding above it defines a long-term uptrend."],
        "vs_peers": "No — it's a price level.",
    },
    "rsi_14": {
        "name": "RSI(14)",
        "category": "Technical",
        "unit": "",
        "what_it_is": "14-day Relative Strength Index, momentum on a 0-100 scale.",
        "how_to_use": [
            ">70 often overbought, <30 often oversold.",
            "For long-term screening, use as confirmation, not a trigger.",
        ],
        "vs_peers": "No — RSI is self-relative, not a peer comparison.",
    },
    "macd_line": {
        "name": "MACD",
        "category": "Technical",
        "unit": "$",
        "what_it_is": "MACD line: 12-day EMA minus 26-day EMA of adjusted close (in price units).",
        "how_to_use": [
            "Positive = short-term momentum above long-term; sign matters more than size.",
            "Mostly read via the crossover and histogram-trend params instead.",
        ],
        "vs_peers": "No — and don't compare across symbols (it scales with price).",
    },
    "macd_signal": {
        "name": "MACD sig",
        "category": "Technical",
        "unit": "$",
        "what_it_is": "9-day EMA of the MACD line — the slower trigger line.",
        "how_to_use": ["MACD line crossing above it is the classic buy signal; see 'MACD crossover'."],
        "vs_peers": "No — same scaling caveat as the MACD line.",
    },
    "macd_hist": {
        "name": "MACD hist",
        "category": "Technical",
        "unit": "$",
        "what_it_is": "MACD line minus signal line — momentum of the momentum.",
        "how_to_use": [
            "Positive and growing = strengthening move; shrinking = losing steam.",
            "Zero crossings are the crossover events.",
        ],
        "vs_peers": "No — scales with price.",
    },
    "macd_crossover": {
        "name": "MACD cross",
        "category": "Technical",
        "unit": "",
        "what_it_is": "Text flag: 'bullish' or 'bearish' if the histogram crossed zero within the last 5 sessions, else 'none'.",
        "how_to_use": [
            "Filter `= bullish` to catch fresh momentum turns.",
            "Crossovers in choppy sideways price action whipsaw — confirm with trend.",
        ],
        "vs_peers": "No — it's an event flag.",
    },
    "macd_hist_trend": {
        "name": "Hist trend",
        "category": "Technical",
        "unit": "",
        "what_it_is": "Text flag from the recent histogram slope: 'growing', 'shrinking' or 'flat'.",
        "how_to_use": [
            "'growing' = momentum building, often before a bullish crossover.",
            "'shrinking' while price still rises = early divergence warning.",
        ],
        "vs_peers": "No — it's a state flag.",
    },
    "bb_pct": {
        "name": "%B",
        "category": "Technical",
        "unit": "",
        "what_it_is": "Position inside the Bollinger bands (20-day, 2σ): 0 = lower band, 1 = upper band; can exceed either.",
        "how_to_use": [
            ">1 = closed above the upper band (strong but stretched); <0 = below the lower.",
            "Mean-reversion screens buy near 0 in an uptrend.",
        ],
        "vs_peers": "No — self-relative by construction.",
    },
    "bb_width": {
        "name": "BB width",
        "category": "Technical",
        "unit": "$",
        "what_it_is": "Distance between the upper and lower Bollinger bands, in price units.",
        "how_to_use": [
            "Narrow = quiet price, often before a move; wide = high volatility.",
            "Scales with price — use 'BB squeeze' to compare tightness fairly.",
        ],
        "vs_peers": "No — use the squeeze flag instead.",
    },
    "bb_position": {
        "name": "BB pos",
        "category": "Technical",
        "unit": "",
        "what_it_is": "Text version of %B: 'above_upper', 'near_upper', 'middle', 'near_lower', 'below_lower'.",
        "how_to_use": ["Easier to filter than raw %B — e.g. `= near_lower` for pullback candidates."],
        "vs_peers": "No — it's a state flag.",
    },
    "bb_squeeze": {
        "name": "BB squeeze",
        "category": "Technical",
        "unit": "",
        "what_it_is": "True when band width (normalized by price) is in the tightest 20% of the last ~6 months.",
        "how_to_use": [
            "A squeeze marks unusually quiet price — breakouts often follow.",
            "It doesn't say which direction; pair with trend or MACD.",
        ],
        "vs_peers": "No — it's already normalized per symbol.",
    },
    "pct_from_52w_high": {
        "name": "vs 52w Hi",
        "category": "Technical",
        "unit": "%",
        "what_it_is": "Distance from the 52-week high: 0 = at the high, −30 = 30% below it.",
        "how_to_use": [
            "Momentum screens want close to 0 (e.g. > −15).",
            "Deep value/turnaround screens hunt far below (e.g. < −50) — riskier.",
        ],
        "vs_peers": "No — self-relative.",
    },
    "pct_from_52w_low": {
        "name": "vs 52w Lo",
        "category": "Technical",
        "unit": "%",
        "what_it_is": "Distance above the 52-week low: 0 = at the low, +80 = 80% above it.",
        "how_to_use": [
            "Strong stocks typically sit well off their lows (e.g. > 30).",
            "Near 0 means the market is still voting against it.",
        ],
        "vs_peers": "No — self-relative.",
    },
    "trend": {
        "name": "Trend",
        "category": "Technical",
        "unit": "",
        "what_it_is": "Swing-point trend over the last year via peak detection: 'strong_uptrend', 'weak_uptrend', 'sideways', 'weak_downtrend', 'strong_downtrend'.",
        "how_to_use": [
            "Strong = higher highs AND higher lows (or lower/lower for down).",
            "Tune the peak detection in Settings → Peak-detection calibration if calls look off.",
        ],
        "vs_peers": "No — it's a per-symbol classification.",
    },
    "vol_20d_avg": {
        "name": "Avg vol",
        "category": "Technical",
        "unit": "",
        "what_it_is": "Average daily share volume over the last 20 sessions (a share count).",
        "how_to_use": [
            "A liquidity gate: very low volume = wide spreads, hard exits.",
            "E.g. require > 100000 to keep tradeable names only.",
        ],
        "vs_peers": "No — it's an absolute liquidity measure.",
    },
    "vol_ratio": {
        "name": "Vol ratio",
        "category": "Technical",
        "unit": "x",
        "what_it_is": "Last session's volume divided by the 20-day average volume.",
        "how_to_use": [
            "≈1 normal day; >2 = unusual attention (news, breakout, earnings).",
            "High volume confirms a price move; low volume undercuts it.",
        ],
        "vs_peers": "No — already normalized per symbol.",
    },
    "vol_trend": {
        "name": "Vol trend",
        "category": "Technical",
        "unit": "",
        "what_it_is": "20-day average volume vs the same average 20 sessions earlier: 'increasing' (>+10%), 'decreasing' (<−10%) or 'flat'.",
        "how_to_use": [
            "Rising volume in an uptrend = accumulation; in a downtrend = distribution.",
            "Fading volume on a rally questions its strength.",
        ],
        "vs_peers": "No — it's a state flag.",
    },
    "atr_pct": {
        "name": "ATR %",
        "category": "Technical",
        "unit": "%",
        "what_it_is": "14-day Average True Range as a percent of price — typical daily movement size.",
        "how_to_use": [
            "~1-2% calm large cap; 5%+ very volatile.",
            "Use as a risk filter (< 4) or to size positions; NULL for mutual funds (no intraday range).",
        ],
        "vs_peers": "No — it's already price-normalized.",
    },
    # ------------------------------------------------------------------ #
    # Intrinsic Value
    # ------------------------------------------------------------------ #
    "intrinsic_value_graham": {
        "name": "Graham",
        "category": "Intrinsic Value",
        "unit": "$",
        "what_it_is": "Graham number: √(22.5 × EPS × book value per share) — fair price if P/E×P/B should not exceed 22.5.",
        "how_to_use": [
            "Price below it = cheap on combined earnings + book strength.",
            "Conservative by design; NULL when EPS or book value is negative.",
        ],
        "vs_peers": "No — compare price to value, not to other symbols.",
    },
    "intrinsic_value_lynch": {
        "name": "Lynch",
        "category": "Intrinsic Value",
        "unit": "$",
        "what_it_is": "Peter Lynch fair value: EPS × growth rate (3y EPS CAGR %, capped at 25) used as the fair P/E.",
        "how_to_use": [
            "Embodies PEG = 1: a grower deserves its growth rate as a P/E.",
            "Only meaningful for steady growers; NULL without positive EPS and growth.",
        ],
        "vs_peers": "No — compare price to value.",
    },
    "intrinsic_value_dcf": {
        "name": "DCF",
        "category": "Intrinsic Value",
        "unit": "$",
        "what_it_is": "Per-share DCF: trailing FCF projected 10 years at its historical CAGR (capped 15%), discounted at risk-free + beta × 5%, plus terminal value, minus net debt.",
        "how_to_use": [
            "The most complete model — and the most assumption-sensitive.",
            "Treat as a rough anchor; demand a margin of safety, not precision.",
            "NULL when FCF is negative.",
        ],
        "vs_peers": "No — compare price to value.",
    },
    "margin_of_safety": {
        "name": "Safety mgn",
        "category": "Intrinsic Value",
        "unit": "%",
        "what_it_is": "How far price sits below the average of the available intrinsic values: (fair − price) / fair × 100.",
        "how_to_use": [
            "Positive = trading below fair value; 30+ is the classic value-investing bar.",
            "Negative = priced above the models' fair value.",
            "Check which models fed it — fewer models = noisier average.",
        ],
        "vs_peers": "No — it's already an absolute discount measure.",
    },
    # ------------------------------------------------------------------ #
    # Relative Strength
    # ------------------------------------------------------------------ #
    "rs_rank": {
        "name": "RS Rank",
        "category": "Relative Strength",
        "unit": "",
        "what_it_is": "IBD-style 0-99 rank of weighted trailing return vs the whole universe.",
        "how_to_use": [
            "Higher means stronger price performance than most other symbols.",
            "80+ is market-leading; NULL when under ~1 year of price history.",
        ],
        "vs_peers": "No — it is already a rank against the entire universe.",
    },
    # ------------------------------------------------------------------ #
    # Statement items (raw financial-statement line items — charted over time on the
    # Fundamentals view; not currently filter metrics. Add more here as needed.)
    # ------------------------------------------------------------------ #
    "total_revenue": {
        "name": "Revenue",
        "category": "Statement item",
        "unit": "$",
        "what_it_is": "Total sales (top line) for the period, before any costs.",
        "how_to_use": [
            "The base everything else is measured against — margins are a share of it.",
            "Watch the trend: steady growth matters more than any single period.",
        ],
        "vs_peers": "No — an absolute dollar amount; compare growth rates and margins instead.",
    },
    "gross_profit": {
        "name": "Gross profit",
        "category": "Statement item",
        "unit": "$",
        "what_it_is": "Revenue minus the direct cost of goods sold.",
        "how_to_use": [
            "What's left to cover operating costs; ÷ revenue = gross margin.",
            "Rising gross profit faster than revenue means widening margins.",
        ],
        "vs_peers": "No — compare the gross margin (a percent) across peers instead.",
    },
    "operating_income": {
        "name": "Operating income",
        "category": "Statement item",
        "unit": "$",
        "what_it_is": "Profit from core operations (EBIT) — after operating costs, before interest and tax.",
        "how_to_use": [
            "The cleanest read on the business itself, stripped of financing and tax.",
            "÷ revenue = operating margin; feeds interest coverage.",
        ],
        "vs_peers": "No — compare the operating margin across peers instead.",
    },
    "net_income": {
        "name": "Net income",
        "category": "Statement item",
        "unit": "$",
        "what_it_is": "The bottom line — profit after every cost, including interest and tax.",
        "how_to_use": [
            "Drives EPS, ROE and the P/E multiple.",
            "Can swing on one-off items — read alongside operating income and cash flow.",
        ],
        "vs_peers": "No — compare net margin / ROE across peers instead.",
    },
    "ebitda": {
        "name": "EBITDA",
        "category": "Statement item",
        "unit": "$",
        "what_it_is": "Earnings before interest, tax, depreciation and amortization — a rough cash-earnings proxy.",
        "how_to_use": [
            "Used in EV/EBITDA and the debt/EBITDA leverage check.",
            "Ignores real capital costs — don't treat it as free cash flow.",
        ],
        "vs_peers": "No — compare via EV/EBITDA or debt/EBITDA (ratios) instead.",
    },
    "free_cash_flow": {
        "name": "Free cash flow",
        "category": "Statement item",
        "unit": "$",
        "what_it_is": "Operating cash flow minus capital expenditure — cash left after keeping the business running.",
        "how_to_use": [
            "The cash that funds dividends, buybacks and debt paydown.",
            "Should roughly track net income over time; persistent gaps are a flag.",
        ],
        "vs_peers": "No — compare FCF margin or P/FCF across peers instead.",
    },
    "diluted_eps": {
        "name": "Diluted EPS",
        "category": "Statement item",
        "unit": "$",
        "what_it_is": "Net income per share, counting all dilutive shares. Charted split-adjusted to current shares.",
        "how_to_use": [
            "The per-share earnings behind P/E; growth here is what compounds for holders.",
            "Negative EPS disables P/E, PEG and the Graham/Lynch values.",
        ],
        "vs_peers": "No — a per-share figure; compare EPS growth and P/E across peers.",
    },
    "stockholders_equity": {
        "name": "Stockholders' equity",
        "category": "Statement item",
        "unit": "$",
        "what_it_is": "Book value — total assets minus total liabilities, the owners' stake.",
        "how_to_use": [
            "The denominator of ROE and the basis of P/B.",
            "Steady growth (earnings retained into book) is the financial-sector tell.",
        ],
        "vs_peers": "No — compare via P/B or ROE across peers instead.",
    },
    "total_assets": {
        "name": "Total assets",
        "category": "Statement item",
        "unit": "$",
        "what_it_is": "Everything the company owns — the size of the balance sheet.",
        "how_to_use": [
            "The denominator of ROA; scale context for debt and equity.",
            "Fast asset growth without matching profit growth can signal empire-building.",
        ],
        "vs_peers": "No — compare via ROA (a ratio) across peers instead.",
    },
    "total_debt": {
        "name": "Total debt",
        "category": "Statement item",
        "unit": "$",
        "what_it_is": "All interest-bearing borrowings (short- plus long-term).",
        "how_to_use": [
            "Feeds debt/equity and debt/EBITDA — judge it against those, not alone.",
            "Rising debt is fine if earnings/cash flow cover it comfortably.",
        ],
        "vs_peers": "No — compare leverage ratios (debt/equity, debt/EBITDA) across peers.",
    },
    # ------------------------------------------------------------------ #
    # Scores
    # ------------------------------------------------------------------ #
    "overall_score": {
        "name": "Overall",
        "category": "Score",
        "unit": "",
        "what_it_is": "0-100 blend of the five category scores (Quality/Growth/Momentum/Value/Income).",
        "how_to_use": [
            "Higher is a stronger all-round profile on current weights.",
            "Sort to triage, then read the category scores to see why.",
        ],
        "vs_peers": "No — it is already a cross-universe percentile blend.",
    },
    "value_score": {
        "name": "Value",
        "category": "Score",
        "unit": "",
        "what_it_is": "0-100 percentile of valuation metrics, ranked within the industry (then sector).",
        "how_to_use": ["Higher means cheaper than industry/sector peers on the weighted multiples."],
        "vs_peers": "No — it is already peer-relative (industry, then sector).",
    },
    "quality_score": {
        "name": "Quality",
        "category": "Score",
        "unit": "",
        "what_it_is": "0-100 percentile of profitability and balance-sheet strength, within the industry (then sector).",
        "how_to_use": ["Higher means more profitable / financially sound than industry/sector peers."],
        "vs_peers": "No — it is already peer-relative (industry, then sector).",
    },
    "growth_score": {
        "name": "Growth",
        "category": "Score",
        "unit": "",
        "what_it_is": "0-100 percentile of revenue/EPS/FCF growth across the universe.",
        "how_to_use": ["Higher means faster, steadier growth than most of the universe."],
        "vs_peers": "No — it is already a cross-universe percentile.",
    },
    "momentum_score": {
        "name": "Momentum",
        "category": "Score",
        "unit": "",
        "what_it_is": "0-100 percentile of price trend (RS rank, MA distance, 52-week) across the universe.",
        "how_to_use": ["Higher means stronger recent price action than most of the universe."],
        "vs_peers": "No — it is already a cross-universe percentile.",
    },
    "income_score": {
        "name": "Income",
        "category": "Score",
        "unit": "",
        "what_it_is": "0-100 percentile of dividend yield, growth and safety across the universe.",
        "how_to_use": ["Higher means a stronger, better-covered income profile; NaN for non-payers."],
        "vs_peers": "No — it is already a cross-universe percentile.",
    },
    # ------------------------------------------------------------------ #
    # Classification — text labels the company/fund carries, not metrics.
    # Filtered by picking values from a list (is any of / is none of).
    # ------------------------------------------------------------------ #
    "sector": {
        "name": "Sector",
        "category": "Classification",
        "unit": "",
        "what_it_is": "The broad economic sector (e.g. Technology, Healthcare) from the data provider.",
        "how_to_use": ["Pick one or more sectors to include, or exclude sectors you don't want."],
        "vs_peers": "No — it is the grouping label itself, not a value to compare.",
    },
    "industry": {
        "name": "Industry",
        "category": "Classification",
        "unit": "",
        "what_it_is": "The narrower industry within a sector (e.g. Semiconductors, Banks — Regional).",
        "how_to_use": ["Pick specific industries to focus a screen, or exclude ones you avoid."],
        "vs_peers": "No — it is the grouping label itself, not a value to compare.",
    },
    "fund_family": {
        "name": "Fund family",
        "category": "Classification",
        "unit": "",
        "what_it_is": "The fund's provider / sponsor (e.g. Vanguard, iShares). Funds only — blank for stocks.",
        "how_to_use": ["Pick one or more providers to include, or exclude families you don't want."],
        "vs_peers": "No — it is the grouping label itself, not a value to compare.",
    },
}


def get_hint(param_key: str) -> dict | None:
    """Return the hint dict for a parameter, or None if undefined yet."""
    return PARAM_HINTS.get(param_key)


# --------------------------------------------------------------------------- #
# Rendering — the ONE place the hint's on-screen style is defined.
#
# Both renderers take the same args so call sites are interchangeable:
#   - param_key : the registry key.
#   - fallback  : {"name", "category", "unit"} used when the key has no hint
#                 yet (e.g. raw statement items browsed in the fundamentals
#                 picker). Yields a single "Name — Category (unit: x)" label.
#   - header    : include the bold "Name · Category · unit: x" title line.
#                 Turn OFF where the surrounding UI already shows the name
#                 (column-header tooltips, the Reference page's own heading).
#   - sections  : which body parts to include, in display order.
# --------------------------------------------------------------------------- #
_SECTIONS = ("what_it_is", "how_to_use", "vs_peers")


def _resolve(param_key: str, fallback: dict | None) -> tuple[dict, bool]:
    """(hint, is_registered). When unknown, synthesize a minimal hint from
    `fallback` so un-registered keys still render with a consistent label."""
    h = PARAM_HINTS.get(param_key)
    if h:
        return h, True
    fb = fallback or {}
    return {"name": fb.get("name", param_key),
            "category": fb.get("category", ""),
            "unit": fb.get("unit", "")}, False


def hint_markdown(
    param_key: str,
    *,
    fallback: dict | None = None,
    header: bool = True,
    sections: tuple[str, ...] = _SECTIONS,
) -> str:
    """The param's hint as dyslexia-friendly Markdown (blank-line spacing, a
    bulleted "how to use"). For Streamlit `help=` tooltips, the Parameter
    Reference page, or any `st.markdown`. Empty string when nothing to show."""
    h, registered = _resolve(param_key, fallback)
    parts: list[str] = []
    if header:
        head = f"**{h.get('name', param_key)}**"
        if h.get("category"):
            head += f" · {h['category']}"
        if h.get("unit"):
            head += f" · unit: {h['unit']}"
        parts.append(head)
    if not registered:
        if not header:
            label = h.get("name", param_key)
            if h.get("category"):
                label += f" — {h['category']}"
            parts.append(label)
        return "\n\n".join(parts)
    if "what_it_is" in sections and h.get("what_it_is"):
        parts.append(h["what_it_is"])
    if "how_to_use" in sections and h.get("how_to_use"):
        how = h["how_to_use"]
        items = how if isinstance(how, list) else [str(how)]
        parts.append("\n".join(f"- {x}" for x in items))
    if "vs_peers" in sections and h.get("vs_peers"):
        parts.append(f"**Peers:** {h['vs_peers']}")
    return "\n\n".join(parts)


def hint_html(
    param_key: str,
    *,
    fallback: dict | None = None,
    sections: tuple[str, ...] = _SECTIONS,
) -> str:
    """The param's hint as escaped HTML for the picker's inline info panel
    (`.fam-hi`/`.fam-h-s` in app.py). Same shape as `hint_markdown`, expressed
    in the markup the popover needs (Streamlit tooltips misbehave there)."""
    h, registered = _resolve(param_key, fallback)
    e = _html.escape
    if not registered:
        unit = f" (unit: {h['unit']})" if h.get("unit") else ""
        return e(f"{h.get('name', param_key)} — {h.get('category', '')}{unit}")
    parts = [f"<b>{e(h['name'])}</b> · {e(h['category'])}"
             + (f" · unit: {e(h['unit'])}" if h.get("unit") else "")]
    if "what_it_is" in sections and h.get("what_it_is"):
        parts.append(f"<div class='fam-h-s'>{e(h['what_it_is'])}</div>")
    if "how_to_use" in sections and h.get("how_to_use"):
        how = h["how_to_use"]
        items = how if isinstance(how, list) else [str(how)]
        parts.append("<ul>" + "".join(f"<li>{e(x)}</li>" for x in items) + "</ul>")
    if "vs_peers" in sections and h.get("vs_peers"):
        parts.append(f"<div class='fam-h-s'><i>Peers:</i> {e(h['vs_peers'])}</div>")
    return "".join(parts)
