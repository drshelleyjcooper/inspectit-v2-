"""Auth: signup (creates a company + admin), login, refresh, password reset,
and invitation acceptance."""
import datetime as dt
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

from .. import config, mailer, security
from ..db import audit, cleanup_user_tokens, get_pool
from ..presets import BLOCKED_COMBINATIONS
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


class TrialSignupIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=EMAIL_MIN, max_length=320)
    password: str = Field(min_length=PASSWORD_MIN, max_length=200)
    track: Optional[Literal["vehicles", "properties", "both"]] = None
    who: Optional[Literal["me", "org"]] = None
    org: Optional[str] = Field(default=None, max_length=200)
    size: Optional[Literal["1-5", "6-25", "26-100", "100+"]] = None


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


def _create_account(conn, *, company_name, name, email, password,
                    trial_days=None, signup_info=None) -> dict:
    """Create user + company + Company Administrator membership. Shared by
    /auth/signup and /auth/trial-signup. Raises 409 if the email is taken."""
    if conn.execute("SELECT 1 FROM users WHERE email = %s", (email,)).fetchone():
        raise HTTPException(409, "An account with this email already exists")
    user = conn.execute(
        """INSERT INTO users (email, password_hash, name)
           VALUES (%s, %s, %s) RETURNING id""",
        (email, security.hash_password(password), name.strip()),
    ).fetchone()
    trial_ends = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=trial_days)
                  if trial_days else None)
    company = conn.execute(
        """INSERT INTO companies (name, trial_ends_at, signup_info)
           VALUES (%s, %s, %s) RETURNING id""",
        (company_name.strip(), trial_ends,
         Jsonb(signup_info) if signup_info else None),
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
    event = {"event": "trial_signup" if trial_days else "signup"}
    if signup_info:
        event["info"] = signup_info
    audit(conn, company["id"], user["id"], "create", "company", company["id"],
          event)
    out = {"company_id": str(company["id"]), "user_id": str(user["id"]),
           **_token_pair(conn, user["id"])}
    if trial_ends:
        out["trial_ends_at"] = trial_ends.isoformat()
    return out


@router.post("/signup")
def signup(body: SignupIn):
    """Self-serve: creates the user, their company, and grants the
    Company Administrator preset role."""
    email = _normalize_email(body.email)
    with get_pool().connection() as conn:
        return _create_account(conn, company_name=body.company_name,
                               name=body.name, email=email,
                               password=body.password)


@router.post("/trial-signup")
def trial_signup(body: TrialSignupIn, background: BackgroundTasks):
    """Website free-trial form. Same account shape as /auth/signup, plus a
    30-day trial end date and the form's optional answers. Individuals have no
    company name, so they get '<Name>'s account'."""
    email = _normalize_email(body.email)
    org = (body.org or "").strip()
    name = body.name.strip()
    company_name = org if (body.who == "org" and org) else f"{name}'s account"
    info = {k: v for k, v in {"track": body.track, "who": body.who,
                              "size": body.size}.items() if v}
    with get_pool().connection() as conn:
        try:
            out = _create_account(conn, company_name=company_name, name=name,
                                  email=email, password=body.password,
                                  trial_days=config.TRIAL_DAYS,
                                  signup_info=info or None)
        except HTTPException as exc:
            if exc.status_code == 409:
                raise HTTPException(
                    409, "That email already has an account. Sign in instead?")
            raise
    subject, text = mailer.trial_signup_email(name, email, company_name, info,
                                              out["trial_ends_at"][:10])
    background.add_task(mailer.send_mail, subject, text, email)
    return out


@router.post("/login")
def login(body: LoginIn):
    email = _normalize_email(body.email)
    with get_pool().connection() as conn:
        user = conn.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
        if not user or not user["password_hash"] or \
                not security.verify_password(body.password, user["password_hash"]):
            raise HTTPException(401, "Invalid email or password")
        if user.get("disabled_at"):
            raise HTTPException(403, "This account has been disabled")
        cleanup_user_tokens(conn, user["id"])   # F9: opportunistic sweep
        conn.execute("UPDATE users SET last_login_at = now() WHERE id = %s",
                     (user["id"],))
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


def issue_password_token(conn, user_id, minutes: int, purpose: str = "reset") -> str:
    """Create a single-use set-password token; returns the raw token (only its
    hash is stored). Used by /forgot and by the admin portal's emailed links."""
    token = security.new_url_token()
    conn.execute(
        """INSERT INTO password_resets (token_hash, user_id, expires_at, purpose)
           VALUES (%s, %s, %s, %s)""",
        (security.sha256(token), user_id,
         dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=minutes), purpose))
    return token


