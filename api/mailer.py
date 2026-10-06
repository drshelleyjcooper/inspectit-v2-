"""Outgoing email over SMTP (GoDaddy Workspace Email by default).

Configured entirely by environment variables (see config.py). If
SMTP_PASSWORD is unset, email is disabled: send_mail() logs that it skipped
and returns False, so local development and tests never need a mail account.

Mail is always sent from a FastAPI BackgroundTask, after the HTTP response,
so a slow or failing mail server can never delay or break a visitor's form
submission. Failures are logged, never raised.
"""
import json
import logging
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr

from . import config
from .ratelimit import RateLimiter

log = logging.getLogger("inspectit")

invite_mail_limiter = RateLimiter(config.INVITE_EMAIL_LIMIT,
                                  config.INVITE_EMAIL_WINDOW_S)


def enabled() -> bool:
    """True if any transport is configured."""
    if config.RESEND_API_KEY and config.MAIL_TO:
        return True
    return bool(config.SMTP_PASSWORD and config.SMTP_USER and config.MAIL_TO)


def _clean(value: str, limit: int = 200) -> str:
    """One line, no control characters: stops header injection via form input."""
    return " ".join(str(value or "").split())[:limit]


def build_message(subject: str, body: str, reply_to: str = None,
                  to: str = None) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = _clean(subject, 150)
    msg["From"] = formataddr((config.MAIL_FROM_NAME, config.MAIL_FROM))
    msg["To"] = _clean(to, 320) if to else config.MAIL_TO
    if reply_to:
        msg["Reply-To"] = _clean(reply_to, 320)
    msg.set_content(body)
    return msg


def _send_resend(subject: str, body: str, reply_to: str = None, to: str = None):
    """Resend HTTPS API. Raises on any non-2xx so send_mail logs it."""
    payload = {"from": formataddr((config.MAIL_FROM_NAME, config.MAIL_FROM)),
               "to": [_clean(to, 320) if to else config.MAIL_TO], "subject": _clean(subject, 150), "text": body}
    if reply_to:
        payload["reply_to"] = _clean(reply_to, 320)
    req = urllib.request.Request(
        "https://api.resend.com/emails", data=json.dumps(payload).encode(),
        method="POST",
        headers={"Authorization": "Bearer " + config.RESEND_API_KEY,
                 "Content-Type": "application/json",
                 "User-Agent": "inspectit-api/1.0"})   # Resend rejects the default UA
    with urllib.request.urlopen(req, timeout=15) as resp:
        if not 200 <= resp.status < 300:
            raise RuntimeError(f"Resend returned HTTP {resp.status}")


def send_mail(subject: str, body: str, reply_to: str = None,
              to: str = None) -> bool:
    """Send one message to `to` (default: MAIL_TO, the owner). True on success."""
    if not enabled():
        log.info("EMAIL disabled (no RESEND_API_KEY or SMTP_PASSWORD); skipped: %s",
                 _clean(subject))
        return False
    try:
        if config.RESEND_API_KEY:
            _send_resend(subject, body, reply_to, to)
            return True
        msg = build_message(subject, body, reply_to, to)
        ctx = ssl.create_default_context()
        if config.SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT,
                                      timeout=15, context=ctx)
        else:
            server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15)
            server.starttls(context=ctx)
        with server:
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as exc:                      # never break the request
        log.error("EMAIL send failed (%s): %s", _clean(subject), exc)
        return False


def demo_request_email(name: str, email: str, count) -> tuple:
    subject = f"New demo request: {_clean(name, 80)}"
    body = (f"Someone asked to schedule a demo on inspectit.app.\n\n"
            f"Name:   {_clean(name)}\n"
            f"Email:  {_clean(email, 320)}\n"
            f"Vehicles or properties: {count if count else 'not given'}\n\n"
            f"Reply to this email to answer them directly.\n")
    return subject, body


def trial_signup_email(name: str, email: str, company: str, info: dict,
                       trial_ends: str) -> tuple:
    subject = f"New free trial: {_clean(name, 80)}"
    lines = "".join(f"  {k}: {v}\n" for k, v in (info or {}).items()) or "  (none given)\n"
    body = (f"A new free trial just started on inspectit.app.\n\n"
            f"Name:    {_clean(name)}\n"
            f"Email:   {_clean(email, 320)}\n"
            f"Account: {_clean(company)}\n"
            f"Trial ends: {trial_ends}\n\n"
            f"Sign-up answers:\n{lines}")
    return subject, body


def human_duration(minutes: int) -> str:
    if minutes % 1440 == 0:
        d = minutes // 1440
        return f"{d} day" + ("" if d == 1 else "s")
    if minutes >= 120 and minutes % 60 == 0:
        return f"{minutes // 60} hours"
    return f"{minutes} minutes"


def password_reset_email(name: str, link: str, ttl_min: int) -> tuple:
    subject = "Reset your Inspectit.app password"
    body = (f"Hi {_clean(name, 80) or 'there'},\n\n"
            f"Someone asked to reset the password for your Inspectit.app account. "
            f"To choose a new password, open this link:\n\n{link}\n\n"
            f"The link works once and expires in {human_duration(ttl_min)}.\n\n"
            f"If you didn't ask for this, you can ignore this email. "
            f"Your password won't change.\n\n"
            f"Inspectit.app\n")
    return subject, body


