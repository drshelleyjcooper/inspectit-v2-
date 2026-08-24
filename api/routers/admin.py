"""Platform admin portal API (first iteration).

Cross-tenant operator endpoints for the people running Inspectit — NOT for
company administrators (they use /companies/{id}/...). Every route requires
users.is_platform_admin (see permissions.platform_admin).

    GET   /admin/stats                    usage overview
    GET   /admin/companies                companies + member counts
    GET   /admin/users?q=&limit=&offset=  users + memberships (search by email/name)
    POST  /admin/users                    create user (+ membership, optional new company)
    PATCH /admin/users/{id}               name / disable / platform-admin flag
    POST  /admin/users/{id}/reset-password   set (or generate) a new password

Bootstrapping: PLATFORM_ADMIN_EMAILS env var -> promote_platform_admins()
runs at startup. The frontend is web/admin.html.
"""
import secrets
import string
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from .. import config, security
from ..db import audit, get_pool
from ..permissions import platform_admin

router = APIRouter(prefix="/admin", tags=["admin"],
                   dependencies=[Depends(platform_admin)])

PASSWORD_MIN = 8
DEFAULT_ROLE = "Company Administrator"


# ---------- bootstrap ----------

def promote_platform_admins(conn) -> list:
    """Flag every PLATFORM_ADMIN_EMAILS account as platform admin. Idempotent;
    called from the app lifespan. Returns the emails that were promoted."""
    if not config.PLATFORM_ADMIN_EMAILS:
        return []
    rows = conn.execute(
        """UPDATE users SET is_platform_admin = true
           WHERE lower(email) = ANY(%s) AND is_platform_admin = false
           RETURNING email""",
        (config.PLATFORM_ADMIN_EMAILS,),
    ).fetchall()
    return [r["email"] for r in rows]


# ---------- helpers ----------

