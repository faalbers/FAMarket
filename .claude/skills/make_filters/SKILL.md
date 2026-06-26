---
name: make_filters
description: This skill only gets triggered with /make_filters command. ask me to continue with this skill before going ahead.
---

# make_filters

This skill will create valid FAMarket `.filt` filter set from plain-English instructions that you only read in dev_docs/create_filters.md
It will also create a report at the end about the created filters with
Follow the Procedure steps explained below

## Purpose

Create FAMarket filters that I can use to find stocks in the database universe.

## When to use

it is very clearly explained in the description:

## When NOT to use

if it the prompt did NOT use the skill command make_filters.

## Inputs you need from the user

List what you must know before producing output, and what to ask for if missing.
- go through each filter you want o create one by one and do the following things before creating them
- make sure you give me the name and a comprehensible brief of what each filter does you are about to create.
- becore creating the filter, ask me the options to save it or chat about it first.

## Reference: how filters work in this repo

First you need to prepare yourself with the following steps:
- Pull the real details from the codebase so the skill stays accurate.
- You will first figure out all the filter system in the project and all the ways you can create filters using available filter parameters.
- You will figure out what all these filter parameters mean and what they represent.
- You will look at the analysis_layer scoring_rules how to interprete these filters well.
- You will also have a total understanding on the Category Scores and how they are created and how they are used in the filter system.
- Block model + `.filt` JSON: `ui/filter_engine.py`
- Per-`screen_type` metric applicability: `ui/filter_registry.py`
- Filter variants (Value / vs Sector / vs Industry / Score): Make sure to use these if it makes sense per instructions
- Where filters are saved: `settings.FILTERS_DIR`

## Metric gotchas

**eps_cagr_1y / 3y / 5y — sign-change gives N/A**
These metrics return N/A when either the base-year or current-year EPS is ≤ 0
(a loss-to-profit or profit-to-loss transition makes the CAGR undefined).
This means the block silently fails for companies that were previously loss-making,
even if they are now highly profitable (e.g. AMZN, TSLA).

Rule: whenever you write a block using any `eps_cagr_*` metric, always add
`forward_eps_growth` at the same threshold as an OR child. This lets companies
that cannot show historical EPS CAGR pass on analyst consensus for future EPS
growth instead.

Example — instead of:
  eps_cagr_3y >= 10  (no fallback)

Write:
  eps_cagr_3y >= 10  OR  forward_eps_growth >= 10

**revenue_cagr_5y / eps_cagr_5y — missing history gives N/A**
5-year CAGR metrics need 5+ years of annual data. Recent IPOs or data gaps produce
N/A, which silently fails the block (same symptom as the eps sign-change issue, but
caused by short history, not a sign change).

Rule: whenever you write a block using any 5y CAGR metric, add the 3y variant at
the same threshold as an OR child.

Example:
  revenue_cagr_5y >= 7  OR  revenue_cagr_3y >= 7

**altman_z — N/A for tech/software companies**
Altman Z-score was designed for manufacturing companies. It is not computable for
asset-light tech, software, or streaming businesses (the formula relies on tangible-
asset ratios that don't apply), so it returns N/A and silently fails those stocks
even when they are financially healthy by every other measure.

Rule: if the filter already includes other financial health blocks (debt_to_ebitda,
interest_coverage, current_ratio), add `altman_z is null` as an OR child. Companies
for which the metric is inapplicable are not penalised; the other blocks still guard
financial health.

Example:
  altman_z >= 2.5  OR  altman_z is null

## Calibration guidance

This section records threshold values that turned out to be too tight in practice,
discovered by reviewing Filter Fail results together. It will be updated over time
as new findings come up — if a future Filter Fail review reveals another param that
needs a better default, add it here.

Use these as informed starting points, not hard rules. Adjust up or down based on
the filter's stated intent (a strict value screen may want tighter values; a broad
growth screen should use the looser ones below).

**current_ratio**
Textbook floor is 1.2, but retailers and e-commerce companies (e.g. AMZN) structurally
run below this — they collect cash from customers before paying suppliers, so a low
current ratio is a sign of business model strength, not risk.
Recommended floor for broad growth screens: **1.1**

**atr_pct**
A 5.0% ceiling is very tight and catches solid industrials and construction companies
by small margins (e.g. FIX at 5.08%). These are not high-risk stocks — the ATR just
reflects normal sector volatility.
Recommended ceiling for broad growth screens: **5.5%**

## End Report

After creating the filters, create a full report that replaces or creates a file under dev_docs/filters_report.md with the following information:
- List of all the filters created with their names
- Add a description for each filter explaining what your thinking pattern was during creation.
- Add a list of parameters that i can sort to find the best results from that filter and how I should interprete these parameters.

## Procedure

1. Go through the reference so you get up to date first on what you need to know.
2. Read the plain-English instructions that you only read in dev_docs/create_filters.md to create the filters
3. Find all the stock market analysis knowledge you can find with web search to get the best results on the instructions in create_filters.md
4. Go through Inputs you need from the user explained above before creating the filters
5. Before saving, check whether `<name>.filt` already exists in FILTERS_DIR.
   - If it does NOT exist (and no `<name>_v2.filt` etc. exist either): save as `<name>.filt`.
   - If `<name>.filt` exists: find the next free version suffix (`_v2`, `_v3`, …) and save there.
   Then write the End Report.

