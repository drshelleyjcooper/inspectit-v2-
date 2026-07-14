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

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# DEV_MODE=1: password-reset tokens are returned in API responses instead of
# being emailed (no email provider is wired up yet). Never enable in production.
DEV_MODE = os.environ.get("DEV_MODE", "") == "1"


def _jwt_secret() -> str:
    env = os.environ.get("JWT_SECRET")
    if env:
        return env
    # Dev fallback: generate once and persist beside the repo so tokens
    # survive restarts. Production must set JWT_SECRET explicitly.
    f = PROJECT_ROOT / ".jwt_secret"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
    return f.read_text().strip()


JWT_SECRET = _jwt_secret()
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
