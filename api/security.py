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

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
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


def _request_meta() -> dict:
    from .requestmeta import request_meta
    return request_meta.get() or {}


def make_refresh_token(conn, user_id: str) -> str:
    jti = uuid.uuid4()
    expires = _now() + dt.timedelta(days=config.REFRESH_TOKEN_TTL_DAYS)
    meta = _request_meta()
    conn.execute(
        """INSERT INTO refresh_tokens (jti, user_id, expires_at, ip, user_agent,
                                       last_seen_at)
           VALUES (%s, %s, %s, %s, %s, now())""",
        (jti, user_id, expires, meta.get("ip"), meta.get("user_agent")),
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
        """UPDATE refresh_tokens SET revoked_at = now(), last_seen_at = now()
           WHERE jti = %s AND revoked_at IS NULL AND expires_at > now()
           RETURNING user_id""",
        (payload["jti"],),
    ).fetchone()
    if not row:
        raise ValueError("refresh token unknown, expired, or already used")
    user_id = row["user_id"]
    return make_access_token(user_id), make_refresh_token(conn, user_id)


def revoke_all_refresh_tokens(conn, user_id) -> int:
    """Kill every live refresh token for a user in one statement. Used by
    password reset and account suspension — anything that must force the
    user to re-authenticate everywhere, immediately. Returns count revoked."""
    rows = conn.execute(
        """UPDATE refresh_tokens SET revoked_at = now()
           WHERE user_id = %s AND revoked_at IS NULL
           RETURNING jti""",
        (user_id,),
    ).fetchall()
    return len(rows)


# ---------- password reset / invitations ----------

def new_url_token() -> str:
    return secrets.token_urlsafe(32)


def sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