def password_changed_email(name: str, login_url: str) -> tuple:
    subject = "Your Inspectit.app password was changed"
    body = (f"Hi {_clean(name, 80) or 'there'},\n\n"
            f"The password for your Inspectit.app account was just changed, and "
            f"you were signed out everywhere.\n\n"
            f"If this was you, log in here: {login_url}\n\n"
            f"If it wasn't you, reply to this email right away.\n\n"
            f"Inspectit.app\n")
    return subject, body


def invitation_email(inviter_name: str, company: str, roles: list, link: str,
                     ttl_days: int) -> tuple:
    who = _clean(inviter_name, 80) or "A colleague"
    org = _clean(company, 120) or "their organization"
    subject = f"{who} invited you to {org} on Inspectit.app"
    role_line = ", ".join(sorted(_clean(r, 60) for r in roles)) or "team member"
    body = (f"Hi,\n\n"
            f"{who} invited you to join {org} on Inspectit.app, the app for "
            f"inspections, maintenance, repairs and records.\n\n"
            f"Your role: {role_line}\n\n"
            f"To accept, open this link and choose a password:\n\n{link}\n\n"
            f"If you already have an Inspectit.app account with this email, "
            f"you will be asked to confirm your current password instead.\n\n"
            f"The invitation expires in {ttl_days} days. If you weren't "
            f"expecting it, you can ignore this email and nothing will happen. "
            f"You can reply to this email to reach {who}.\n\n"
            f"Inspectit.app\n")
    return subject, body


def _day(ts) -> str:
    return ts.strftime("%B %d, %Y").replace(" 0", " ")


def trial_reminder_email(name: str, company: str, ends_at, link: str) -> tuple:
    subject = "Your Inspectit.app free trial ends in a week"
    body = (f"Hi {_clean(name, 80) or 'there'},\n\n"
            f"Your 30-day free trial for {_clean(company, 120)} ends on "
            f"{_day(ends_at)}.\n\n"
            f"To keep using Inspectit.app without a break, choose a plan here:\n\n"
            f"{link}\n\n"
            f"If you don't subscribe by then, your account will be paused. "
            f"Nothing is deleted, and access comes back as soon as you subscribe.\n\n"
            f"Questions? Just reply to this email.\n\n"
            f"Inspectit.app\n")
    return subject, body


def trial_ended_email(name: str, company: str, grace_ends, link: str) -> tuple:
    subject = "Your Inspectit.app free trial has ended"
    body = (f"Hi {_clean(name, 80) or 'there'},\n\n"
            f"The free trial for {_clean(company, 120)} has ended. You can keep "
            f"using Inspectit.app until {_day(grace_ends)} while you decide.\n\n"
            f"To keep going without interruption, subscribe here:\n\n{link}\n\n"
            f"After {_day(grace_ends)} the account is paused. Your records are not "
            f"deleted, and access returns as soon as you subscribe.\n\n"
            f"Need more time or have questions? Reply to this email.\n\n"
            f"Inspectit.app\n")
    return subject, body


def account_paused_email(name: str, company: str, delete_at, link: str) -> tuple:
    subject = "Your Inspectit.app account is paused"
    body = (f"Hi {_clean(name, 80) or 'there'},\n\n"
            f"The grace period for {_clean(company, 120)} is over, so the account "
            f"is now paused.\n\n"
            f"Your records are kept safe until {_day(delete_at)}. Subscribe any "
            f"time before then and everything is back exactly as you left it:\n\n"
            f"{link}\n\n"
            f"Questions, or need more time? Reply to this email.\n\n"
            f"Inspectit.app\n")
    return subject, body


def deletion_warning_email(name: str, company: str, delete_at, link: str) -> tuple:
    subject = "Action needed: your Inspectit.app records will be deleted"
    body = (f"Hi {_clean(name, 80) or 'there'},\n\n"
            f"The records for {_clean(company, 120)} will be deleted on "
            f"{_day(delete_at)} unless you subscribe.\n\n"
            f"To keep them, subscribe here before that date:\n\n{link}\n\n"
            f"If you need a copy of your records or more time, reply to this "
            f"email as soon as you can.\n\n"
            f"Inspectit.app\n")
    return subject, body


def welcome_email(name: str, company: str, role: str, link: str, ttl_min: int) -> tuple:
    subject = "Welcome to Inspectit.app: set your password"
    where = f" for {_clean(company, 120)}" if company else ""
    role_line = f"Your role: {_clean(role, 60)}\n\n" if role else ""
    body = (f"Hi {_clean(name, 80) or 'there'},\n\n"
            f"An Inspectit.app account has been set up{where} for you.\n\n"
            f"{role_line}"
            f"To start, choose your password with this link:\n\n{link}\n\n"
            f"The link works once and expires in {human_duration(ttl_min)}. If it "
            f"expires, use \"Forgot your password?\" on the log-in page to get a new one.\n\n"
            f"If you weren't expecting this, you can ignore this email.\n\n"
            f"Inspectit.app\n")
    return subject, body
