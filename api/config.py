"""Environment-driven configuration.

Local dev needs no env vars at all: an embedded PostgreSQL (pgserver) is booted
automatically and files go to ./.filestore. In production on DigitalOcean App
Platform, set DATABASE_URL (Managed Postgres), JWT_SECRET, and the SPACES_*
variables.
"""
import logging
import os
import secrets
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 'development' (default) or 'production'. Production refuses unsafe settings
# instead of silently papering over them (see check_production_config).
APP_ENV = os.environ.get("APP_ENV", "development")
IS_PRODUCTION = APP_ENV == "production"

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# DEV_MODE=1: password-reset tokens are returned in API responses instead of
# being emailed (no email provider is wired up yet). Never enable in production.
DEV_MODE = os.environ.get("DEV_MODE", "") == "1"


def parse_origins(raw: str) -> list:
    """Comma-separated origins -> list (empty entries dropped)."""
    return [o.strip() for o in (raw or "").split(",") if o.strip()]


# CORS: wide open in dev; must be the app's real origin(s) in production.
ALLOWED_ORIGINS = parse_origins(os.environ.get("ALLOWED_ORIGINS", "")) or ["*"]


def check_production_config(is_production: bool, jwt_secret_env,
                            dev_mode: bool, allowed_origins: list):
    """Fail fast on unsafe production settings. Pure function (unit-tested)."""
    if not is_production:
        return
    problems = []
    if not jwt_secret_env:
        problems.append("JWT_SECRET must be set explicitly")
    if dev_mode:
        problems.append("DEV_MODE must not be enabled")
    if not allowed_origins or "*" in allowed_origins:
        problems.append("ALLOWED_ORIGINS must list the app's real origin(s)")
    if problems:
        raise RuntimeError(
            "Refusing to start with APP_ENV=production: " + "; ".join(problems))


_PRODUCTION_PROBLEMS = None

try:
    check_production_config(IS_PRODUCTION, os.environ.get("JWT_SECRET"),
                            DEV_MODE, ALLOWED_ORIGINS)
except RuntimeError as exc:
    _PRODUCTION_PROBLEMS = str(exc)
    logging.getLogger("inspectit").error("CONFIG: %s", exc)
    # Previously this was logged and swallowed, so a production deploy missing
    # JWT_SECRET would boot anyway and fall through to the dev-only file
    # fallback below. On App Platform that file is ephemeral, so every deploy
    # silently rotated the signing key and 401'd every existing session.
    # Fail the deploy instead — a broken deploy is visible, a rotating key is not.
    if IS_PRODUCTION:
        raise


def _jwt_secret() -> str:
    env = os.environ.get("JWT_SECRET")
    if env:
        return env
    # Dev-only fallback: generate once and persist beside the repo so tokens
    # survive restarts. Guarded twice on purpose — the check above should
    # already have stopped a production boot, but this fallback must never
    # be reachable in production even if that guard is changed or bypassed.
    if IS_PRODUCTION:
        raise RuntimeError(
            "JWT_SECRET must be set in production. The on-disk fallback is "
            "dev-only: on App Platform the container filesystem is ephemeral, "
            "so every deploy would issue a new signing key and invalidate all "
            "existing sessions.")
    f = PROJECT_ROOT / ".jwt_secret"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
    return f.read_text().strip()


JWT_SECRET = _jwt_secret()

# Calendar subscription links (api/calendar_feed.py) are signed with this key.
# Defaults to JWT_SECRET; set it separately so rotating the session key doesn't
# break everyone's calendar link (and vice versa). Changing it invalidates all
# calendar links.
CALENDAR_SECRET = os.environ.get("CALENDAR_SECRET", "") or JWT_SECRET
# How long a built feed is reused before it is rebuilt from the synced data,
# and how often one link may be fetched (calendar apps poll; this caps a
# misconfigured client or a leaked link).
CALENDAR_CACHE_S = int(os.environ.get("CALENDAR_CACHE_S", "900"))
CALENDAR_RATE_LIMIT = int(os.environ.get("CALENDAR_RATE_LIMIT", "60"))
CALENDAR_RATE_WINDOW_S = int(os.environ.get("CALENDAR_RATE_WINDOW_S", "3600"))

# Auth-route rate limiting (per client IP): max requests per window.
AUTH_RATE_LIMIT = int(os.environ.get("AUTH_RATE_LIMIT", "10"))
AUTH_RATE_WINDOW_S = int(os.environ.get("AUTH_RATE_WINDOW_S", "60"))

