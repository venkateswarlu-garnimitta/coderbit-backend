from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .. import config

logger = logging.getLogger(__name__)


def _format_scheduled_label(scheduled_at: datetime) -> str:
    """Render the scheduled time in the configured display timezone.

    Times are stored/received in UTC; naive values are assumed to be UTC.
    """
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

    try:
        tz = ZoneInfo(config.DISPLAY_TIMEZONE)
        return scheduled_at.astimezone(tz).strftime("%A, %B %d, %Y at %I:%M %p %Z")
    except ZoneInfoNotFoundError:
        logger.warning(
            "Unknown DISPLAY_TIMEZONE %r; falling back to UTC", config.DISPLAY_TIMEZONE
        )
        return scheduled_at.astimezone(timezone.utc).strftime(
            "%A, %B %d, %Y at %I:%M %p UTC"
        )


def send_email(
    *, to: str, subject: str, body_text: str, body_html: str | None = None
) -> tuple[bool, str | None]:
    if not config.SMTP_USER or not config.SMTP_PASSWORD:
        return (
            False,
            "Set SMTP_USER and SMTP_PASSWORD in backend/.env (use a Gmail App Password)",
        )

    smtp_from = config.SMTP_FROM or config.SMTP_USER
    smtp_host = config.SMTP_HOST or "smtp.gmail.com"

    message = EmailMessage()
    message["From"] = smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body_text)
    if body_html:
        message.add_alternative(body_html, subtype="html")

    try:
        with smtplib.SMTP(smtp_host, config.SMTP_PORT, timeout=30) as server:
            if config.SMTP_USE_TLS:
                server.starttls()
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(message)
        logger.info("Sent email to %s: %s", to, subject)
        return True, None
    except smtplib.SMTPAuthenticationError:
        logger.exception("SMTP authentication failed for %s", config.SMTP_USER)
        return False, "Gmail login failed — use an App Password, not your normal password"
    except Exception as exc:
        logger.exception("Failed to send email to %s", to)
        return False, str(exc)


def send_interview_scheduled_email(
    *,
    email: str,
    password: str | None,
    scheduled_at: datetime,
    duration_minutes: int,
) -> tuple[bool, str | None]:
    login_url = f"{config.FRONTEND_URL.rstrip('/')}/login"
    scheduled_label = _format_scheduled_label(scheduled_at)
    subject = "Your Interview is Scheduled"

    password_line = (
        f"  Password: {password}"
        if password
        else "  Password: (use the password set when your account was created)"
    )
    password_html = (
        f"<li><strong>Password:</strong> {password}</li>"
        if password
        else "<li><strong>Password:</strong> use the password set when your account was created</li>"
    )

    body_text = f"""Hello,

Your coding interview has been scheduled.

Login credentials:
  Email: {email}
{password_line}

Interview details:
  Scheduled time: {scheduled_label}
  Duration: {duration_minutes} minutes

You can start writing the exam 2 minutes before the scheduled time.

Login here: {login_url}

Best regards,
Fission Labs Interview Platform
"""

    body_html = f"""\
<html>
<body style="font-family: Arial, sans-serif; color: #222; line-height: 1.6;">
  <p>Hello,</p>
  <p>Your coding interview has been scheduled.</p>
  <p><strong>Login credentials:</strong></p>
  <ul>
    <li><strong>Email:</strong> {email}</li>
    {password_html}
  </ul>
  <p><strong>Interview details:</strong></p>
  <ul>
    <li><strong>Scheduled time:</strong> {scheduled_label}</li>
    <li><strong>Duration:</strong> {duration_minutes} minutes</li>
  </ul>
  <p>You can start writing the exam <strong>2 minutes before</strong> the scheduled time.</p>
  <p><a href="{login_url}">Login to the platform</a></p>
  <p>Best regards,<br>Fission Labs Interview Platform</p>
</body>
</html>
"""

    return send_email(
        to=email,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
    )


def send_interview_scheduled_email_task(
    *,
    email: str,
    password: str | None,
    scheduled_at: datetime,
    duration_minutes: int,
) -> None:
    """Background task: send interview invite email after the HTTP response."""
    email_sent, email_error = send_interview_scheduled_email(
        email=email,
        password=password,
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
    )
    if email_sent:
        logger.info("Interview invite email sent to %s", email)
    else:
        logger.error(
            "Failed to send interview invite email to %s: %s", email, email_error
        )
