"""Transactional email through Brevo (the same account and sender the Flutter
app's Supabase functions use).

send_email() never raises: the callers send mail AFTER the real work is done
(e.g. an account is already activated), so a mail problem must be reported
back, never turned into an error or a rollback.
"""
import html
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


def activation_email(first_name: str | None, login_email: str, missing_id: bool, missing_address: bool) -> tuple[str, str]:
    """(subject, html) for a placeholder instructor whose login the SAO admin
    just activated. Wording copied from the Flutter app's admin-activate-account
    Supabase function (needsPasswordSetup branch)."""
    first = html.escape(" ".join((first_name or "").split()) or "there")
    email = html.escape(login_email or "")
    missing = " and ".join(x for x, on in (("ID", missing_id), ("address", missing_address)) if on)
    complete_note = f"<p>After you sign in, the app will ask you to complete your {missing}.</p>" if missing else ""
    subject = "Account Activated — Set Your Password"
    body = f"""
          <h3>Hello, {first}!</h3>
          <p>Your iEvaluate account has been <b>activated by the SAO admin</b>. Your evaluation records are already linked to it.</p>
          <p><b>Your login email:</b> {email}</p>
          <p><b>To set your password:</b></p>
          <ol>
            <li>Open the iEvaluate app.</li>
            <li>Tap <b>Forgot Password?</b> on the login screen.</li>
            <li>Enter <b>{email}</b> and tap <b>Send Code</b>.</li>
            <li>Type the code we email you, then choose your new password.</li>
          </ol>
          {complete_note}
          <p>If you did not expect this email, please contact the SAO office immediately.</p>
          <p>Best regards,<br>SAO Management Team</p>
        """
    return subject, body
