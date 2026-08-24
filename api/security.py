"""Password hashing (bcrypt) and JWT access/refresh tokens.

Refresh tokens are tracked server-side (refresh_tokens table) so they can be
rotated on use and revoked. Access tokens are short-lived and stateless.
"""
import datetime as dt
import hashlib
import secrets
import uuid

import bcrypt
import jwt

from . import config


# ---------- passwords ----------

# bcrypt only looks at the first 72 bytes. bcrypt < 5 truncated silently;
# bcrypt >= 5 (what requirements.txt installs) raises ValueError instead,
# which turned a long password into a 500 on signup and a 401 on login.
# Truncate explicitly so behaviour is stable across bcrypt versions.
_BCRYPT_MAX = 72


def _pw_bytes(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_pw_bytes(password), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(_pw_bytes(password), password_hash.encode())
    except ValueError:
        return False


# ---------- JWTs ----------

def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def make_access_token(user_id: str) -> str:
    payload = {
        "sub": str(user_id), "typ": "access",
        "exp": _now() + dt.timedelta(minutes=config.ACCESS_TOKEN_TTL_MIN),
        "iat": _now(),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm="HS256")


def make_refresh_token(conn, user_id: str) -> str:
    jti = uuid.uuid4()
    expires = _now() + dt.timedelta(days=config.REFRESH_TOKEN_TTL_DAYS)
    conn.execute(
        "INSERT INTO refresh_tokens (jti, user_id, expires_at) VALUES (%s, %s, %s)",
        (jti, user_id, expires),
    )
    payload = {"sub": str(user_id), "typ": "refresh", "jti": str(jti),
               "exp": expires, "iat": _now()}
    return jwt.encode(payload, config.JWT_SECRET, algorithm="HS256")


def decode_token(token: str, expected_type: str) -> dict:
    """Returns the payload or raises jwt exceptions / ValueError."""
    payload = jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
    if payload.get("typ") != expected_type:
        raise ValueError("wrong token type")
    return payload


def rotate_refresh_token(conn, token: str) -> tuple:
    """Validate a refresh token, revoke it, and issue a new pair.
    Returns (access, refresh) or raises ValueError."""
    payload = decode_token(token, "refresh")
    row = conn.execute(
        """UPDATE refresh_tokens SET revoked_at = now()
           WHERE jti = %s AND revoked_at IS NULL AND expires_at > now()
           RETURNING user_id""",
        (payload["jti"],),
    ).fetchone()
    if not row:
        raise ValueError("refresh token unknown, expired, or already used")
    user_id = row["user_id"]
    return make_access_token(user_id), make_refresh_token(conn, user_id)


# ---------- password reset / invitations ----------

def new_url_token() -> str:
    return secrets.token_urlsafe(32)


def sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
