"""Email sending behind an interface. Tests use FakeEmailSender; no real calls here."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Protocol

import httpx

log = logging.getLogger("garage.mail")


def mask_email(email: str) -> str:   # same masking as email_service.py:18-24
    if not email or "@" not in email:
        return "***"
    local, domain = email.rsplit("@", 1)
    return f"*@{domain}" if len(local) <= 1 else f"{local[0]}***@{domain}"


class EmailSender(Protocol):
    def send(self, to: str, subject: str, text: str, html: str | None = None) -> bool: ...


@dataclass
class Sent:
    to: str
    subject: str
    text: str


class FakeEmailSender:
    def __init__(self):
        self.outbox: list[Sent] = []

    def send(self, to, subject, text, html=None) -> bool:
        self.outbox.append(Sent(to, subject, text))
        return True


class ResendEmailSender:
    """Resend over HTTPS (payload shape from email_service.py:96-120, own sender name; sync)."""
    URL = "https://api.resend.com/emails"

    def __init__(self, api_key: str | None = None, sender: str | None = None, dry_run: bool | None = None,
                 http: httpx.Client | None = None):
        self.api_key = api_key or os.environ.get("RESEND_API_KEY")
        self.sender = sender or os.environ.get("GARAGE_EMAIL_FROM") or os.environ.get("EMAIL_FROM") \
            or "onboarding@resend.dev"
        self.dry_run = (os.environ.get("EMAIL_DRY_RUN", "false").lower() == "true") if dry_run is None else dry_run
        self._http = http or httpx.Client(timeout=10.0)

    def send(self, to, subject, text, html=None) -> bool:
        if self.dry_run:
            log.info("[DRY RUN] email suppressed for %s", mask_email(to))
            return True
        if not self.api_key:
            log.error("RESEND_API_KEY not configured - email not sent")
            return False
        payload = {"from": f"AutoSafe Garage <{self.sender}>", "to": [to], "subject": subject, "text": text}
        if html:
            payload["html"] = html
        try:
            r = self._http.post(self.URL, headers={"Authorization": f"Bearer {self.api_key}"}, json=payload)
        except httpx.HTTPError as exc:
            log.error("email send exception: %s", type(exc).__name__)
            return False
        if r.status_code != 200:
            log.error("email send failed: status=%s", r.status_code)
            return False
        return True


class ConsoleEmailSender:
    """Local development only: prints the message (including the sign-in link) to stdout."""

    def send(self, to, subject, text, html=None) -> bool:
        print(f"--- email to {to} ---\n{subject}\n{text}\n---")
        return True
