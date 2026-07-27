"""
Standalone feasibility test for the "guidance-vs-actual tracking" idea
(dev_docs/FAMarket_Epansion.md, Topic 1) — NOT wired into analysis_layer,
filter_registry.py, or param_hints.py. Ad-hoc validation script only.

For each symbol: pulls the most recent 8-K press-release exhibit from SEC EDGAR
(free), regex-locates the guidance paragraph, sends it to Claude Haiku for
structured extraction, and reports REAL per-call timing + token cost (computed
from response.usage, not an estimate) so it can be checked against the Anthropic
console.

Usage:
    python -m scripts.test_guidance_extraction
    python -m scripts.test_guidance_extraction --symbols FDX,NKE,COST

Requires ANTHROPIC_API_KEY in .env (see .env.template) and `pip install anthropic`.
Results (extracted JSON + source excerpt) are saved to
dev_docs/guidance_extraction_test_results.json for manual review.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from functools import lru_cache

import requests

from config import secrets, settings
from core.logging_config import get_logger, setup_logging
from core.net import configure_tls

log = get_logger("scripts")

DEFAULT_SYMBOLS = ["FDX", "NKE"]

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/index.json"
ARCHIVE_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{doc}"

# Fair-access pause between SEC requests (SEC allows ~10 req/s; well under that).
_SEC_PAUSE_SECS = 0.15

GUIDANCE_KEYWORDS = re.compile(
    r"\b(guidance|we expect|outlook|anticipat\w+|forecast\w*|(?:full[- ]year|quarterly) "
    r"(?:revenue|earnings) (?:of|to be))\b",
    re.IGNORECASE,
)

# claude-haiku-4-5 published rate ($/million tokens) — for computing real cost
# from response.usage, so it can be checked against the Anthropic console.
HAIKU_IN_PER_M = 1.00
HAIKU_OUT_PER_M = 5.00

EXTRACTION_PROMPT = """You are extracting forward-looking financial guidance from an \
excerpt of a company's earnings press release. If the company gives quantitative \
guidance for an upcoming period, extract it. If it doesn't, say so.

Respond with ONLY a JSON object (no markdown fencing, no other text), in exactly this shape:
{{
  "has_guidance": true or false,
  "period": "<e.g. 'Q3 FY2026' or 'full year 2026', or null>",
  "revenue_guidance_low": <number in millions USD, or null>,
  "revenue_guidance_high": <number in millions USD, or null>,
  "eps_guidance_low": <number, or null>,
  "eps_guidance_high": <number, or null>,
  "confidence": "high", "medium", or "low"
}}

