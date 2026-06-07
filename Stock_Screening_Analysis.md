# Stock Market Screening System

## Project Blueprint:
Purpose:
- A brainstorm and design document to plan the full system before coding begins.
- Make sure to ask questions and show me possible subjects that would be interesting to consider while brainstorming on this project.
- No actual Python code created in this project
- Code will be created in seperate Claude Code Project
- Do not ever edit this file. I will manually edit it as needed.

Goal:
- Build a flexible Python-based system that gathers stock market data and screens for mainly long-term investment candidates.

Data Source:
- Free end not payed APIs — primarily `yfinance` (Yahoo Finance). But other free API's or systems can be proposed of course.

Interface:
- Python scripts (run from terminal or Claude Code).

## What the System Does:
1. Symbols Fetch:ather a collection af available stock symbols investable in the US stock market
2. Quote Fetch: Get quote data for those symbols
3. Financial Data Fetch: Get financial historic data that can be used to create fundamental and techival metrics for those symbols. Historic if possible so we can calculate growth in the historic periods.
4. Historic Chart data: Daily chart data for those symbols
5. Calculate: Calculate the financial metrics to use in the screener analysis
6. Peers comparison: Find the stocks of the same sector or industry and get the median (or better value) of the metrics representing the peers.
7. Score or rank: For each stock against defined criteria
8. Filter: Filter down the stocks with certain data and metrics of the symbols. Create a and/or filter system that parameters with values and other values so it can create a list of resulting stock candidates
8. Report: results in a readable format (console output, CSV, HTML report, PDF, or other)

## System Architecture Overview
- Data Layer:
    - Fetch all data from API's or other scraping systems
    - add rate limit to API requests to not go over and get blocked
    - Store data in SQLite databases
    - Make rules to fetch stock data based on the last time they were fetched and other criteria we will discuss
- Analysis Layer:
    - Create a SQLite database with all relevant data from stocks so we can filter them in a filtering system
    - This database will also include calculated fundamental and Technical metrics
    - It will also include peers data from the same sector or industry the stock belongs to calculated wit median (or better value)
    - It will also include score and ranking based un certain criteria
- Filter Interface Layer:
    - Create a filtering system that filters parameter values of Analysis data
    - It needs to be able to save or import these filter collections
    - The Filter sresult can then open the Output Interface explained below
- Output Interface Layer:
    - This interface wil show the resulting stock symbols these will be selectable.
    - On the selected stocks we can run a bunch of analyzing functions like:
        - Compare stock prices in a certain date range
        - Compare financial data in periods like anual, quarterly, ttm
        - Open them in stock market analyzing websites w=se we can compare them there (like Yahoo Finance, Finviz, TradingView, and others)
        - Brainstorm with me what other possibilities will be interesting as functionality

The three layers are intentionally separate. This means you can swap out a data source, add a new indicator, or change the output format without breaking the rest of the system.

## How to find stock symbol candidates
- I think Polygon API tickers is a very good candidate to get them . Please suggest other possible ways to get these. They should all be free and not payed

## Data to collect
- Stock quote data using yfinance or other systems.
    - also gather the sector and industry info of course
    - I guess this is also where Technical data is included ? Correct me if I'm wrong.
- Historic financial business data of stock
- Chart daily historic data of stocks

## Build Phases of Project
Suggest me the build phases

## How to resume a new project brainstorming chat
- Every time a new chat is resumed you will read the brainstorm_roadmap.md to get up to date with everything that was discussed and updated.
- The roadmap clearly defines where we left off last time and we need to continue from there with the brainstorm.
- You , being claude, will update in brainstorm_roadmap.md with everything discussed and decided during a specific topic planning at the end of the topic discussion. Not at the end of the sub topic , but at the end of the topic. Unless I specifically ask to update the file. Then update it with all the new planning information.
- When you ask questions during brainstorm, ask them one by one. Not all at once
