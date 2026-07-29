"""
Utilities — small tools that don't belong to a data page.

Today: email a symbol selection. Credentials live in `.env` only
(`GMAIL_USER` / `GMAIL_APP_PASSWORD`); SMTP TLS relies on `configure_tls()`
having run at startup.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core import mailer
from services import email_body
from ui import selection_io as SEL

router = APIRouter(prefix="/api/utilities")


@router.get("/email/status")
def email_status() -> dict[str, Any]:
    return {"configured": mailer.is_configured()}


class EmailRequest(BaseModel):
    to: list[str]
    subject: str
    intro: str = ""
    symbols: list[str]


@router.post("/email/send")
def send_email(req: EmailRequest) -> dict[str, Any]:
    if not mailer.is_configured():
        raise HTTPException(
            status_code=400,
            detail="Email isn't set up — add GMAIL_USER and GMAIL_APP_PASSWORD to .env.",
        )
    recipients = [address.strip() for address in req.to if address.strip()]
    if not recipients or not req.subject.strip() or not req.symbols:
        raise HTTPException(status_code=400, detail="recipients, a subject and symbols are required")

    info = SEL.symbol_info(req.symbols)
    try:
        mailer.send_html(
            to=recipients,
            subject=req.subject.strip(),
            html=email_body.html(req.subject, req.intro, req.symbols, info),
            text=email_body.plain(req.subject, req.intro, req.symbols, info),
        )
    except Exception as exc:  # SMTP/auth errors surface to the user verbatim
        raise HTTPException(status_code=502, detail=f"Sending failed: {exc}") from exc

    return {"sent": True, "recipients": len(recipients), "symbols": len(req.symbols)}