# Public marketing-site forms. Demo requests are unauthenticated, so they get
# their own, tighter per-IP budget than the auth routes.
DEMO_RATE_LIMIT = int(os.environ.get("DEMO_RATE_LIMIT", "5"))
DEMO_RATE_WINDOW_S = int(os.environ.get("DEMO_RATE_WINDOW_S", "3600"))
# Invitation emails: each signed-in user may trigger this many per window. Past
# it the invitation is still created (the admin can share the link by hand);
# only the email is skipped. Keeps our sending domain from being used as a relay.
INVITE_EMAIL_LIMIT = int(os.environ.get("INVITE_EMAIL_LIMIT", "20"))
INVITE_EMAIL_WINDOW_S = int(os.environ.get("INVITE_EMAIL_WINDOW_S", "3600"))
# Trial lifecycle (api/trial.py): remind admins this many days before the trial
# ends; the loop runs every TRIAL_CHECK_INTERVAL_S seconds (0 = off).
TRIAL_REMINDER_DAYS = int(os.environ.get("TRIAL_REMINDER_DAYS", "7"))
TRIAL_CHECK_INTERVAL_S = int(os.environ.get("TRIAL_CHECK_INTERVAL_S", "3600"))
# After the trial ends: full access for TRIAL_GRACE_DAYS, then the account is
# paused (402) and its records are kept DATA_RETENTION_DAYS. A final warning
# goes out RETENTION_WARNING_DAYS before deletion. Deletion itself only runs
# when RETENTION_PURGE_ENABLED=1 (default OFF: it removes a customer's access
# to their records, so switch it on deliberately).
TRIAL_GRACE_DAYS = int(os.environ.get("TRIAL_GRACE_DAYS", "7"))
DATA_RETENTION_DAYS = int(os.environ.get("DATA_RETENTION_DAYS", "30"))
RETENTION_WARNING_DAYS = int(os.environ.get("RETENTION_WARNING_DAYS", "7"))
RETENTION_PURGE_ENABLED = os.environ.get("RETENTION_PURGE_ENABLED", "") == "1"
# Length of the free trial granted by POST /auth/trial-signup.
TRIAL_DAYS = int(os.environ.get("TRIAL_DAYS", "30"))

# Outgoing email (api/mailer.py). inspectit.app's MX points at Microsoft 365
# (bought through GoDaddy): smtp.office365.com:587 (STARTTLS). If the mailbox is
# ever GoDaddy Workspace Email instead: smtpout.secureserver.net, port 465 (SSL).
# SMTP_PASSWORD is a secret: set it only in the environment, never in code.
# Unset = email disabled (dev/tests). Notifications go to MAIL_TO.
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.office365.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "info@inspectit.app")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
MAIL_FROM = os.environ.get("MAIL_FROM", "info@inspectit.app")
MAIL_FROM_NAME = os.environ.get("MAIL_FROM_NAME", "Inspectit.app")
MAIL_TO = os.environ.get("MAIL_TO", "info@inspectit.app")
# Preferred over SMTP when set: Resend (resend.com) HTTPS API. Needs inspectit.app
# verified at Resend (DNS records), and sends as MAIL_FROM. Secret: env only.
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")

# Public base URL used in links inside emails (password reset). Never derived
# from the request's Host header (that would allow reset-link poisoning).
APP_BASE_URL = os.environ.get(
    "APP_BASE_URL",
    "https://inspectit.app" if IS_PRODUCTION else "http://127.0.0.1:8100").rstrip("/")

# Global request-body ceiling (F4). Decided 2026-07-16: 75 MB.
MAX_BODY_MB = int(os.environ.get("MAX_BODY_MB", "75"))

# Connection pool sizing (F7): DO basic Managed Postgres allows ~22
# connections; stay well under it in production.
POOL_MIN = int(os.environ.get("POOL_MIN", "1"))
POOL_MAX = int(os.environ.get("POOL_MAX", "5" if IS_PRODUCTION else "10"))
ACCESS_TOKEN_TTL_MIN = int(os.environ.get("ACCESS_TOKEN_TTL_MIN", "30"))
REFRESH_TOKEN_TTL_DAYS = int(os.environ.get("REFRESH_TOKEN_TTL_DAYS", "30"))
RESET_TOKEN_TTL_MIN = int(os.environ.get("RESET_TOKEN_TTL_MIN", "60"))
# Links an admin triggers (welcome / reset by email) last longer: the person may
# not open their inbox right away.
ADMIN_LINK_TTL_MIN = int(os.environ.get("ADMIN_LINK_TTL_MIN", str(7 * 24 * 60)))
INVITE_TTL_DAYS = int(os.environ.get("INVITE_TTL_DAYS", "14"))

# Comma-separated emails that are promoted to platform admin at every startup
# (idempotent). This is how the first admin is bootstrapped in production —
# set it in the App Platform env vars, redeploy, and that account can open
# /web/admin.html. Accounts must already exist (sign up in the app first).
PLATFORM_ADMIN_EMAILS = [e.strip().lower() for e in
                         os.environ.get("PLATFORM_ADMIN_EMAILS", "").split(",")
                         if e.strip()]

# 'local' (dev: files under ./.filestore) or 's3' (DigitalOcean Spaces)
STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local")
STORAGE_DIR = os.environ.get("STORAGE_DIR", str(PROJECT_ROOT / ".filestore"))
SPACES_REGION = os.environ.get("SPACES_REGION", "nyc3")
SPACES_BUCKET = os.environ.get("SPACES_BUCKET", "")
SPACES_KEY = os.environ.get("SPACES_KEY", "")
SPACES_SECRET = os.environ.get("SPACES_SECRET", "")
