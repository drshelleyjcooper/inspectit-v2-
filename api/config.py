"""Environment-driven configuration.

Local dev needs no env vars at all: an embedded PostgreSQL (pgserver) is booted
automatically and files go to ./.filestore. In production on DigitalOcean App
Platform, set DATABASE_URL (Managed Postgres), JWT_SECRET, and the SPACES_*
variables.
"""
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


check_production_config(IS_PRODUCTION, os.environ.get("JWT_SECRET"),
                        DEV_MODE, ALLOWED_ORIGINS)


def _jwt_secret() -> str:
    env = os.environ.get("JWT_SECRET")
    if env:
        return env
    # Dev-only fallback: generate once and persist beside the repo so tokens
    # survive restarts. Production is gated above and never reaches this.
    f = PROJECT_ROOT / ".jwt_secret"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
    return f.read_text().strip()


JWT_SECRET = _jwt_secret()

# Auth-route rate limiting (per client IP): max requests per window.
AUTH_RATE_LIMIT = int(os.environ.get("AUTH_RATE_LIMIT", "10"))
AUTH_RATE_WINDOW_S = int(os.environ.get("AUTH_RATE_WINDOW_S", "60"))

# Global request-body ceiling (F4). Decided 2026-07-16: 75 MB.
MAX_BODY_MB = int(os.environ.get("MAX_BODY_MB", "75"))

# Connection pool sizing (F7): DO basic Managed Postgres allows ~22
# connections; stay well under it in production.
POOL_MIN = int(os.environ.get("POOL_MIN", "1"))
POOL_MAX = int(os.environ.get("POOL_MAX", "5" if IS_PRODUCTION else "10"))
ACCESS_TOKEN_TTL_MIN = int(os.environ.get("ACCESS_TOKEN_TTL_MIN", "30"))
REFRESH_TOKEN_TTL_DAYS = int(os.environ.get("REFRESH_TOKEN_TTL_DAYS", "30"))
RESET_TOKEN_TTL_MIN = int(os.environ.get("RESET_TOKEN_TTL_MIN", "60"))
INVITE_TTL_DAYS = int(os.environ.get("INVITE_TTL_DAYS", "14"))

# 'local' (dev: files under ./.filestore) or 's3' (DigitalOcean Spaces)
STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local")
STORAGE_DIR = os.environ.get("STORAGE_DIR", str(PROJECT_ROOT / ".filestore"))
SPACES_REGION = os.environ.get("SPACES_REGION", "nyc3")
SPACES_BUCKET = os.environ.get("SPACES_BUCKET", "")
SPACES_KEY = os.environ.get("SPACES_KEY", "")
SPACES_SECRET = os.environ.get("SPACES_SECRET", "")
