"""
SMTP email sending — used by the Utilities ▸ Email a symbol selection tool.

Split the usual way: non-sensitive SMTP config (host/port/TLS, from-address) lives
in `config/settings.py`; the username + password are secrets in `.env`
(GMAIL_USER / GMAIL_APP_PASSWORD, via `config/secrets.py`).

TLS note: verification uses the stdlib `ssl` default context, which
`core.net.configure_tls()` has already pointed at the OS trust store (app.py runs
it at startup) — so STARTTLS succeeds behind this machine's TLS interception, the
same reason the HTTPS fetchers need it.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from config import secrets, settings


# .env variable names for the sending account's credentials.
_USER_KEY = "GMAIL_USER"
_PASSWORD_KEY = "GMAIL_APP_PASSWORD"


def is_configured() -> bool:
    """True when both SMTP credentials are present in .env (the send precondition)."""
    return secrets.has(_USER_KEY) and secrets.has(_PASSWORD_KEY)


def send_html(*, to: str | list[str], subject: str, html: str, text: str = "") -> None:
    """Send one multipart (plain-text + HTML) email. Raises on any SMTP/auth error.

    `to` is one address or a list of them (all go on the To header, so every
    recipient is sent the same message). `text` is the plain-text fallback for
    non-HTML clients; `html` is the rich body.
    """
    recipients = [to] if isinstance(to, str) else list(to)
    if not recipients:
        raise ValueError("no recipient addresses given")

    user = secrets.require(_USER_KEY)
    # Google displays app passwords as "xxxx xxxx xxxx xxxx"; the real secret is the
    # 16 chars without spaces, so drop ALL whitespace however it was pasted.
    password = "".join(secrets.require(_PASSWORD_KEY).split())
    sender = settings.EMAIL_FROM.strip() or user

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(text or "This message is best viewed in an HTML-capable client.")
    msg.add_alternative(html, subtype="html")

    context = ssl.create_default_context()
    if settings.SMTP_PORT == 465:  # implicit SSL
        with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, context=context) as s:
            s.login(user, password)
            s.send_message(msg)
    else:                          # STARTTLS (587) or plain
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as s:
            s.ehlo()
            if settings.SMTP_USE_TLS:
                s.starttls(context=context)
                s.ehlo()
            s.login(user, password)
            s.send_message(msg)
