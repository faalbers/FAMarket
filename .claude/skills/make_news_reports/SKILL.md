---
name: make_news_reports
description: This skill only gets triggered with the /make_news_reports command. It reads every AI news .md file and creates a plain-language summary PDF for each stock. Confirm the stock list with me once before generating.
---

# make_news_reports

This skill turns the scraped AI news reports into easy-to-read **summary PDFs** — one
per stock. It reads each `<SYMBOL>_ai_news_report.md` in the AI news folder, writes a
plain-language, dyslexia-friendly summary using the fixed prompt below, and saves it as
`<SYMBOL>_ai_news_report_summary.pdf` in the same folder.

## Purpose

I'm not a professional investor and I'm mildly dyslexic. I want a clear, jargon-free
summary of each stock's recent news that I can actually understand at a glance — saved
as a PDF next to the raw `.md` report.

## When to use

Only when the prompt used the `/make_news_reports` command.

## When NOT to use

If the prompt did NOT use the `/make_news_reports` command, do nothing.

## Where things live

- Input `.md` files and output `_summary.pdf` files: `settings.AI_NEWS_REPORTS_DIR`
  (the `ai_news_reports/` folder; the folder is gitignored).
- File discovery + company-name parsing + PDF rendering: `reporting/ai_news_summary.py`.
- Render one summary to a PDF (the command the skill runs per stock):
  `python -m reporting.ai_news_summary <SYMBOL> <summary_md_path> [--company "Name"]`.
  Run it with the project venv Python (`.\.venv\Scripts\python.exe` on Windows).

## Run flow (confirm once, then all)

1. List the stocks found, wait for ONE go-ahead, then generate every summary PDF and
   give a single end report. Do not ask me to approve each summary individually.

## The summary prompt (use this EXACTLY for every stock)

For each stock's `.md` file, produce the summary by following this prompt to the letter:

```
You are a friendly investing explainer. I'm giving you a markdown file named
<SYMBOL>_ai_news_report.md — automatically scraped recent news about ONE stock.

WHO YOU'RE WRITING FOR:
I am NOT a professional investor, and I'm mildly dyslexic. Write so a normal
person can understand it AND so it's easy on the eyes.
- Plain, everyday language. Short sentences.
- Avoid finance and tech jargon. If a technical term is truly needed, follow it
  immediately with a plain-English explanation in parentheses.
  e.g. "book-to-bill of 1.24 (they're getting more new orders than they're
  shipping — a sign demand is growing)".
- Always spell out what a fact MEANS for the company or the stock, not just the
  number itself.

HOW TO FORMAT IT (easy-read, for dyslexia):
- One idea per sentence. One idea per bullet. Keep bullets to a single line
  where you can.
- Prefer bullets over paragraphs. Never write a wall of text — no paragraph
  longer than 2 short sentences.
- Put plenty of blank space between sections so blocks are easy to separate.
- Start each bullet with a **bold anchor word** so I can skim.
- Put every number on its own bullet — don't cram several numbers into one
  sentence.
- Use **bold** for emphasis, NOT italics or ALL CAPS.
- Keep the wording simple and consistent. Don't be clever or wordy.

Format of the file:
- A header: ticker, company name, generation date, and how many
  company-specific articles it holds.
- Then numbered "## Article N" blocks, each with **Title**, **Date**,
  **Source** (publisher · URL), and **Article text**.

Read carefully and note:
- Some articles have the FULL body; others say
  "(summary only — full text could not be retrieved)" or "(no text available)".
  Use ONLY what is actually present. Never invent facts to fill a gap.
- Articles overlap heavily and repeat the same story — MERGE repeated points,
  don't say the same thing twice.
- Articles span a date range and some are old. Lean on recent ones, and flag
  when a point comes from an older article.

Write the summary in this structure, using these plain headings:

**Bottom line** — 1–2 sentences at the very top: the single most important
takeaway, in plain words.

1. **The big picture** — 2–3 sentences: what's going on with this company right
   now, and why people are talking about it.
2. **What's happening** — bullets of the main news items in plain words; each
   ends with (date · source). Merge repeats.
3. **Why it could be good** — plain-language bullets (only points the articles
   actually support).
4. **Why it could be risky** — plain-language bullets (same rule).
5. **Key numbers, explained** — one number per bullet. For EACH: the number,
   then a plain "what this means" in everyday terms. Mark any numbers that
   disagree between articles.
6. **What the experts think** — what analysts/sources conclude, in plain words
   (e.g. "most rate it a Buy; their price targets suggest the stock could rise
   about X%").
7. **How solid is this summary** — 1–2 sentences: how many articles had full
   text vs just a headline, the date range covered, and how much to trust it.
8. **Jargon buster** — a short list: any finance/tech term you had to use,
   each defined in ONE simple sentence.

Rules:
- Stay faithful to the source text. Attribute claims; don't present opinion as
  fact.
- Give NO price predictions or buy/sell advice of your own — only report what
  the articles say.
- If the file has almost no usable content, just say so — don't pad.
```

### Notes on rendering the prompt's output to PDF

The PDF renderer (`reporting/ai_news_summary.py`) understands this exact markdown subset,
so write the summary using it:
- Section titles as `## The big picture` (or `**Bottom line**` for the top line).
- Points as `- ` bullets, with the **bold anchor word** at the start.
- Inline emphasis with `**bold**` (not italics or CAPS).
- A blank line between sections for white space.

## Procedure

1. Read the reference section above so you know where files live and how to render a PDF.
2. Discover the input files with `reporting.ai_news_summary.iter_report_files()` (or list
   `settings.AI_NEWS_REPORTS_DIR` for `*_ai_news_report.md`, excluding `*summary*`).
3. Show me the list of stocks (ticker + company) you found and **wait for one go-ahead.**
4. For each stock, in order:
   a. Read its `.md` file. Get the company name via
      `reporting.ai_news_summary.company_from_md(path)`.
   b. Write the summary by following the prompt above, using the markdown subset.
   c. Save the summary text to a scratchpad markdown file, then render it:
      `python -m reporting.ai_news_summary <SYMBOL> <scratch_summary.md> --company "<Company>"`.
      This overwrites any existing `<SYMBOL>_ai_news_report_summary.pdf`.
   d. If a `.md` has essentially no usable article content, skip it (don't emit an empty
      PDF) and note it for the end report.
5. End report: list every summary PDF written (and any stocks skipped), with the output
   folder path (`settings.AI_NEWS_REPORTS_DIR`).
