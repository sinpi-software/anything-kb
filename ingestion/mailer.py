"""Transactional email via Resend. When RESEND_API_KEY is unset (local dev),
send_email logs the message + link instead of sending so the flows still work.
Callers treat sends as best-effort — a failure must never break the user action."""

import logging
import os

import httpx

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
AUTH_EMAIL_FROM = os.environ.get("AUTH_EMAIL_FROM", "noreply@mail.sinpi.software")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "https://desk.sinpi.software").rstrip("/")

_RESEND_URL = "https://api.resend.com/emails"
_log = logging.getLogger("mailer")


def link(path: str) -> str:
    return f"{APP_BASE_URL}{path}"


def send_email(to: str, subject: str, html: str) -> None:
    """Best-effort: never raises. A delivery failure is logged (with Resend's
    reason, e.g. an unverified sender domain) so it is diagnosable, but callers
    treat email as fire-and-forget — the user's action must not fail with it."""
    if not RESEND_API_KEY:
        _log.warning("email not sent (no RESEND_API_KEY) to=%s subject=%s\n%s", to, subject, html)
        return
    try:
        resp = httpx.post(
            _RESEND_URL,
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={"from": AUTH_EMAIL_FROM, "to": [to], "subject": subject, "html": html},
            timeout=15,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # Resend puts the reason in the body — surface it rather than a bare status.
        _log.error(
            "email rejected by Resend to=%s subject=%s from=%s status=%s body=%s",
            to,
            subject,
            AUTH_EMAIL_FROM,
            exc.response.status_code,
            exc.response.text[:300],
        )
    except httpx.HTTPError as exc:
        _log.error("email send failed to=%s subject=%s: %s", to, subject, exc)
