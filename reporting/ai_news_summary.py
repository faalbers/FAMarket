"""
AI news SUMMARY report — render an LLM-written summary into a per-stock PDF.

Companion to `ai_news_report.py`: that module scrapes article bodies into
`<symbol>_ai_news_report.md`; the `/make_news_reports` skill then reads each `.md`,
writes a plain-language, dyslexia-friendly summary (markdown), and this module turns
that summary into `<symbol>_ai_news_report_summary.pdf` in the SAME folder
(`settings.AI_NEWS_REPORTS_DIR`).

The summary TEXT is produced by the LLM (code can't summarize) — this module only
does the markdown -> PDF rendering, reusing `core.pdf.ReportBuilder` (never reportlab
directly). It maps a small, predictable markdown subset:

  * `## X` / `# X` / `**Heading**` / `1. **X**` lines  -> heading / subheading
  * `- ` / `* ` lines                                   -> bullet (with inline **bold**)
  * blank line                                          -> spacer
  * anything else                                       -> body paragraph (inline **bold**)

CLI (how the skill invokes it):
    python -m reporting.ai_news_summary <SYMBOL> <summary_md_path> [--company "Name"]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from config import settings
from core.logging_config import get_logger
from core.pdf import ReportBuilder

log = get_logger("reporting.ai_news_summary")

_SLUG = re.compile(r"[^A-Za-z0-9._-]+")
# "# AI News Report — APH (Amphenol Corporation)" -> company in the parentheses.
_HEADER = re.compile(r"^#\s*AI News Report.*\(([^)]+)\)\s*$")
# A numbered section like "1. **The big picture**" or a "**Heading**" line.
_NUM_HEAD = re.compile(r"^\d+\.\s*\*\*(.+?)\*\*\s*$")
_BOLD_HEAD = re.compile(r"^\*\*(.+?)\*\*\s*$")


def _slug(symbol: str) -> str:
    """Filesystem-safe symbol for the filename (mirrors ai_news_report._slug)."""
    s = _SLUG.sub("-", (symbol or "symbol").strip()).strip("-_.")
    return (s or "symbol")[:40]


def iter_report_files() -> list[tuple[str, Path]]:
    """Every input `<symbol>_ai_news_report.md` (NOT the *_summary* PDFs), as
    (symbol, path). Symbol = filename stem before `_ai_news_report`."""
    out: list[tuple[str, Path]] = []
    for path in sorted(settings.AI_NEWS_REPORTS_DIR.glob("*_ai_news_report.md")):
        if "summary" in path.name.lower():
            continue
        symbol = path.name.split("_ai_news_report", 1)[0]
        out.append((symbol, path))
    return out


def company_from_md(path: Path) -> str | None:
    """Pull the company name out of the report's `# AI News Report — SYM (Company)` header."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            m = _HEADER.match(line.strip())
            if m:
                return m.group(1).strip()
            if line.strip():  # header is the first non-blank line; stop once past it
                break
    except OSError:
        return None
    return None


def summary_to_pdf(symbol: str, summary_md: str, company: str | None = None) -> bytes:
    """Render an LLM summary (light markdown) to PDF bytes via ReportBuilder."""
    company = (company or "").strip()
    title = f"{symbol} — {company} · News summary" if company else f"{symbol} · News summary"
    b = ReportBuilder(title=f"{symbol} News Summary")
    b.cover_title(title)
    b.subtitle("Plain-language summary of recent news · auto-generated from scraped articles")
    b.spacer(6)

    for raw in (summary_md or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            b.spacer(4)
            continue
        # Headings: "## X" / "# X" / "**Heading**" / "1. **X**"
        if stripped.startswith("## "):
            b.heading(stripped[3:].strip())
        elif stripped.startswith("# "):
            b.heading(stripped[2:].strip())
        elif _NUM_HEAD.match(stripped):
            b.heading(_NUM_HEAD.match(stripped).group(1).strip())
        elif _BOLD_HEAD.match(stripped):
            b.heading(_BOLD_HEAD.match(stripped).group(1).strip())
        # Bullets: "- " / "* "
        elif stripped[:2] in ("- ", "* "):
            b.bullet(stripped[2:].strip())
        else:
            b.body(stripped)
    return b.build()


def save_summary_pdf(symbol: str, summary_md: str, company: str | None = None) -> Path:
    """Write `<symbol>_ai_news_report_summary.pdf` into AI_NEWS_REPORTS_DIR (overwrites)."""
    out_dir = settings.AI_NEWS_REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_slug(symbol)}_ai_news_report_summary.pdf"
    path.write_bytes(summary_to_pdf(symbol, summary_md, company))
    log.info("Wrote summary PDF %s", path.name)
    return path


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render an AI news summary markdown to a PDF.")
    ap.add_argument("symbol", help="Ticker, e.g. APH")
    ap.add_argument("summary_md_path", help="Path to the summary markdown file")
    ap.add_argument("--company", default=None, help="Company name for the title")
    args = ap.parse_args(argv)

    md_path = Path(args.summary_md_path)
    if not md_path.is_file():
        print(f"summary markdown not found: {md_path}", file=sys.stderr)
        return 2
    summary_md = md_path.read_text(encoding="utf-8")
    if not summary_md.strip():
        print(f"summary markdown is empty: {md_path}", file=sys.stderr)
        return 2
    path = save_summary_pdf(args.symbol, summary_md, args.company)
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
