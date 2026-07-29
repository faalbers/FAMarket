"""
Email body builders for the "email a symbol selection" tool.

Extracted from `ui/email_report.py`. The HTML is fully inline-styled because
mail clients strip stylesheets, and every field is escaped.
"""

from __future__ import annotations

from html import escape
from typing import Mapping

Info = Mapping[str, Mapping[str, str]]


def _rows(symbols: list[str], info: Info) -> list[tuple[str, str, str, str]]:
    out: list[tuple[str, str, str, str]] = []
    for symbol in symbols:
        entry = info.get(symbol, {})
        out.append(
            (
                symbol,
                str(entry.get("company", "")),
                str(entry.get("sector", "")),
                str(entry.get("industry", "")),
            )
        )
    return out


def plain(title: str, intro: str, symbols: list[str], info: Info) -> str:
    """Plain-text fallback: the intro, then a simple symbol list."""
    lines: list[str] = []
    if title.strip():
        lines += [title.strip(), ""]
    if intro.strip():
        lines += [intro.strip(), ""]
    lines.append("Symbols:")
    for symbol, company, sector, industry in _rows(symbols, info):
        bits = [b for b in (company, sector, industry) if b]
        lines.append(f"  {symbol}" + (f" — {' · '.join(bits)}" if bits else ""))
    return "\n".join(lines)


def html(title: str, intro: str, symbols: list[str], info: Info) -> str:
    """Inline-styled HTML body that survives mail-client sanitising."""
    th = (
        'style="text-align:left;padding:8px 12px;border-bottom:2px solid #2f6db5;'
        'font-size:13px;color:#2f6db5;"'
    )
    td = 'style="padding:8px 12px;border-bottom:1px solid #e3e6ea;font-size:14px;"'
    sym_td = (
        'style="padding:8px 12px;border-bottom:1px solid #e3e6ea;font-size:14px;'
        'font-weight:bold;"'
    )

    body_rows = "".join(
        f"<tr><td {sym_td}>{escape(symbol)}</td>"
        f"<td {td}>{escape(company)}</td>"
        f"<td {td}>{escape(sector)}</td>"
        f"<td {td}>{escape(industry)}</td></tr>"
        for symbol, company, sector, industry in _rows(symbols, info)
    )
    intro_html = (
        f'<p style="font-size:15px;line-height:1.6;color:#333;">'
        f"{escape(intro.strip()).replace(chr(10), '<br>')}</p>"
        if intro.strip()
        else ""
    )

    return f"""\
<!DOCTYPE html>
<html>
<body style="margin:0;padding:24px;background:#ffffff;font-family:Arial,Helvetica,sans-serif;">
  <div style="max-width:640px;margin:0 auto;padding:4px 0;">
    <h1 style="margin:0 0 16px 0;font-size:22px;color:#1a2733;">{escape(title)}</h1>
    {intro_html}
    <table style="border-collapse:collapse;width:100%;margin-top:12px;">
      <thead><tr>
        <th {th}>Symbol</th><th {th}>Company</th><th {th}>Sector</th><th {th}>Industry</th>
      </tr></thead>
      <tbody>{body_rows}</tbody>
    </table>
  </div>
</body>
</html>"""