@router.post("/forgot")
def forgot(body: ForgotIn, background: BackgroundTasks):
    """Always returns 200 (no account-existence oracle). The reset link is
    emailed to the account's address in a background task; in DEV_MODE the
    token is also returned directly so tests and local work need no mailbox."""
    try:
        email = _normalize_email(body.email)
    except HTTPException:
        return {"ok": True}
    with get_pool().connection() as conn:
        user = conn.execute(
            "SELECT id, name, disabled_at FROM users WHERE email = %s",
            (email,)).fetchone()
        if not user or user.get("disabled_at"):
            return {"ok": True}
        token = security.new_url_token()
        conn.execute(
            """INSERT INTO password_resets (token_hash, user_id, expires_at)
               VALUES (%s, %s, %s)""",
            (security.sha256(token), user["id"],
             dt.datetime.now(dt.timezone.utc)
             + dt.timedelta(minutes=config.RESET_TOKEN_TTL_MIN)),
        )
    link = f"{config.APP_BASE_URL}/reset-password?token={token}"
    subject, text = mailer.password_reset_email(user["name"], link,
                                                config.RESET_TOKEN_TTL_MIN)
    background.add_task(mailer.send_mail, subject, text, None, email)
    out = {"ok": True}
    if config.DEV_MODE:
        out["dev_reset_token"] = token
    return out


@router.post("/reset")
def reset(body: ResetIn, background: BackgroundTasks):
    with get_pool().connection() as conn:
        row = conn.execute(
            """UPDATE password_resets SET used_at = now()
               WHERE token_hash = %s AND used_at IS NULL AND expires_at > now()
               RETURNING user_id, purpose""",
            (security.sha256(body.token),),
        ).fetchone()
        if not row:
            raise HTTPException(400, "Reset link is invalid or expired")
        conn.execute("UPDATE users SET password_hash = %s WHERE id = %s",
                     (security.hash_password(body.password), row["user_id"]))
        # Force re-login everywhere after a password change.
        conn.execute(
            """UPDATE refresh_tokens SET revoked_at = now()
               WHERE user_id = %s AND revoked_at IS NULL""",
            (row["user_id"],))
        user = conn.execute("SELECT email, name FROM users WHERE id = %s",
                            (row["user_id"],)).fetchone()
    if user and row["purpose"] != "welcome":
        subject, text = mailer.password_changed_email(
            user["name"], f"{config.APP_BASE_URL}/web/inspectit-app.html")
        background.add_task(mailer.send_mail, subject, text, None, user["email"])
    return {"ok": True}


def _issuer_holds_company_admin(conn, company_id, user_id) -> bool:
    """Does the invitation's issuer hold company:admin in this company now?

    The §2.3 block exists to stop a DOMAIN manager assembling inspector +
    maintenance; Company Administrator and Manager may issue the pair
    deliberately (§4.2) and the invite route exempts them. The acceptance
    re-check must honour the same exemption or every such invitation dies at
    accept — which it did until 2026-09-09 (spec §11). Checked against the
    issuer's CURRENT roles: if they were demoted between issue and accept the
    exemption goes with them."""
    row = conn.execute(
        """SELECT 1 FROM memberships m
           JOIN membership_roles mr ON mr.membership_id = m.id
           JOIN roles r ON r.id = mr.role_id AND r.deleted_at IS NULL
           WHERE m.company_id = %s AND m.user_id = %s
             AND m.status = 'active' AND m.deleted_at IS NULL
             AND r.permissions->'company' ? 'admin'
           LIMIT 1""",
        (company_id, user_id),
    ).fetchone()
    return row is not None


def _reject_blocked_combination(conn, inv, user):
    """Acceptance-time §2.3 re-check. Raises 409 and revokes on a hit."""
    # Re-check the blocked combination here, not just at invite time: the
    # membership set can change between issue and accept, and acceptance is
    # the moment the roles actually land (§2.3). Revoke rather than leave
    # it pending — a token that can never be accepted is worse than no
    # token, because the invitee would retry forever.
    held = conn.execute(
        """SELECT r.name FROM memberships m
           JOIN membership_roles mr ON mr.membership_id = m.id
           JOIN roles r ON r.id = mr.role_id AND r.deleted_at IS NULL
           WHERE m.company_id = %s AND m.user_id = %s
             AND m.deleted_at IS NULL""",
        (inv["company_id"], user["id"]),
    ).fetchall()
    incoming = conn.execute(
        "SELECT name FROM roles WHERE id = ANY(%s::uuid[])",
        (list(inv["role_ids"]),),
    ).fetchall()
    resulting = {r["name"] for r in held} | {r["name"] for r in incoming}
    for pair in BLOCKED_COMBINATIONS:
        if pair <= resulting:
            conn.execute(
                "UPDATE invitations SET status = 'revoked' WHERE id = %s",
                (inv["id"],))
            # Commit before raising: the pool rolls back on an exception, and
            # until 2026-09-09 that silently undid this revoke — the token
            # stayed pending and every retry got the same 409 (spec §11).
            conn.commit()
            raise HTTPException(
                409, "Holding " + " and ".join(sorted(pair)) + " together "
                     "needs administrator approval. This invitation has "
                     "been cancelled — ask an administrator.")


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

        if not _issuer_holds_company_admin(conn, inv["company_id"],
                                           inv["invited_by"]):
            _reject_blocked_combination(conn, inv, user)

        membership = conn.execute(
            """INSERT INTO memberships (company_id, user_id) VALUES (%s, %s)
               ON CONFLICT (company_id, user_id) DO UPDATE SET status = 'active',deleted_at = NULL
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
        return {"company_id": str(inv["company_id"]), "user_id": str(user["id"]), "email": inv["email"],
                **_token_pair(conn, user["id"])}