def _generate_password(n: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def _uuid(value: str, what: str):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise HTTPException(404, f"{what} not found")


def _normalize_email(email: str) -> str:
    email = (email or "").strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(422, "Invalid email address")
    return email


def _user_out(row) -> dict:
    return {
        "id": str(row["id"]), "email": row["email"], "name": row["name"],
        "is_platform_admin": row["is_platform_admin"],
        "created_at": row["created_at"], "last_login_at": row.get("last_login_at"),
        "disabled_at": row.get("disabled_at"),
        "memberships": row.get("memberships") or [],
    }


_USER_SELECT = """
    SELECT u.id, u.email, u.name, u.is_platform_admin, u.created_at,
           u.last_login_at, u.disabled_at,
           COALESCE(json_agg(json_build_object(
                'company_id', c.id, 'company_name', c.name, 'status', m.status,
                'roles', (SELECT COALESCE(json_agg(r.name ORDER BY r.name), '[]')
                          FROM membership_roles mr JOIN roles r ON r.id = mr.role_id
                          WHERE mr.membership_id = m.id)))
             FILTER (WHERE m.id IS NOT NULL), '[]') AS memberships
    FROM users u
    LEFT JOIN memberships m ON m.user_id = u.id AND m.deleted_at IS NULL
    LEFT JOIN companies c ON c.id = m.company_id AND c.deleted_at IS NULL
"""


def _fetch_user(conn, user_id):
    return conn.execute(_USER_SELECT + " WHERE u.id = %s GROUP BY u.id",
                        (user_id,)).fetchone()


# ---------- stats ----------

@router.get("/stats")
def stats():
    with get_pool().connection() as conn:
        one = lambda sql, *args: conn.execute(sql, args).fetchone()  # noqa: E731
        totals = one("""
            SELECT
              (SELECT count(*) FROM users)                                  AS users,
              (SELECT count(*) FROM users WHERE disabled_at IS NOT NULL)    AS users_disabled,
              (SELECT count(*) FROM users WHERE is_platform_admin)          AS platform_admins,
              (SELECT count(*) FROM companies WHERE deleted_at IS NULL)     AS companies,
              (SELECT count(*) FROM memberships
                 WHERE deleted_at IS NULL AND status = 'active')            AS active_memberships,
              (SELECT count(*) FROM invitations WHERE status = 'pending')   AS pending_invitations,
              (SELECT count(*) FROM app_collections)                        AS synced_collections,
              (SELECT count(*) FROM files WHERE deleted_at IS NULL)         AS files,
              (SELECT COALESCE(sum(size_bytes), 0) FROM files
                 WHERE deleted_at IS NULL)                                  AS file_bytes,
              (SELECT count(*) FROM refresh_tokens
                 WHERE revoked_at IS NULL AND expires_at > now())           AS live_sessions
        """)
        activity = one("""
            SELECT
              (SELECT count(*) FROM users WHERE created_at > now() - interval '7 days')   AS signups_7d,
              (SELECT count(*) FROM users WHERE created_at > now() - interval '30 days')  AS signups_30d,
              (SELECT count(*) FROM users WHERE last_login_at > now() - interval '1 day') AS logins_24h,
              (SELECT count(*) FROM users WHERE last_login_at > now() - interval '7 days')  AS logins_7d,
              (SELECT count(*) FROM users WHERE last_login_at > now() - interval '30 days') AS logins_30d,
              (SELECT count(*) FROM app_collections
                 WHERE updated_at > now() - interval '1 day')                              AS syncs_24h,
              (SELECT count(*) FROM app_collections
                 WHERE updated_at > now() - interval '7 days')                             AS syncs_7d,
              (SELECT count(*) FROM audit_log WHERE at > now() - interval '7 days')        AS audit_events_7d
        """)
        signups_daily = conn.execute("""
            SELECT date_trunc('day', created_at)::date AS day, count(*) AS n
            FROM users WHERE created_at > now() - interval '30 days'
            GROUP BY 1 ORDER BY 1
        """).fetchall()
        top_companies = conn.execute("""
            SELECT c.id, c.name, c.created_at,
                   count(DISTINCT m.user_id) FILTER (WHERE m.deleted_at IS NULL) AS members,
                   (SELECT max(updated_at) FROM app_collections a
                     WHERE a.company_id = c.id) AS last_sync_at,
                   (SELECT count(*) FROM app_collections a
                     WHERE a.company_id = c.id) AS collections
            FROM companies c
            LEFT JOIN memberships m ON m.company_id = c.id
            WHERE c.deleted_at IS NULL
            GROUP BY c.id ORDER BY last_sync_at DESC NULLS LAST, c.created_at DESC
            LIMIT 10
        """).fetchall()
        recent_logins = conn.execute("""
            SELECT id, email, name, last_login_at FROM users
            WHERE last_login_at IS NOT NULL
            ORDER BY last_login_at DESC LIMIT 10
        """).fetchall()
    return {
        "totals": dict(totals),
        "activity": dict(activity),
        "signups_daily": [dict(r) for r in signups_daily],
        "top_companies": [{**r, "id": str(r["id"])} for r in top_companies],
        "recent_logins": [{**r, "id": str(r["id"])} for r in recent_logins],
    }


# ---------- companies ----------

@router.get("/companies")
def list_companies(q: Optional[str] = None,
                   limit: int = Query(200, ge=1, le=500),
                   offset: int = Query(0, ge=0)):
    with get_pool().connection() as conn:
        rows = conn.execute("""
            SELECT c.id, c.name, c.created_at,
                   count(m.id) FILTER (WHERE m.deleted_at IS NULL AND m.status = 'active') AS members
            FROM companies c
            LEFT JOIN memberships m ON m.company_id = c.id
            WHERE c.deleted_at IS NULL AND c.name ILIKE %s
            GROUP BY c.id ORDER BY c.name LIMIT %s OFFSET %s
        """, (f"%{q}%" if q else "%", limit, offset)).fetchall()
    return [{**r, "id": str(r["id"])} for r in rows]


# ---------- users ----------

@router.get("/users")
def list_users(response: Response, q: Optional[str] = None,
               company_id: Optional[str] = None,
               limit: int = Query(100, ge=1, le=500),
               offset: int = Query(0, ge=0)):
    where = ["true"]
    params: List = []
    if q:
        where.append("(u.email ILIKE %s OR u.name ILIKE %s)")
        params += [f"%{q}%", f"%{q}%"]
    if company_id:
        where.append("""u.id IN (SELECT user_id FROM memberships
                                  WHERE company_id = %s AND deleted_at IS NULL)""")
        params.append(str(_uuid(company_id, "Company")))
    with get_pool().connection() as conn:
        total = conn.execute("SELECT count(*) AS n FROM users u WHERE " + " AND ".join(where),
                             params).fetchone()["n"]
        rows = conn.execute(
            _USER_SELECT + " WHERE " + " AND ".join(where)
            + " GROUP BY u.id ORDER BY u.created_at DESC LIMIT %s OFFSET %s",
            params + [limit, offset]).fetchall()
    response.headers["X-Total-Count"] = str(total)
    return [_user_out(r) for r in rows]


class CreateUserIn(BaseModel):
    email: str = Field(min_length=5, max_length=320)
    name: str = Field(min_length=1, max_length=200)
    password: Optional[str] = Field(default=None, min_length=PASSWORD_MIN, max_length=200)
    # Attach to an existing company, or create a new one. If neither is
    # given the user is created with no membership (they can be invited later).
    company_id: Optional[str] = None
    company_name: Optional[str] = Field(default=None, max_length=200)
    role: str = DEFAULT_ROLE          # preset role name for the membership
    is_platform_admin: bool = False


@router.post("/users", status_code=201)
def create_user(body: CreateUserIn, admin: dict = Depends(platform_admin)):
    email = _normalize_email(body.email)
    if body.company_id and body.company_name:
        raise HTTPException(422, "Give company_id OR company_name, not both")
    password = body.password or _generate_password()
    with get_pool().connection() as conn:
        if conn.execute("SELECT 1 FROM users WHERE email = %s", (email,)).fetchone():
            raise HTTPException(409, "An account with this email already exists")
        user = conn.execute(
            """INSERT INTO users (email, password_hash, name, is_platform_admin)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (email, security.hash_password(password), body.name.strip(),
             body.is_platform_admin),
        ).fetchone()

        company_id = None
        if body.company_name and body.company_name.strip():
            company_id = conn.execute(
                "INSERT INTO companies (name) VALUES (%s) RETURNING id",
                (body.company_name.strip(),)).fetchone()["id"]
        elif body.company_id:
            cid = _uuid(body.company_id, "Company")
            if not conn.execute(
                    "SELECT 1 FROM companies WHERE id = %s AND deleted_at IS NULL",
                    (cid,)).fetchone():
                raise HTTPException(404, "Company not found")
            company_id = cid

        if company_id:
            role = conn.execute(
                """SELECT id FROM roles
                   WHERE name = %s AND deleted_at IS NULL
                     AND (company_id IS NULL OR company_id = %s)
                   ORDER BY company_id NULLS LAST LIMIT 1""",
                (body.role, company_id)).fetchone()
            if not role:
                raise HTTPException(422, f"Unknown role '{body.role}'")
            membership = conn.execute(
                "INSERT INTO memberships (company_id, user_id) VALUES (%s, %s) RETURNING id",
                (company_id, user["id"])).fetchone()
            conn.execute(
                "INSERT INTO membership_roles (membership_id, role_id) VALUES (%s, %s)",
                (membership["id"], role["id"]))
            audit(conn, company_id, admin["id"], "create", "membership",
                  membership["id"], {"event": "admin_create_user",
                                     "user_id": str(user["id"]), "role": body.role})
        out = _user_out(_fetch_user(conn, user["id"]))
    # The password is shown ONCE so the operator can hand it to the person.
    out["password"] = password
    out["password_generated"] = body.password is None
    return out


class UpdateUserIn(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    disabled: Optional[bool] = None
    is_platform_admin: Optional[bool] = None


@router.patch("/users/{user_id}")
def update_user(user_id: str, body: UpdateUserIn, admin: dict = Depends(platform_admin)):
    uid = _uuid(user_id, "User")
    with get_pool().connection() as conn:
        if not conn.execute("SELECT 1 FROM users WHERE id = %s", (uid,)).fetchone():
            raise HTTPException(404, "User not found")
        if body.name is not None:
            conn.execute("UPDATE users SET name = %s WHERE id = %s",
                         (body.name.strip(), uid))
        if body.is_platform_admin is not None:
            if uid == admin["id"] and not body.is_platform_admin:
                raise HTTPException(422, "You can't remove your own platform-admin flag")
            conn.execute("UPDATE users SET is_platform_admin = %s WHERE id = %s",
                         (body.is_platform_admin, uid))
        if body.disabled is not None:
            if uid == admin["id"] and body.disabled:
                raise HTTPException(422, "You can't disable your own account")
            if body.disabled:
                conn.execute("UPDATE users SET disabled_at = now() WHERE id = %s", (uid,))
                conn.execute("""UPDATE refresh_tokens SET revoked_at = now()
                                WHERE user_id = %s AND revoked_at IS NULL""", (uid,))
            else:
                conn.execute("UPDATE users SET disabled_at = NULL WHERE id = %s", (uid,))
        return _user_out(_fetch_user(conn, uid))


class ResetPasswordIn(BaseModel):
    password: Optional[str] = Field(default=None, min_length=PASSWORD_MIN, max_length=200)


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: str, body: ResetPasswordIn = None,
                   admin: dict = Depends(platform_admin)):
    """Set a new password for the user (generated if not supplied) and sign
    them out everywhere. The new password is returned ONCE for the operator
    to pass on — this stands in for emailed reset links until a mailer exists."""
    uid = _uuid(user_id, "User")
    password = (body.password if body and body.password else None) or _generate_password()
    with get_pool().connection() as conn:
        row = conn.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s RETURNING email",
            (security.hash_password(password), uid)).fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        conn.execute("""UPDATE refresh_tokens SET revoked_at = now()
                        WHERE user_id = %s AND revoked_at IS NULL""", (uid,))
        conn.execute("""UPDATE password_resets SET used_at = now()
                        WHERE user_id = %s AND used_at IS NULL""", (uid,))
        # Audit against each company the user belongs to (audit_log needs one).
        for m in conn.execute(
                "SELECT company_id FROM memberships WHERE user_id = %s AND deleted_at IS NULL",
                (uid,)).fetchall():
            audit(conn, m["company_id"], admin["id"], "update", "user", uid,
                  {"event": "admin_password_reset"})
    return {"ok": True, "user_id": str(uid), "email": row["email"],
            "password": password,
            "password_generated": not (body and body.password)}
