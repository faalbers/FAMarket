# signals.db — analyst estimates, earnings surprises and ownership

Forward-looking and "smart money" data per operating company from Yahoo Finance: analyst consensus estimates, the history of earnings beats/misses, and insider/institutional ownership.
Stored raw, as Yahoo returns it. The derived metrics (forward growth, revisions, average surprise, …) are in `analysis.db`.

Join key: `symbol` (see `symbols.md` for shared conventions).

## How it is retrieved

- Source: Yahoo Finance, one pass per symbol reading its analyst, earnings and holder data.
- Only `stock`, `adr` (and REIT-type stocks) — funds and ETFs have no rows.
- Refetched weekly. Symbols with no analyst coverage return nothing and simply have no rows.
- Each table holds the **latest snapshot**, replaced in place on refetch (except `earnings_surprise`, which accumulates quarters). Check `fetched_at`.

## Table `estimates` — one row per (symbol, horizon)

Analyst consensus, from Yahoo's earnings estimate, revenue estimate, growth estimate, EPS trend and EPS revisions data.

- **`horizon`** — the period the estimate is for:
  - `0q` = current fiscal quarter (not yet reported), `+1q` = next quarter.
  - `0y` = current fiscal year, `+1y` = next fiscal year.
  - `LTG` = long-term growth (only the growth columns are filled).
- **EPS estimate** — `eps_avg` (consensus), `eps_low`, `eps_high`, `eps_year_ago` (actual EPS a year earlier), `eps_num_analysts`, `eps_growth` (consensus vs year-ago, **fraction**).
- **Revenue estimate** — `rev_avg`, `rev_low`, `rev_high`, `rev_year_ago`, `rev_num_analysts`, `rev_growth` (**fraction**). In the reporting currency.
- **Growth** — `growth_stock_trend` (the stock's expected growth), `growth_index_trend` (the S&P 500's, for comparison). Fractions.
  - On the `LTG` row only `growth_index_trend` is filled — it is one market-wide number, the same for every stock. There is no usable per-stock long-term growth.
- **EPS trend** — how the consensus EPS for that horizon has drifted: `eps_trend_current`, `eps_trend_7d`, `eps_trend_30d`, `eps_trend_60d`, `eps_trend_90d` (the consensus value 7/30/60/90 days ago).
- **EPS revisions** — number of analysts who raised / cut their estimate: `eps_rev_up_7d`, `eps_rev_up_30d`, `eps_rev_down_7d`, `eps_rev_down_30d`.
- **`currency`**, **`fetched_at`**.

## Table `earnings_surprise` — one row per (symbol, period_end)

Reported quarterly EPS vs the consensus before the report, from Yahoo's earnings history (last ~4 quarters per fetch).

- **`period_end`** — fiscal quarter end.
- **`eps_estimate`**, **`eps_actual`**, **`eps_difference`** (actual − estimate).
- **`surprise_percent`** — (actual − estimate) / |estimate|, as a **fraction** (0.10 = beat by 10%).
- **`fetched_at`** — when that row was last written.
- New fetches update existing quarters and add new ones, so older quarters accumulate across fetches (some symbols go back many years).

## Table `ownership` — one row per symbol

Upcoming event dates and ownership snapshot, from Yahoo's event calendar, insider purchase summary and major-holders data.

- **Dates** — `next_earnings_date` (earliest of Yahoo's projected dates), `ex_dividend_date`, `dividend_date` (payment). ISO dates; may be past dates if Yahoo hasn't updated.
- **Insider activity**, over the last `insider_period_months` (almost always 6):
  - Shares: `insider_buy_shares`, `insider_sell_shares`, `insider_net_shares` (bought − sold), `insider_total_held`.
  - **Fractions**: `insider_pct_net` (net shares bought / shares held by insiders), `insider_buy_pct`, `insider_sell_pct`.
  - Transaction counts: `insider_buy_trans`, `insider_sell_trans`, `insider_net_trans`.
- **`institutions_count`** — number of institutions holding the stock.
- Insider and institutional ownership *percentages* are not here — they are in `quotes.db` (`heldPercentInsiders`, `heldPercentInstitutions`).

## Known limitations

- Snapshot only (except `earnings_surprise`): there is no archive of how estimates looked in the past beyond the 7-90-day trend columns.
- Coverage is limited to names analysts follow; small caps often have no rows.
- Yahoo's per-share estimates are in the trading currency for most US listings, but can be in the reporting currency for some foreign filers — check `currency`.