Excerpt:
\"\"\"
{excerpt}
\"\"\"
"""


def _sec_get(url: str) -> requests.Response:
    resp = requests.get(url, headers={"User-Agent": secrets.SEC_USER_AGENT}, timeout=20)
    resp.raise_for_status()
    time.sleep(_SEC_PAUSE_SECS)
    return resp


@lru_cache(maxsize=1)
def _cik_map() -> dict[str, int]:
    data = _sec_get(SEC_TICKERS_URL).json()
    return {
        rec["ticker"].strip().upper(): int(rec["cik_str"])
        for rec in data.values()
        if rec.get("ticker") and rec.get("cik_str") is not None
    }


def _latest_8k_document(cik: int) -> tuple[str, str] | None:
    """Return (doc_url, doc_type) for the most recent 8-K's press-release exhibit,
    or its primary document if no EX-99 exhibit is found. None if no 8-K exists."""
    filings = _sec_get(SUBMISSIONS_URL.format(cik=cik)).json()["filings"]["recent"]
    forms = filings["form"]
    idx = next((i for i, f in enumerate(forms) if f == "8-K"), None)
    if idx is None:
        return None

    accession = filings["accessionNumber"][idx].replace("-", "")
    primary_doc = filings["primaryDocument"][idx]
    index = _sec_get(ARCHIVE_INDEX_URL.format(cik=cik, accession=accession)).json()
    items = index.get("directory", {}).get("item", [])

    exhibit = next(
        (it["name"] for it in items
         if "ex99" in it["name"].lower().replace("-", "").replace(".", "", 1)
         or str(it.get("type", "")).upper().startswith("EX-99")),
        None,
    )
    doc_name = exhibit or primary_doc
    url = ARCHIVE_DOC_URL.format(cik=cik, accession=accession, doc=doc_name)
    return url, ("exhibit" if exhibit else "primary_8k_body")


def _guidance_excerpt(html: str, context_chars: int = 800) -> str | None:
    import trafilatura

    text = trafilatura.extract(html, include_comments=False, include_tables=False)
    if not text:
        return None
    match = GUIDANCE_KEYWORDS.search(text)
    if not match:
        return None
    start = max(0, match.start() - context_chars // 2)
    end = min(len(text), match.end() + context_chars)
    return text[start:end]


def _extract_guidance(client, excerpt: str) -> dict:
    t0 = time.perf_counter()
    resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=300,
        messages=[{"role": "user", "content": EXTRACTION_PROMPT.format(excerpt=excerpt)}],
    )
    elapsed = time.perf_counter() - t0

    raw = resp.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"parse_error": True, "raw_text": raw}

    cost = (
        resp.usage.input_tokens / 1_000_000 * HAIKU_IN_PER_M
        + resp.usage.output_tokens / 1_000_000 * HAIKU_OUT_PER_M
    )
    return {
        "parsed": parsed,
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
        "cost_usd": cost,
        "claude_seconds": elapsed,
    }


def run(symbols: list[str]) -> list[dict]:
    import anthropic

    configure_tls()
    client = anthropic.Anthropic(api_key=secrets.require("ANTHROPIC_API_KEY"))

    results = []
    for sym in symbols:
        row: dict = {"symbol": sym}
        t_fetch0 = time.perf_counter()
        try:
            cik = _cik_map().get(sym.upper())
            if cik is None:
                row["error"] = "not an SEC domestic filer (no CIK found)"
                results.append(row)
                print(f"{sym}: no CIK found (skipping)")
                continue

            doc = _latest_8k_document(cik)
            if doc is None:
                row["error"] = "no 8-K filing found"
                results.append(row)
                print(f"{sym}: no 8-K found (skipping)")
                continue
            doc_url, doc_kind = doc
            html = _sec_get(doc_url).text
            excerpt = _guidance_excerpt(html)
            row["fetch_seconds"] = time.perf_counter() - t_fetch0
            row["doc_url"] = doc_url
            row["doc_kind"] = doc_kind

            if excerpt is None:
                row["error"] = "no guidance paragraph found in filing"
                results.append(row)
                print(f"{sym}: fetched ({row['fetch_seconds']:.1f}s) — "
                      f"no guidance paragraph found")
                continue
            row["excerpt"] = excerpt

            extraction = _extract_guidance(client, excerpt)
            row.update(extraction)
            results.append(row)
            print(
                f"{sym}: fetch={row['fetch_seconds']:.1f}s  "
                f"claude={extraction['claude_seconds']:.1f}s  "
                f"tokens(in/out)={extraction['input_tokens']}/{extraction['output_tokens']}  "
                f"cost=${extraction['cost_usd']:.5f}  "
                f"has_guidance={extraction['parsed'].get('has_guidance')}"
            )
        except Exception as exc:  # noqa: BLE001 — throwaway script, keep going per symbol
            row["error"] = str(exc)
            results.append(row)
            print(f"{sym}: FAILED — {exc}")

    return results


def main() -> None:
    setup_logging()
    p = argparse.ArgumentParser(description="Test SEC EDGAR + Claude guidance extraction")
    p.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS),
                    help="Comma-separated tickers (default: %(default)s)")
    args = p.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    t0 = time.perf_counter()
    results = run(symbols)
    total_seconds = time.perf_counter() - t0

    ok = [r for r in results if "cost_usd" in r]
    total_cost = sum(r["cost_usd"] for r in ok)
    print(f"\n{len(ok)}/{len(symbols)} succeeded — total wall time {total_seconds:.1f}s, "
          f"total computed cost ${total_cost:.5f}")
    if ok:
        print(f"avg per symbol: {total_seconds / len(symbols):.1f}s, "
              f"${total_cost / len(ok):.5f}")

    out_path = settings.BASE_DIR / "dev_docs" / "guidance_extraction_test_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nFull results (incl. excerpts) saved to {out_path}")


if __name__ == "__main__":
    main()
