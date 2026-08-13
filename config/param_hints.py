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
    # Size
    # ------------------------------------------------------------------ #
    "market_cap": {
        "name": "Market cap",
        "category": "Size",
        "unit": "$",
        "what_it_is": "Company size: last completed session's price × shares outstanding.",
        "how_to_use": [
            "Smaller companies have more room to grow (and more risk); giants compound slowly.",
            "Use as a band — e.g. small/mid-cap hunting (300M–10B) — or a floor to drop micro-caps.",
        ],
        "vs_peers": "No — it's an absolute size; compare valuation multiples instead.",
    },
    # ------------------------------------------------------------------ #
    # Valuation
    # ------------------------------------------------------------------ #
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
    "peg": {
        "name": "PEG",
        "category": "Valuation",
        "unit": "x",
        "what_it_is": "Trailing P/E divided by the EPS trend growth rate (log-linear fit, in %).",
        "how_to_use": [
            "Around 1 is fairly priced for its growth (Lynch's rule of thumb).",
            "Under 1 suggests growth at a discount; NULL when growth is zero or negative.",
        ],
        "vs_peers": "Somewhat — it already adjusts for growth, but norms still differ by sector.",
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
    # ------------------------------------------------------------------ #
    # Profitability
    # ------------------------------------------------------------------ #
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
    "ocf_to_ni_3y": {
        "name": "Cash conv (3y)",
        "category": "Profitability",
        "unit": "%",
        "what_it_is": "Average operating cash flow ÷ net income × 100 across the last 3 fiscal years "
                      "(loss years excluded — see how_to_use). The multi-year answer to fcf_margin's "
                      "'earnings ahead of cash?' question.",
        "how_to_use": [
            "Near/above 100% = earnings are cash-backed, the healthy case.",
            "Well below 100% for multiple years = net income is running ahead of cash — aggressive "
            "revenue recognition or working-capital games, look closer.",
            "Blank if fewer than 2 profitable years are on file — a 1-year 'average' isn't one.",
        ],
        "vs_peers": "No — 100% (cash matches earnings) is the universal reference line.",
    },
    "ocf_to_ni_5y": {
        "name": "Cash conv (5y)",
        "category": "Profitability",
        "unit": "%",
        "what_it_is": "Same as Cash conv (3y) but averaged over the last 5 fiscal years — smooths out "
                      "a single unusual year further at the cost of reacting slower to a real change.",
        "how_to_use": [
            "Near/above 100% = earnings are cash-backed, the healthy case.",
            "A 3y/5y split that diverges a lot is itself informative — check which years drove it.",
            "Blank if fewer than 2 profitable years are on file.",
        ],
        "vs_peers": "No — 100% (cash matches earnings) is the universal reference line.",
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
    "gross_margin_trend_3y": {
        "name": "Gross mgn trend 3y",
        "category": "Profitability",
        "unit": "%",
        "what_it_is": "Change in gross margin over ~3 years (latest annual margin minus the margin ~3 years earlier), in percentage points.",
        "how_to_use": [
            "Positive = widening margins = pricing power; often precedes a re-rating.",
            "Negative = margin erosion — an early warning even while sales still grow.",
        ],
        "vs_peers": "No — read the direction on its own; compare the level via gross margin.",
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
    "operating_margin_trend_3y": {
        "name": "Op mgn trend 3y",
        "category": "Profitability",
        "unit": "%",
        "what_it_is": "Change in operating margin over ~3 years (latest annual margin minus the margin ~3 years earlier), in percentage points.",
        "how_to_use": [
            "Positive = the core business is getting more efficient — the cleanest quality-trend signal.",
            "Negative = costs outpacing sales; profit quality slipping.",
        ],
        "vs_peers": "No — read the direction on its own; compare the level via operating margin.",
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
    "roe_roa_gap": {
        "name": "ROE-ROA gap",
        "category": "Profitability",
        "unit": "%",
        "what_it_is": "ROE minus ROA, in percentage points — how much of ROE comes from debt financing rather than the business itself.",
        "how_to_use": [
            "Positive and small = ROE is mostly real (margin/efficiency), not borrowed.",
            "Large positive = ROE is mostly leverage — check debt/equity and interest coverage before trusting the ROE number.",
            "Negative = the company is losing money and leverage is making the loss worse (or equity is negative) — treat as a red flag on its own.",
        ],
        "vs_peers": "No — read the gap size/sign directly; it's already leverage-normalized.",
    },
    "asset_turnover": {
        "name": "Asset turnover",
        "category": "Profitability",
        "unit": "x",
        "what_it_is": "Revenue / total assets — how many dollars of sales each dollar of assets generates.",
        "how_to_use": [
            "Higher = assets are being used efficiently to generate sales (common in retail/services).",
            "Naturally low for asset-heavy businesses (utilities, REITs) — compare within the industry.",
        ],
        "vs_peers": "Yes — asset intensity varies a lot by industry.",
    },
    "equity_multiplier": {
        "name": "Equity multiplier",
        "category": "Profitability",
        "unit": "x",
        "what_it_is": "Total assets / equity — how much of the balance sheet is funded by debt vs shareholders' own money (the leverage piece of ROE).",
        "how_to_use": [
            "1.0 = no debt at all; the higher above 1, the more assets are debt-funded.",
            "A high value inflates ROE without improving the underlying business — pair with debt/equity and interest coverage.",
        ],
        "vs_peers": "Yes — normal leverage differs a lot by industry (banks run high by design).",
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
    "wacc": {
        "name": "WACC",
        "category": "Profitability",
        "unit": "%",
        "what_it_is": "Weighted average cost of capital: market-cap-weighted CAPM cost of equity "
                      "(risk-free rate + beta × equity risk premium) blended with the tax-adjusted "
                      "cost of debt (interest expense / total debt).",
        "how_to_use": [
            "The real, company-specific version of the 'rough 10%' rule of thumb under ROIC.",
            "Pair with ROIC, don't read alone — it's a hurdle rate, not a quality signal by itself.",
            "Also the discount rate the DCF and DDM use when it's available, so a blank here means those models fell back to a plain CAPM cost of equity.",
            "Blank when beta or debt/interest data is missing — never guessed/defaulted.",
        ],
        "vs_peers": "No — it's an input to roic_vs_wacc and the DCF/DDM discount rate, not a value judgement on its own.",
    },
    "roic_vs_wacc": {
        "name": "ROIC - WACC",
        "category": "Profitability",
        "unit": "%",
        "what_it_is": "ROIC minus WACC, in percentage points — the actual, company-specific "
                      "value-creation spread (replaces ROIC's rough '~10% cost of capital' rule "
                      "of thumb with a real computed hurdle rate).",
        "how_to_use": [
            "Positive = the business earns more than its capital costs (value creation).",
            "Negative = it's destroying value even if ROIC alone looks decent.",
            "Blank whenever WACC or ROIC is blank (missing beta, debt, or invested-capital data) — never a defaulted/guessed number.",
        ],
        "vs_peers": "No — 0 is the universal pass/fail line, not peer-relative.",
    },
    "roic_vs_wacc_5y": {
        "name": "ROIC - WACC (5y median)",
        "category": "Profitability",
        "unit": "%",
        "what_it_is": "Median annual ROIC over the last up to 5 fiscal years, minus today's WACC — "
                      "has the value-creation spread held up over time, not just this year. Still "
                      "graded against today's WACC (no stored history of beta/market cap to build a "
                      "true year-by-year hurdle rate), so it approximates rather than exactly reconstructs "
                      "each year's actual cost of capital.",
        "how_to_use": [
            "A moat-persistence check: positive here across a multi-year median is a stronger signal than a single positive roic_vs_wacc reading.",
            "Blank below 2 valid fiscal years of financials, or whenever WACC is blank.",
        ],
        "vs_peers": "No — 0 is the universal pass/fail line, not peer-relative.",
    },
    "roic_trend_3y": {
        "name": "ROIC trend (3y)",
        "category": "Profitability",
        "unit": "%",
        "what_it_is": "Change in annual ROIC (percentage points) from ~3 fiscal years ago to the "
                      "latest — is the moat widening or narrowing. Same level-change convention as "
                      "gross_margin_trend_3y/operating_margin_trend_3y.",
        "how_to_use": [
            "Positive = ROIC rising — moat widening (pricing power or capital efficiency improving).",
            "Negative = ROIC falling — moat narrowing, worth checking why even if the current level still looks fine.",
            "Blank if no annual ROIC point exists ~3 years back.",
        ],
        "vs_peers": "No — it's a self-referential trend, not a peer comparison.",
    },
    # ------------------------------------------------------------------ #
    # Balance Sheet
    # ------------------------------------------------------------------ #
    "altman_z": {
        "name": "Altman Z",
        "category": "Balance Sheet",
        "unit": "",
        "what_it_is": "Bankruptcy-risk score blending working capital, retained earnings, EBIT, market cap and sales vs assets/liabilities.",
        "how_to_use": [
            "Above 3 = safe zone, 1.8-3 = grey zone, below 1.8 = distress zone.",
            "Designed for manufacturers — take it lightly for other business models.",
            "Blank for a large, currently-profitable company whose retained earnings has gone deeply negative from decades of share buybacks (e.g. VRSN) rather than accumulated losses — the score would otherwise read as distressed for the wrong reason.",
        ],
        "vs_peers": "No — the zone thresholds are absolute.",
    },
    "beneish_m_score": {
        "name": "Beneish M",
        "category": "Balance Sheet",
        "unit": "",
        "what_it_is": "Beneish M-Score: a weighted blend of 8 year-over-year ratios (receivables "
                      "vs sales, margin change, asset quality, sales growth, depreciation rate, "
                      "SG&A vs sales, leverage change, and accruals) estimating how likely a "
                      "company is manipulating its earnings.",
        "how_to_use": [
            "Lower (more negative) is cleaner; most healthy companies sit around -2 to -3.",
            "Above -1.78 flags a likely manipulator — Beneish's own back-tested cutoff.",
            "Treat a flag as 'look closer', not an automatic disqualify — fast, legitimately "
            "growing companies can drift upward too, without any actual manipulation.",
        ],
        "vs_peers": "No — an absolute, universal threshold; unlike most ratios here it does "
                    "NOT need an industry comparison.",
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
    "quick_health_score": {
        "name": "Quick health",
        "category": "Balance Sheet",
        "unit": "",
        "what_it_is": "A fast, count-based pass/fail gate (0-7), not a weighted/anchored score "
                      "like Beneish or Altman. Counts how many of 7 basic year-over-year checks "
                      "currently hold: revenue growing, cost of revenue NOT outgrowing revenue, "
                      "gross profit growing, assets > liabilities, liabilities NOT outgrowing "
                      "assets, a comfortable cash cushion (cash ratio >= 0.2), and operating "
                      "cash flow trending up.",
        "how_to_use": [
            "Meant as a first-pass filter BEFORE deeper ratio/valuation work, not a final "
            "verdict on the company.",
            "Count-based, not all-or-nothing — filter e.g. >= 5 or >= 6 rather than requiring "
            "a perfect 7; a healthy company can legitimately miss one check for a normal "
            "reason (e.g. a thin cash cushion right after a big capex year).",
            "A missing input counts as a FAIL for that one check (conservative), so treat a "
            "low score as 'worth checking why', not an automatic disqualify — EXCEPT cost of "
            "revenue/gross profit: a company reporting neither (a bank, a BDC) gets NaN for "
            "the whole score instead, since it isn't failing those 2 checks, the checklist's "
            "traditional-operating-company shape just doesn't apply to it.",
            "ONLY computed for Common Stock — Standard / ADRs, NaN for every other type "
            "(banks, insurers, REITs, funds) — same as Beneish M and Altman Z. This is a "
            "deliberate CONCEPT gate, not just a data-availability one: a REIT's cost-of-"
            "revenue line (property operating expense) doesn't mean the same thing as a "
            "producing company's COGS even when it happens to be reported, so it's excluded "
            "regardless of what data is on file.",
        ],
        "vs_peers": "No — every check is self-relative (this year vs last year), never "
                    "compared to industry peers.",
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
    # ------------------------------------------------------------------ #
    # Growth (one hint per base; covers all windows: CAGR 1/3/5y, YoY
    # quarter, growth volatility %, trend R² (0-1), variability CV %)
    # ------------------------------------------------------------------ #
    "book_value": {
        "name": "BV growth",
        "category": "Growth",
        "unit": "%",
        "what_it_is": "Growth of shareholders' equity (book value). Same windows as revenue growth.",
        "how_to_use": [
            "THE growth metric for banks and insurers (their earnings compound into book).",
            "Steady ~10%+ book growth plus reasonable P/B is the classic financial-sector screen.",
            "Volatility % and CV % low + R² near 1 = steady compounding; high vol = one-off write-downs/raises distorting book value.",
            "Trend growth %/yr — a steadier alternative to CAGR, fit across the full ~5-year window instead of just the two endpoints.",
        ],
        "vs_peers": "Yes — compare within banks/insurance specifically.",
    },
    "eps": {
        "name": "EPS growth",
        "category": "Growth",
        "unit": "%",
        "what_it_is": "Growth of split-adjusted diluted EPS. Same windows as revenue growth (CAGR 1/3/5y, YoY quarter, trend stats).",
        "how_to_use": [
            "EPS growing faster than revenue = margin expansion or buybacks.",
            "Erratic EPS (high vol, low R²) makes P/E and PEG less trustworthy.",
            "Volatility % and CV % low + R² near 1 = steady earner; high vol = one-off gains/charges distorting the trend.",
            "Trend growth %/yr — a steadier alternative to CAGR, fit across the full ~5-year window instead of just the two endpoints.",
        ],
        "vs_peers": "Yes — compare pace and steadiness within the industry.",
    },
    "eps_accel": {
        "name": "EPS accel",
        "category": "Growth",
        "unit": "%",
        "what_it_is": "EPS acceleration: latest quarter's YoY EPS growth minus the 3-year EPS CAGR, in percentage points.",
        "how_to_use": [
            "Positive = earnings growth accelerating — often what drives a fresh price run.",
            "Negative = earnings momentum fading even if still growing.",
        ],
        "vs_peers": "No — it's self-relative (a stock vs its own trend).",
    },
    "fcf": {
        "name": "FCF growth",
        "category": "Growth",
        "unit": "%",
        "what_it_is": "Growth of free cash flow (operating cash flow + capex), derived from deep annual history. Same windows as revenue growth.",
        "how_to_use": [
            "The cash check on EPS growth — both should roughly agree over time.",
            "Feeds the DCF intrinsic value — trend growth sets where the projection starts (bounded at 35%) before fading to the terminal rate.",
            "Volatility % and CV % low + R² near 1 = steady cash generation; high vol = lumpy or capex-heavy years.",
            "Trend growth %/yr — a steadier alternative to CAGR, fit across the full ~5-year window instead of just the two endpoints.",
        ],
        "vs_peers": "Yes — but steadiness (R², CV) matters more than raw pace.",
    },
    "revenue": {
        "name": "Rev growth",
        "category": "Growth",
        "unit": "%",
        "what_it_is": "Growth of annual total revenue. Windows: 1/3/5-year CAGR, latest quarter vs the same quarter last year (YoY), plus trend quality over ~5 years.",
        "how_to_use": [
            "CAGR = smoothed yearly pace; YoY quarter = what's happening right now.",
            "Volatility % and CV % low + R² near 1 = steady grower; high vol = lumpy or cyclical.",
            "R² is 0-1 (fit quality), not a percent.",
            "Trend growth %/yr — a steadier alternative to CAGR, fit across the full ~5-year window instead of just the two endpoints.",
        ],
        "vs_peers": "Yes — growth is only impressive against the industry's pace.",
    },
    "revenue_accel": {
        "name": "Rev accel",
        "category": "Growth",
        "unit": "%",
        "what_it_is": "Revenue acceleration: latest quarter's YoY growth minus the 3-year revenue CAGR, in percentage points.",
        "how_to_use": [
            "Positive = growth speeding up vs its own recent pace — a strong forward signal.",
            "Negative = decelerating; read alongside the raw growth rates.",
        ],
        "vs_peers": "No — it's self-relative (a stock vs its own trend).",
    },
    "share_count_chg_1y": {
        "name": "Share count chg 1y",
        "category": "Growth",
        "unit": "%",
        "what_it_is": "Year-over-year change in diluted share count (the EPS denominator).",
        "how_to_use": [
            "Negative = net buybacks — fewer shares quietly lift EPS and per-share value.",
            "Positive = dilution (new shares, stock comp, raises) — a per-share headwind.",
            "Filter < 0 to find companies shrinking their share count.",
        ],
        "vs_peers": "No — read it as an absolute capital-return signal.",
    },
    # ------------------------------------------------------------------ #
    # Estimates (forward-looking analyst data, from the `estimates` table in signals.db)
    # ------------------------------------------------------------------ #
    "analyst_count": {
        "name": "Analysts",
        "category": "Estimates",
        "unit": "",
        "what_it_is": "Number of analysts behind the current-year EPS estimate.",
        "how_to_use": [
            "More analysts = a more reliable consensus; very low counts make the estimates noisy.",
            "Use a floor (e.g. >= 3) to keep estimate-based screens trustworthy.",
        ],
        "vs_peers": "No — it's coverage depth, not a valuation; read it as a confidence gate.",
    },
    "eps_revision_1m": {
        "name": "EPS rev 1m",
        "category": "Estimates",
        "unit": "%",
        "what_it_is": "Percent change in the current-year consensus EPS estimate over the last 30 days.",
        "how_to_use": [
            "Positive = analysts raising estimates — one of the strongest forward-return signals.",
            "Negative = downgrades in progress; a warning even on a cheap-looking stock.",
        ],
        "vs_peers": "No — it's self-relative (the estimate vs its own level a month ago).",
    },
    "eps_revision_3m": {
        "name": "EPS rev 3m",
        "category": "Estimates",
        "unit": "%",
        "what_it_is": "Percent change in the current-year consensus EPS estimate over the last 90 days.",
        "how_to_use": [
            "The slower-moving revision trend — confirms the 1-month signal isn't just noise.",
            "Both 1m and 3m positive = sustained upgrade momentum.",
        ],
        "vs_peers": "No — it's self-relative (the estimate vs its own level three months ago).",
    },
    "eps_revision_breadth": {
        "name": "EPS rev breadth",
        "category": "Estimates",
        "unit": "",
        "what_it_is": "Net analysts revising the current-year EPS estimate UP minus DOWN over the last 30 days (a count).",
        "how_to_use": [
            "Positive = more raised than cut (broad agreement on improvement); filter > 0.",
            "Reads the breadth of the revision, not just the size — pair with EPS rev 1m.",
        ],
        "vs_peers": "No — it's an absolute count of analyst actions.",
    },
    "forward_eps_growth": {
        "name": "Fwd EPS gr",
        "category": "Estimates",
        "unit": "%",
        "what_it_is": "Analysts' consensus EPS growth for the NEXT fiscal year vs the current one.",
        "how_to_use": [
            "The forward complement to historical EPS growth — what the Street expects next.",
            "High + accelerating vs trailing growth is the GARP sweet spot; it's a forecast, not fact.",
        ],
        "vs_peers": "Yes — expected growth norms differ by sector; compare within the group.",
    },
    "forward_peg": {
        "name": "Fwd PEG",
        "category": "Estimates",
        "unit": "x",
        "what_it_is": "Forward P/E divided by next-year EPS growth (%). (yfinance gives no usable "
                      "per-stock long-term rate, so the 1-year-forward growth is used.)",
        "how_to_use": [
            "Around 1 is fairly priced for its expected growth; under 1 is growth at a discount.",
            "NULL when forward growth is zero or negative. More forward-looking than trailing PEG.",
        ],
        "vs_peers": "Somewhat — it already adjusts for growth, but sector norms still differ.",
    },
    "forward_rev_growth": {
        "name": "Fwd rev gr",
        "category": "Estimates",
        "unit": "%",
        "what_it_is": "Analysts' consensus revenue growth for the next fiscal year vs the current one.",
        "how_to_use": [
            "Top-line forward growth — cleaner than EPS for early-stage / thin-margin names.",
            "Pair with forward EPS growth: revenue growing faster than EPS hints at margin pressure.",
        ],
        "vs_peers": "Yes — compare within sector/industry.",
    },
    # ------------------------------------------------------------------ #
    # Earnings (surprise history + next earnings date, from signals.db)
    # ------------------------------------------------------------------ #
    "days_to_next_earnings": {
        "name": "Days to earnings",
        "category": "Earnings",
        "unit": "days",
        "what_it_is": "Calendar days until the next expected earnings report (from the analyst calendar).",
        "how_to_use": [
            "A RISK GATE, not a quality score — a report can gap a stock either way.",
            "Avoid initiating right before earnings (small number), or target a post-report window.",
        ],
        "vs_peers": "No — it's an event date, not a comparative metric.",
    },
    "earnings_beat_rate": {
        "name": "Beat rate",
        "category": "Earnings",
        "unit": "%",
        "what_it_is": "Share of the last 4 reported quarters where actual EPS beat the estimate.",
        "how_to_use": [
            "Higher = more consistent execution above expectations (100 = beat every quarter).",
            "Consistency matters more than a single lucky beat — use with the surprise average.",
        ],
        "vs_peers": "No — ranked across the whole universe; consistency is good everywhere.",
    },
    "earnings_surprise_avg": {
        "name": "Surprise avg",
        "category": "Earnings",
        "unit": "%",
        "what_it_is": "Average earnings surprise (actual vs estimate EPS) over the last 4 reported quarters.",
        "how_to_use": [
            "Positive = a habit of beating estimates; post-earnings drift tends to follow beats.",
            "A consistent beater is executing above expectations — pair with the beat rate for consistency.",
        ],
        "vs_peers": "No — beating estimates is good in any sector; judged on an absolute basis (line at 0).",
    },
    "earnings_surprise_last": {
        "name": "Surprise last",
        "category": "Earnings",
        "unit": "%",
        "what_it_is": "Earnings surprise (actual vs estimate EPS) for the most recent reported quarter.",
        "how_to_use": [
            "The freshest beat/miss — a big positive can kick off post-earnings drift.",
            "Read next to the 4-quarter average: one beat after misses is weaker than a steady streak.",
        ],
        "vs_peers": "No — absolute beat/miss (line at 0), not a sector comparison.",
    },
    # ------------------------------------------------------------------ #
    # Ownership (insider + institutional, from signals.db)
    # ------------------------------------------------------------------ #
    "insider_net_buy_pct": {
        "name": "Insider net buy",
        "category": "Ownership",
        "unit": "%",
        "what_it_is": "Net insider share buying over the last ~6 months as a percent of insider-held shares "
                      "(buys minus sells).",
        "how_to_use": [
            "Positive = insiders net BUYING — the 'smart money confirms' signal; filter > 0.",
            "Insiders buy for one reason (they expect upside) but sell for many — buying is the stronger tell.",
        ],
        "vs_peers": "No — net insider buying is bullish in any sector; absolute (line at 0).",
    },
    "institutions_count": {
        "name": "Institutions",
        "category": "Ownership",
        "unit": "",
        "what_it_is": "Number of institutional holders on record for the stock.",
        "how_to_use": [
            "More institutions = deeper professional ownership and liquidity; very low = under-the-radar.",
            "A context/liquidity read, not a buy signal on its own — pair with insider buying.",
        ],
        "vs_peers": "No — a raw count (size/liquidity-correlated); read it as context.",
    },
    # ------------------------------------------------------------------ #
    # Income
    # ------------------------------------------------------------------ #
    "div": {
        "name": "Div growth",
        "category": "Income",
        "unit": "%",
        "what_it_is": "Growth of the annual dividend rate (each complete year's max per-share "
                      "payment). Windows: 1/3/5-year CAGR, plus trend quality over ~5 years.",
        "how_to_use": [
            "CAGR: 5-10%+ with a safe payout ratio is the dividend-growth sweet spot.",
            "Volatility % and CV % low + R² near 1 = steady raiser; high vol = lumpy raises.",
            "Negative CAGR means the payout shrank — check for cuts.",
            "Trend growth %/yr — a steadier alternative to CAGR, fit across the full ~5-year window instead of just the two endpoints.",
            "Feeds the DDM intrinsic value — trend growth sets where the projection starts (bounded at 35%) before fading to the terminal rate.",
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
    # ------------------------------------------------------------------ #
    # Technical
    # ------------------------------------------------------------------ #
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
    "history_years": {
        "name": "History (yrs)",
        "category": "Technical",
        "unit": "yr",
        "what_it_is": "Years of stored price history available for the symbol (earliest OHLCV date to the last completed session), as a fraction — 1.5 means about 18 months.",
        "how_to_use": [
            "Use as a data-quality gate: require enough history for the indicators/CAGRs you're screening on (e.g. > 5 for a 5y CAGR to be meaningful).",
            "Low values flag recent IPOs/listings or thin backfill — treat their longer-window metrics as unreliable or missing.",
        ],
        "vs_peers": "No — it's a data-availability measure, not a comparative one.",
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
    # ------------------------------------------------------------------ #
    # Intrinsic Value
    # ------------------------------------------------------------------ #
    "intrinsic_value_dcf": {
        "name": "DCF",
        "category": "Intrinsic Value",
        "unit": "$",
        "what_it_is": "Per-share DCF: trailing FCF projected 10 years at a growth rate that fades from its trend rate (log-linear fit, start bounded at 35%) down to the 2.5% terminal rate by the final year, discounted at the weighted WACC where available (else risk-free + beta × 5%), plus terminal value, minus net debt.",
        "how_to_use": [
            "The most complete model — and the most assumption-sensitive.",
            "The fade is why this reads lower than a flat-growth DCF: nothing compounds at its trailing rate for a decade.",
            "Treat as a rough anchor; demand a margin of safety, not precision.",
            "NULL when FCF is negative.",
        ],
        "vs_peers": "No — compare price to value.",
    },
    "intrinsic_value_ddm": {
        "name": "DDM",
        "category": "Intrinsic Value",
        "unit": "$",
        "what_it_is": "Dividend discount model: trailing per-share dividend projected 10 years on the same fading growth path as the DCF (trend rate, start bounded at 35%, declining to the 2.5% terminal rate), discounted at the same rate as the DCF, plus terminal value.",
        "how_to_use": [
            "The model banks, insurers and REITs actually get valued on — deposits/underwriting and GAAP depreciation make FCF-DCF and EPS-based Graham/Lynch unreliable for them.",
            "Only meaningful for real dividend payers; NULL for non-payers. In the base case a shrinking dividend is floored to 0% growth; only the bear scenario may start negative.",
        ],
        "vs_peers": "No — compare price to value.",
    },
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
        "what_it_is": "Peter Lynch fair value: EPS × growth rate (EPS trend growth %, log-linear fit, capped at 25) used as the fair P/E.",
        "how_to_use": [
            "Embodies PEG = 1: a grower deserves its growth rate as a P/E.",
            "Only meaningful for steady growers; NULL without positive EPS and growth.",
        ],
        "vs_peers": "No — compare price to value.",
    },
    "fair_value": {
        "name": "Fair value",
        "category": "Intrinsic Value",
        "unit": "$",
        "what_it_is": "Median of whichever intrinsic-value models fit the company's type: Graham/Lynch/DCF/DDM for a standard company, Graham/Lynch/DDM for banks/insurers/regulated utilities (DCF's FCF projection is unreliable for them), DDM only for REITs (GAAP depreciation distorts EPS/FCF too). Median, not mean, so one blown-up DCF or one near-zero Lynch doesn't dominate.",
        "how_to_use": [
            "The headline blended fair-value estimate — compare directly to price.",
            "A point estimate: read it next to fair_value_bear / fair_value_bull, which show how much it moves on pessimistic vs optimistic growth.",
            "Graham is included here but deliberately left out of the bear/bull pair (it has no forward growth input to flex), so this can legitimately sit outside that range.",
            "NULL when no type-appropriate model computed (e.g. funds/ETFs, or a name missing the inputs its applicable models need).",
        ],
        "vs_peers": "No — compare price to value.",
    },
    "margin_of_safety": {
        "name": "Safety mgn",
        "category": "Intrinsic Value",
        "unit": "%",
        "what_it_is": "How far price sits below fair_value (the type-gated median blend): (fair_value − price) / fair_value × 100.",
        "how_to_use": [
            "Positive = trading below fair value; 30+ is the classic value-investing bar.",
            "Negative = priced above fair_value.",
            "NULL wherever fair_value is NULL.",
        ],
        "vs_peers": "No — it's already an absolute discount measure.",
    },
    "fair_value_bear": {
        "name": "Fair value (bear)",
        "category": "Intrinsic Value",
        "unit": "$",
        "what_it_is": "fair_value recomputed with each applicable model's starting growth pulled down to trend growth minus its own residual volatility (Graham excluded — it has no forward growth input to flex, so it never varies by scenario).",
        "how_to_use": [
            "The pessimistic end of the range — compare against fair_value_bull to see how sensitive the valuation actually is.",
            "A narrow bear-to-bull spread means a steady growth history; a wide one means a noisy one.",
            "May start from a negative growth rate (floored at -10%), which then fades up toward the terminal rate — a genuine contraction case. Lynch drops out of the blend when that happens, since fair P/E ≈ growth says nothing about a shrinking company.",
            "NULL under the same conditions as fair_value, AND whenever no applicable model has enough growth history to build a scenario — a blank here means 'no range available', not 'no downside'.",
        ],
        "vs_peers": "No — compare price to value.",
    },
    "fair_value_bull": {
        "name": "Fair value (bull)",
        "category": "Intrinsic Value",
        "unit": "$",
        "what_it_is": "fair_value recomputed with each applicable model's starting growth pushed up to trend growth plus its own residual volatility, still bounded by each model's own start cap (Graham excluded, same as fair_value_bear).",
        "how_to_use": [
            "The optimistic end of the range.",
            "NULL under the same conditions as fair_value, AND whenever no applicable model has enough growth history to build a scenario.",
        ],
        "vs_peers": "No — compare price to value.",
    },
    "margin_of_safety_bear": {
        "name": "Safety mgn (bear)",
        "category": "Intrinsic Value",
        "unit": "%",
        "what_it_is": "margin_of_safety computed against fair_value_bear instead of fair_value.",
        "how_to_use": [
            "Still positive here means undervalued even in the pessimistic case — the strongest single value test here, and a good value-trap screen.",
            "NULL wherever fair_value_bear is NULL, which includes names with too little growth history to build a scenario. Filtering on this therefore excludes them rather than judging them — that's intended.",
        ],
        "vs_peers": "No — it's already an absolute discount measure.",
    },
    "margin_of_safety_bull": {
        "name": "Safety mgn (bull)",
        "category": "Intrinsic Value",
        "unit": "%",
        "what_it_is": "margin_of_safety computed against fair_value_bull instead of fair_value.",
        "how_to_use": [
            "NULL wherever fair_value_bull is NULL.",
        ],
        "vs_peers": "No — it's already an absolute discount measure.",
    },
    "valuation_guardrail_flag": {
        "name": "Valuation guardrail",
        "category": "Intrinsic Value",
        "unit": "",
        "what_it_is": "True when the DCF/DDM's discount rate needed its minimum-spread floor to stay above terminal growth, or the ROIC-WACC gap implies an implausibly wide excess return persisting forever in the terminal value.",
        "how_to_use": [
            "A warning, not a hard block — the fair-value numbers still compute either way.",
            "Filter to True to find names whose valuation assumptions are at the edge of plausibility.",
        ],
        "vs_peers": "No — an absolute mechanical check.",
    },
    "bear_flag_cash_conversion": {
        "name": "Bear flag: cash conversion",
        "category": "Intrinsic Value",
        "unit": "",
        "what_it_is": "True when trailing OCF/net-income (ocf_to_ni_3y, falling back to 5y) is below 100% — cash flow isn't keeping pace with reported earnings.",
        "how_to_use": [
            "Visible only — nothing here adjusts fair_value_bear automatically. No professional framework exists for turning this into a numeric valuation haircut, so it's shown for you to weigh yourself.",
            "STANDARD screen type only; NULL/False elsewhere.",
        ],
        "vs_peers": "No — an absolute threshold on the company's own ratio.",
    },
    "bear_flag_moat_narrowing": {
        "name": "Bear flag: thin moat",
        "category": "Intrinsic Value",
        "unit": "",
        "what_it_is": "True when the ROIC-WACC gap (roic_vs_wacc) is below 3 percentage points — little or no economic moat by this proxy.",
        "how_to_use": [
            "Visible only, same as the other bear flags — not applied to any number.",
            "STANDARD screen type only; NULL/False elsewhere.",
        ],
        "vs_peers": "No — an absolute threshold on the company's own spread.",
    },
    "bear_flag_interest_coverage": {
        "name": "Bear flag: weak coverage",
        "category": "Intrinsic Value",
        "unit": "",
        "what_it_is": "True when interest_coverage (EBIT / interest expense) is below 2x.",
        "how_to_use": [
            "Visible only, same as the other bear flags — not applied to any number.",
        ],
        "vs_peers": "No — an absolute threshold, not peer-relative.",
    },
    "bear_flag_earnings_quality": {
        "name": "Bear flag: earnings quality",
        "category": "Intrinsic Value",
        "unit": "",
        "what_it_is": "True when beneish_m_score is above -1.78 — Beneish's own back-tested cutoff for a likely earnings manipulator.",
        "how_to_use": [
            "Visible only, same as the other bear flags — not applied to any number.",
        ],
        "vs_peers": "No — Beneish's absolute cutoff.",
    },
    "bear_flag_count": {
        "name": "Bear flags",
        "category": "Intrinsic Value",
        "unit": "",
        "what_it_is": "How many of the 4 bear-flag checks are True for this symbol (0-4).",
        "how_to_use": [
            "A quick triage column — sort descending to find names with the most red flags stacked up.",
        ],
        "vs_peers": "No — a simple count.",
    },
    # ------------------------------------------------------------------ #
    # Relative Strength
    # ------------------------------------------------------------------ #
    "rs_rank": {
        "name": "RS Rank",
        "category": "Relative Strength",
        "unit": "",
        "what_it_is": "IBD-style 0-99 strength rank of a weighted trailing return, ranked "
                      "within the symbol's security type.",
        "how_to_use": [
            "Input data: total return over four ~3-month windows from adjusted-close prices, "
            "the most recent window weighted heaviest (40/20/20/20).",
            "Ranked WITHIN its security type (stock / ETF / fund / …) so funds don't distort "
            "stocks — the universe is ~65% mutual funds.",
            "Higher means stronger price performance than most others of its type; 80+ leads.",
            "NULL when under ~1 year of price history.",
        ],
        "vs_peers": "Ranked within its security type, not the whole universe.",
    },
    # ------------------------------------------------------------------ #
    # Statement items (raw financial-statement line items — charted over time on the
    # Fundamentals view; not currently filter metrics. Add more here as needed.)
    # ------------------------------------------------------------------ #
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
    # ------------------------------------------------------------------ #
    # Scores
    # ------------------------------------------------------------------ #
    "growth_score": {
        "name": "Growth",
        "category": "Score",
        "unit": "",
        "what_it_is": "0-100 weighted average of each growth metric's Scoring-Rule strength, "
                      "ranked across the universe.",
        "how_to_use": [
            "Built from: 3y & 5y revenue and EPS CAGR, 3y FCF CAGR, latest revenue & EPS "
            "YoY, and EPS-growth steadiness.",
            "Higher means faster, steadier growth than most of the universe.",
        ],
        "vs_peers": "No — ranked across the whole universe.",
    },
    "income_score": {
        "name": "Income",
        "category": "Score",
        "unit": "",
        "what_it_is": "0-100 weighted average of each dividend metric's Scoring-Rule strength, "
                      "mostly judged against absolute income targets.",
        "how_to_use": [
            "Built from: dividend yield, 5y dividend growth, coverage, consistency, payout "
            "ratio and consecutive-years streak.",
            "Higher means a stronger, better-covered income profile; NaN for non-payers.",
        ],
        "vs_peers": "Absolute — judged against income targets (e.g. yield 2-6%, REIT 4-10%), "
                    "not peers.",
    },
    "momentum_score": {
        "name": "Momentum",
        "category": "Score",
        "unit": "",
        "what_it_is": "0-100 weighted average of each price-trend metric's Scoring-Rule strength.",
        "how_to_use": [
            "Built from: RS Rank, distance above the 50 / 150 / 200-day moving averages, "
            "and distance from the 52-week high.",
            "Higher means stronger recent price action.",
        ],
        "vs_peers": "Mixed — RS Rank ranks within security type; the MA / 52-week pieces "
                    "score against fixed lines.",
    },
    "orphan_score": {
        "name": "Orphan",
        "category": "Score",
        "unit": "",
        "what_it_is": "Growth Score, but only for stocks that are both under-covered by "
                      "analysts and pass a basic solvency check — the \"neglected firm "
                      "effect\" candidates. NaN for everything else.",
        "how_to_use": [
            "Under-covered = no analyst estimates, or fewer analysts than its own peer "
            "group's (Standard / REIT) median.",
            "Solvency check = Current ratio at or above 1.1, so a low score isn't just "
            "masking a company in financial trouble.",
            "Only computed for Common Stock — Standard and REIT; everything else is NaN.",
            "A high value means fast/steady growth AND little analyst attention — "
            "possible mispricing, not a guaranteed bargain.",
        ],
        "vs_peers": "No — the coverage gate is peer-relative, but the score itself is "
                    "growth_score's universe ranking.",
    },
    "overall_score": {
        "name": "Overall",
        "category": "Score",
        "unit": "",
        "what_it_is": "0-100 weighted blend of the five category scores.",
        "how_to_use": [
            "Default weights (adjustable in Settings): Quality 25, Value 22, Momentum 20, "
            "Growth 18, Income 15.",
            "Categories with no data (e.g. a fund's fundamentals) drop out and the rest re-weight.",
            "Higher is a stronger all-round profile; sort to triage, then read the category "
            "scores to see why.",
        ],
        "vs_peers": "No — a blend of the category scores (each already ranked/scored).",
    },
    "quality_score": {
        "name": "Quality",
        "category": "Score",
        "unit": "",
        "what_it_is": "0-100 weighted average of each profitability/balance-sheet metric's "
                      "Scoring-Rule strength, ranked within the industry (then sector).",
        "how_to_use": [
            "Built from: ROIC, gross margin, ROE, Altman-Z, FCF margin, net margin, "
            "debt/equity, current ratio, interest coverage, operating margin, ROA, debt/EBITDA.",
            "Higher means more profitable / financially sound than industry/sector peers.",
        ],
        "vs_peers": "Yes — peer-relative (industry, then sector).",
    },
    "value_score": {
        "name": "Value",
        "category": "Score",
        "unit": "",
        "what_it_is": "0-100 weighted average of each valuation metric's Scoring-Rule strength, "
                      "ranked within the industry (then sector).",
        "how_to_use": [
            "Built from: EV/EBITDA, P/FCF, margin of safety, P/E, PEG, P/B, P/S, EV/Sales, "
            "forward P/E (cheaper = stronger).",
            "Higher means cheaper than industry/sector peers on the weighted multiples.",
        ],
        "vs_peers": "Yes — peer-relative (industry, then sector).",
    },
    # ------------------------------------------------------------------ #
    # Classification — text labels the company/fund carries, not metrics.
    # Filtered by picking values from a list (is any of / is none of).
    # ------------------------------------------------------------------ #
    "fund_family": {
        "name": "Fund family",
        "category": "Classification",
        "unit": "",
        "what_it_is": "The fund's provider / sponsor (e.g. Vanguard, iShares). Funds only — blank for stocks.",
        "how_to_use": ["Pick one or more providers to include, or exclude families you don't want."],
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
    "sector": {
        "name": "Sector",
        "category": "Classification",
        "unit": "",
        "what_it_is": "The broad economic sector (e.g. Technology, Healthcare) from the data provider.",
        "how_to_use": ["Pick one or more sectors to include, or exclude sectors you don't want."],
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
