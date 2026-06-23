"""
Email-a-selection utility (the first entry on the Utilities page).

Composes an email report about a saved symbol selection: a recipient, a title, an
intro paragraph, and a `.syms` selection file picked through the app's native dialog
(`ui/selection_io.load_dialog`). The chosen symbols are listed with the info the
`.syms` file carries (Company / Sector / Industry).

Send builds a nicely formatted HTML email (inline-styled so mail clients render it)
with a plain-text fallback, and sends it via `core.mailer` (SMTP config in settings,
credentials in .env). The same symbol set drives both the on-screen table and the
email's HTML table.

Pure-`render()` module (no module-level `st.*`), like `ui/calibration.py`, so the
Utilities page can import it and call `render()` under a collapse toggle.
"""

from __future__ import annotations

import re
from html import escape

import pandas as pd
import streamlit as st

from core import mailer
from ui import selection_io as SEL

# Recipients are typed in one box, separated by commas, semicolons or whitespace.
_SPLIT_RECIPIENTS = re.compile(r"[,;\s]+")


def _parse_recipients(raw: str) -> list[str]:
    """Split the To field into a clean list of addresses (one or many)."""
    return [a for a in _SPLIT_RECIPIENTS.split(raw.strip()) if a]


def _symbols_frame(items: dict[str, dict]) -> pd.DataFrame:
    """The loaded selection as a Symbol / Company / Sector / Industry table."""
    rows = [
        {
            "Symbol": sym,
            "Company": info.get("company", ""),
            "Sector": info.get("sector", ""),
            "Industry": info.get("industry", ""),
        }
        for sym, info in items.items()
    ]
    return pd.DataFrame(rows, columns=["Symbol", "Company", "Sector", "Industry"])


def _plain_text(intro: str, frame: pd.DataFrame) -> str:
    """Plain-text fallback body: the intro then a simple symbol list."""
    lines = []
    if intro.strip():
        lines += [intro.strip(), ""]
    lines.append("Symbols:")
    for _, r in frame.iterrows():
        bits = [b for b in (r["Company"], r["Sector"], r["Industry"]) if b]
        lines.append(f"  {r['Symbol']}" + (f" — {' · '.join(bits)}" if bits else ""))
    return "\n".join(lines)


def _html(title: str, intro: str, frame: pd.DataFrame) -> str:
    """A nicely formatted, inline-styled HTML email body (works across mail clients)."""
    th = ("style=\"text-align:left;padding:8px 12px;border-bottom:2px solid #2f6db5;"
          "font-size:13px;color:#2f6db5;\"")
    td = "style=\"padding:8px 12px;border-bottom:1px solid #e3e6ea;font-size:14px;\""
    sym_td = ("style=\"padding:8px 12px;border-bottom:1px solid #e3e6ea;font-size:14px;"
              "font-weight:bold;\"")

    rows = "".join(
        f"<tr><td {sym_td}>{escape(str(r['Symbol']))}</td>"
        f"<td {td}>{escape(str(r['Company']))}</td>"
        f"<td {td}>{escape(str(r['Sector']))}</td>"
        f"<td {td}>{escape(str(r['Industry']))}</td></tr>"
        for _, r in frame.iterrows()
    )
    intro_html = (f"<p style=\"font-size:15px;line-height:1.6;color:#333;\">"
                  f"{escape(intro.strip()).replace(chr(10), '<br>')}</p>"
                  if intro.strip() else "")

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
      <tbody>{rows}</tbody>
    </table>
  </div>
</body>
</html>"""


def render() -> None:
    """The email-report form: fields, .syms picker, symbol list, Send (real email)."""
    to = st.text_input("To", key="util_email_to",
                       placeholder="name@example.com, other@example.com")
    recipients = _parse_recipients(to)
    title = st.text_input("Title", key="util_email_title",
                          placeholder="Email subject")
    intro = st.text_area("Body intro", key="util_email_intro",
                         placeholder="Intro text that opens the email…")

    if st.button("📂 Pick selection (.syms)", key="util_email_pick"):
        data = SEL.load_dialog(kind="symbols")
        if data:
            st.session_state["util_email_sel"] = data
            st.rerun()

    sel = st.session_state.get("util_email_sel")
    items: dict[str, dict] = sel["items"] if sel else {}
    if sel:
        st.caption(f"{sel['path'].name} — {len(items)} symbols")
        st.dataframe(_symbols_frame(items), width="stretch", hide_index=True)
    else:
        st.caption("No selection picked yet.")

    ready = bool(recipients) and bool(title.strip()) and bool(items)
    if st.button("✉️ Send email", key="util_email_send", disabled=not ready, type="primary"):
        if not mailer.is_configured():
            st.error("Email isn't set up yet. Add GMAIL_USER and GMAIL_APP_PASSWORD to "
                     "your .env (see .env.template), then restart the app.")
            return
        frame = _symbols_frame(items)
        try:
            mailer.send_html(
                to=recipients,
                subject=title.strip(),
                html=_html(title.strip(), intro, frame),
                text=_plain_text(intro, frame),
            )
        except Exception as exc:  # surface any SMTP/auth/connection error to the user
            st.error(f"Send failed: {exc}")
        else:
            st.success(f"Email sent to {', '.join(recipients)}.")
            st.toast(f"Email sent to {len(recipients)} recipient(s)")
