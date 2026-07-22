"""Auth: signup (creates a company + admin), login, refresh, password reset,
and invitation acceptance."""
import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import config, security
from ..db import audit, cleanup_user_tokens, get_pool
from ..ratelimit import rate_limit_auth

# Every /auth route is rate-limited per client IP (F3): these are the only
# endpoints an attacker can hammer without a valid token.
router = APIRouter(prefix="/auth", tags=["auth"],
                   dependencies=[Depends(rate_limit_auth)])

EMAIL_MIN = 5  # light validation; real email verification comes with the mailer
PASSWORD_MIN = 8


class SignupIn(BaseModel):
    company_name: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=EMAIL_MIN, max_length=320)
    password: str = Field(min_length=PASSWORD_MIN, max_length=200)


class LoginIn(BaseModel):
    email: str
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class ForgotIn(BaseModel):
    email: str


class ResetIn(BaseModel):
    token: str
    password: str = Field(min_length=PASSWORD_MIN, max_length=200)


class AcceptInviteIn(BaseModel):
    token: str
    name: Optional[str] = None
    password: Optional[str] = None


def _normalize_email(email: str) -> str:
    email = email.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(422, "Invalid email address")
    return email


def _token_pair(conn, user_id) -> dict:
    return {
        "access_token": security.make_access_token(user_id),
        "refresh_token": security.make_refresh_token(conn, user_id),
        "token_type": "bearer",
    }


@router.post("/signup")
def signup(body: SignupIn):
    """Self-serve: creates the user, their company, and grants the
    Company Administrator preset role."""
    email = _normalize_email(body.email)
    with get_pool().connection() as conn:
        if conn.execute("SELECT 1 FROM users WHERE email = %s", (email,)).fetchone():
            raise HTTPException(409, "An account with this email already exists")
        user = conn.execute(
            """INSERT INTO users (email, password_hash, name)
               VALUES (%s, %s, %s) RETURNING id""",
            (email, security.hash_password(body.password), body.name.strip()),
        ).fetchone()
        company = conn.execute(
            "INSERT INTO companies (name) VALUES (%s) RETURNING id",
            (body.company_name.strip(),),
        ).fetchone()
        membership = conn.execute(
            """INSERT INTO memberships (company_id, user_id)
               VALUES (%s, %s) RETURNING id""",
            (company["id"], user["id"]),
        ).fetchone()
        admin_role = conn.execute(
            """SELECT id FROM roles
               WHERE company_id IS NULL AND name = 'Company Administrator'""",
        ).fetchone()
        conn.execute(
            "INSERT INTO membership_roles (membership_id, role_id) VALUES (%s, %s)",
            (membership["id"], admin_role["id"]),
        )
        audit(conn, company["id"], user["id"], "create", "company", company["id"],
              {"event": "signup"})
        return {"company_id": str(company["id"]), "user_id": str(user["id"]),
                **_token_pair(conn, user["id"])}


@router.post("/login")
def login(body: LoginIn):
    email = _normalize_email(body.email)
    with get_pool().connection() as conn:
        user = conn.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
        if not user or not user["password_hash"] or \
                not security.verify_password(body.password, user["password_hash"]):
            raise HTTPException(401, "Invalid email or password")
        cleanup_user_tokens(conn, user["id"])   # F9: opportunistic sweep
        return {"user_id": str(user["id"]), **_token_pair(conn, user["id"])}


@router.post("/refresh")
def refresh(body: RefreshIn):
    with get_pool().connection() as conn:
        try:
            access, new_refresh = security.rotate_refresh_token(conn, body.refresh_token)
        except Exception:
            raise HTTPException(401, "Invalid refresh token")
        return {"access_token": access, "refresh_token": new_refresh,
                "token_type": "bearer"}


@router.post("/forgot")
def forgot(body: ForgotIn):
    """Always returns 200 (no account-existence oracle). In DEV_MODE the reset
    token is returned directly; production will email it instead."""
    try:
        email = _normalize_email(body.email)
    except HTTPException:
        return {"ok": True}
    with get_pool().connection() as conn:
        user = conn.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()
        if not user:
            return {"ok": True}
        token = security.new_url_token()
        conn.execute(
            """INSERT INTO password_resets (token_hash, user_id, expires_at)
               VALUES (%s, %s, %s)""",
            (security.sha256(token), user["id"],
             dt.datetime.now(dt.timezone.utc)
             + dt.timedelta(minutes=config.RESET_TOKEN_TTL_MIN)),
        )
    out = {"ok": True}
    if config.DEV_MODE:
        out["dev_reset_token"] = token
    return out


@router.post("/reset")
def reset(body: ResetIn):
    with get_pool().connection() as conn:
        row = conn.execute(
            """UPDATE password_resets SET used_at = now()
               WHERE token_hash = %s AND used_at IS NULL AND expires_at > now()
               RETURNING user_id""",
            (security.sha256(body.token),),
        ).fetchone()
        if not row:
            raise HTTPException(400, "Reset link is invalid or expired")
        conn.execute("UPDATE users SET password_hash = %s WHERE id = %s",
                     (security.hash_password(body.password), row["user_id"]))
        # Force re-login everywhere after a password change.
        security.revoke_all_refresh_tokens(conn, row["user_id"])
    return {"ok": True}


@router.post("/invitations/accept")
def accept_invitation(body: AcceptInviteIn):
    """Join a company from an invite token. New users must supply name+password;
    an existing user (same email) must supply their current password."""
    with get_pool().connection() as conn:
        inv = conn.execute(
            """SELECT * FROM invitations
               WHERE token = %s AND status = 'pending' AND expires_at > now()""",
            (body.token,),
        ).fetchone()
        if not inv:
            raise HTTPException(400, "Invitation is invalid or expired")

        user = conn.execute("SELECT * FROM users WHERE email = %s",
                            (inv["email"],)).fetchone()
        if user:
            if not body.password or not security.verify_password(
                    body.password, user["password_hash"] or ""):
                raise HTTPException(401,
                    "An account with this email exists — confirm its password")
        else:
            if not body.name or not body.password:
                raise HTTPException(422, "name and password are required")
            if len(body.password) < PASSWORD_MIN:
                raise HTTPException(422, "Password must be at least 8 characters")
            user = conn.execute(
                """INSERT INTO users (email, password_hash, name)
                   VALUES (%s, %s, %s) RETURNING *""",
                (inv["email"], security.hash_password(body.password),
                 body.name.strip()),
            ).fetchone()

        membership = conn.execute(
            """INSERT INTO memberships (company_id, user_id) VALUES (%s, %s)
               ON CONFLICT (company_id, user_id) DO UPDATE SET status = 'active'
               RETURNING id""",
            (inv["company_id"], user["id"]),
        ).fetchone()
        for role_id in inv["role_ids"]:
            conn.execute(
                """INSERT INTO membership_roles (membership_id, role_id)
                   VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                (membership["id"], role_id))
        conn.execute("UPDATE invitations SET status = 'accepted' WHERE id = %s",
                     (inv["id"],))
        audit(conn, inv["company_id"], user["id"], "create", "membership",
              membership["id"], {"event": "invitation_accepted"})
        return {"company_id": str(inv["company_id"]), "user_id": str(user["id"]),
                **_token_pair(conn, user["id"])}
