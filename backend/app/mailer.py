"""Transactional email through Brevo (the same account and sender the Flutter
app's Supabase functions use).

send_email() never raises: callers send mail AFTER the real work is done,
so a mail problem must be reported back, never turned into an error or a
rollback.
"""
import logging

import httpx

from app.db import get_settings

log = logging.getLogger("ievaluate.mailer")

BREVO_URL = "https://api.brevo.com/v3/smtp/email"


def _client() -> httpx.Client:
    """Tests swap this for a client on an httpx.MockTransport."""
    return httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False)


def send_email(to: str, subject: str, html_content: str) -> tuple[bool, str | None]:
    """(True, None) when Brevo accepted the message, else (False, reason):
    'not_configured' (BREVO_API_KEY / BREVO_SENDER_EMAIL empty) or
    'send_failed' (non-2xx reply, timeout, network or any other error).
    Never logs the API key or the request headers."""
    try:
        s = get_settings()
        if not s.email_configured:
            log.warning("email not sent: BREVO_API_KEY / BREVO_SENDER_EMAIL is not set")
            return False, "not_configured"
        payload = {
            "sender": {"name": s.brevo_sender_name, "email": s.brevo_sender_email},
            "to": [{"email": to}],
            "subject": subject,
            "htmlContent": html_content,
        }
        headers = {
            "api-key": s.brevo_api_key,
            "content-type": "application/json",
            "accept": "application/json",
        }
        with _client() as client:
            res = client.post(BREVO_URL, json=payload, headers=headers)
        if 200 <= res.status_code < 300:
            return True, None
        log.error("Brevo API error: status=%s body=%s", res.status_code, (res.text or "")[:300])
        return False, "send_failed"
    except Exception as exc:  # noqa: BLE001 - mail must never break the caller
        log.error("Brevo API error: %s", type(exc).__name__)
        return False, "send_failed"

