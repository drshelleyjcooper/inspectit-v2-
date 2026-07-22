"""Company membership management: roles listing, members, invitations."""
import datetime as dt
import uuid
from typing import List

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from .. import config, security
from ..db import audit, get_pool, reactivate_membership, suspend_membership
from ..permissions import (AuthContext, company_member, require,
                           resolve_permissions)

router = APIRouter(prefix="/companies/{company_id}", tags=["members"])


def _admin_membership_ids(conn, company_id) -> set:
    """Active memberships whose role union grants company:delete — the one
    action this pass otherwise leaves unused (Stage 4/delete), chosen as the
    'must never hit zero' permission because it's exactly what the deferred
    DELETE endpoint will require. Permission-based, not name-based, so a
    custom role cloning Company Administrator's grants still counts."""
    rows = conn.execute(
        """SELECT m.id,
                  COALESCE(json_agg(r.permissions) FILTER (WHERE r.id IS NOT NULL),
                           '[]') AS perms
           FROM memberships m
           LEFT JOIN membership_roles mr ON mr.membership_id = m.id
           LEFT JOIN roles r ON r.id = mr.role_id AND r.deleted_at IS NULL
           WHERE m.company_id = %s AND m.status = 'active' AND m.deleted_at IS NULL
           GROUP BY m.id""",
        (company_id,),
    ).fetchall()
    admins = set()
    for row in rows:
        for perm in row["perms"]:
            if "delete" in perm.get("company", []):
                admins.add(str(row["id"]))
                break
    return admins


def _get_membership(conn, company_id, user_id):
    """Company-scoped lookup: a uid belonging to another company is a plain
    404 here, not a cross-tenant guard — see docs/admin-users-api.md."""
    return conn.execute(
        """SELECT id, user_id, status FROM memberships
           WHERE company_id = %s AND user_id = %s AND deleted_at IS NULL""",
        (company_id, user_id),
    ).fetchone()


def _member_roles(conn, membership_id):
    return conn.execute(
        """SELECT r.id, r.name, r.scope, r.permissions
           FROM membership_roles mr JOIN roles r ON r.id = mr.role_id
           WHERE mr.membership_id = %s AND r.deleted_at IS NULL""",
        (membership_id,),
    ).fetchall()


@router.get("/roles")
def list_roles(ctx: AuthContext = Depends(company_member)):
    """Built-in presets + this company's custom roles. Any member may look."""
    with get_pool().connection() as conn:
        rows = conn.execute(
            """SELECT id, name, scope, permissions, is_preset FROM roles
               WHERE (company_id IS NULL OR company_id = %s) AND deleted_at IS NULL
               ORDER BY is_preset DESC, name""",
            (ctx.company_id,),
        ).fetchall()
    return [{**r, "id": str(r["id"])} for r in rows]


@router.get("/members")
def list_members(response: Response,
                 status: Optional[str] = None,
                 role: Optional[str] = None,
                 q: Optional[str] = None,
                 limit: int = Query(50, ge=1, le=500),
                 offset: int = Query(0, ge=0),
                 ctx: AuthContext = Depends(company_member)):
    """Members with their roles. Needs company:view (admin) or any assign
    permission (managers must see who they can assign) — unchanged from
    before this pass; suspend-status visibility isn't sensitive enough to
    warrant tightening an already-shipped visibility rule."""
    can = ctx.grant_scope("company", "view") or any(
        ctx.grant_scope(m, "assign")
        for m in ("vehicles", "properties", "projects"))
    if not can:
        raise HTTPException(403, "Requires company:view or an assign permission")
    if status and status not in ("active", "suspended"):
        raise HTTPException(422, "status must be 'active' or 'suspended'")
    if role:
        try:
            uuid.UUID(role)
        except ValueError:
            raise HTTPException(422, "role must be a UUID")

    where = "m.company_id = %s AND m.deleted_at IS NULL"
    params = [ctx.company_id]
    if status:
        where += " AND m.status = %s"
        params.append(status)
    if q:
        where += " AND (u.name ILIKE %s OR u.email ILIKE %s)"
        like = f"%{q}%"
        params += [like, like]
    having = ""
    having_params = []
    if role:
        having = "HAVING bool_or(r.id = %s)"
        having_params = [role]

    with get_pool().connection() as conn:
        total = conn.execute(
            f"""SELECT count(*) AS n FROM (
                    SELECT m.id
                    FROM memberships m
                    JOIN users u ON u.id = m.user_id
                    LEFT JOIN membership_roles mr ON mr.membership_id = m.id
                    LEFT JOIN roles r ON r.id = mr.role_id AND r.deleted_at IS NULL
                    WHERE {where}
                    GROUP BY m.id
                    {having}
                ) sub""",
            params + having_params,
        ).fetchone()["n"]
        rows = conn.execute(
            f"""SELECT m.id AS membership_id, m.status, m.created_at AS joined_at,
                      u.id AS user_id, u.name, u.email,
                      (SELECT max(created_at) FROM refresh_tokens
                        WHERE user_id = u.id) AS last_login,
                      COALESCE(json_agg(json_build_object('id', r.id, 'name', r.name))
                               FILTER (WHERE r.id IS NOT NULL), '[]') AS roles
               FROM memberships m
               JOIN users u ON u.id = m.user_id
               LEFT JOIN membership_roles mr ON mr.membership_id = m.id
               LEFT JOIN roles r ON r.id = mr.role_id AND r.deleted_at IS NULL
               WHERE {where}
               GROUP BY m.id, m.status, m.created_at, u.id, u.name, u.email
               {having}
               ORDER BY u.name LIMIT %s OFFSET %s""",
            params + having_params + [limit, offset],
        ).fetchall()
    response.headers["X-Total-Count"] = str(total)
    return [{**r, "membership_id": str(r["membership_id"]),
             "user_id": str(r["user_id"])} for r in rows]


@router.get("/members/{user_id}")
def get_member(user_id: str, ctx: AuthContext = Depends(require("company", "view"))):
    """Profile + roles + resolved permissions + sessions + activity. Gated
    at company:view (same sensitivity as /audit) — this carries suspend
    reason and IP-bearing session data, more sensitive than the roster."""
    try:
        uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(404, "Member not found")
    with get_pool().connection() as conn:
        m = conn.execute(
            """SELECT m.id, m.status, m.suspended_at, m.suspended_by,
                      m.suspend_reason, u.id AS user_id, u.name, u.email
               FROM memberships m JOIN users u ON u.id = m.user_id
               WHERE m.company_id = %s AND m.user_id = %s AND m.deleted_at IS NULL""",
            (ctx.company_id, user_id),
        ).fetchone()
        if not m:
            raise HTTPException(404, "Member not found")
        roles = _member_roles(conn, m["id"])
        last_login = conn.execute(
            "SELECT max(created_at) AS t FROM refresh_tokens WHERE user_id = %s",
            (user_id,)).fetchone()["t"]
        last_data_activity = conn.execute(
            """SELECT max(at) AS t FROM audit_log
               WHERE user_id = %s AND company_id = %s""",
            (user_id, ctx.company_id)).fetchone()["t"]
        sessions = conn.execute(
            """SELECT jti, created_at, expires_at, revoked_at, ip, user_agent,
                      last_seen_at
               FROM refresh_tokens WHERE user_id = %s
               ORDER BY created_at DESC LIMIT 50""",
            (user_id,)).fetchall()
    now = dt.datetime.now(dt.timezone.utc)
    return {
        "user_id": str(m["user_id"]), "name": m["name"], "email": m["email"],
        "status": m["status"],
        "suspended_at": m["suspended_at"],
        "suspended_by": str(m["suspended_by"]) if m["suspended_by"] else None,
        "suspend_reason": m["suspend_reason"],
        "roles": [{"id": str(r["id"]), "name": r["name"], "scope": r["scope"]}
                 for r in roles],
        "permissions": resolve_permissions(roles),
        "last_login": last_login,
        "last_data_activity": last_data_activity,
        "sessions": [{
            "jti": str(s["jti"]), "created_at": s["created_at"],
            "expires_at": s["expires_at"], "ip": s["ip"],
            "user_agent": s["user_agent"], "last_seen_at": s["last_seen_at"],
            "revoked_at": s["revoked_at"],
            "active": s["revoked_at"] is None and s["expires_at"] > now,
        } for s in sessions],
    }


class RoleDiffIn(BaseModel):
    add: List[str] = []
    remove: List[str] = []


@router.patch("/members/{user_id}/roles")
def patch_member_roles(user_id: str, body: RoleDiffIn,
                       ctx: AuthContext = Depends(require("company", "assign"))):
    try:
        uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(404, "Member not found")
    with get_pool().connection() as conn:
        m = _get_membership(conn, ctx.company_id, user_id)
        if not m:
            raise HTTPException(404, "Member not found")

        for rid in set(body.add) | set(body.remove):
            ok = conn.execute(
                """SELECT 1 FROM roles
                   WHERE id = %s AND (company_id IS NULL OR company_id = %s)
                     AND deleted_at IS NULL""",
                (rid, ctx.company_id)).fetchone()
            if not ok:
                raise HTTPException(422, f"Unknown role: {rid}")

        roles_before = _member_roles(conn, m["id"])
        before_ids = {str(r["id"]) for r in roles_before}
        after_ids = (before_ids - set(body.remove)) | set(body.add)
        before_grants_delete = any(
            "delete" in r["permissions"].get("company", []) for r in roles_before)
        after_roles_rows = conn.execute(
            "SELECT permissions FROM roles WHERE id = ANY(%s::uuid[])",
            (list(after_ids),)).fetchall() if after_ids else []
        after_grants_delete = any(
            "delete" in r["permissions"].get("company", []) for r in after_roles_rows)

        if before_grants_delete and not after_grants_delete:
            if str(user_id) == str(ctx.user["id"]):
                raise HTTPException(409, {"code": "self_deadmin",
                    "message": "You can't remove your own admin role — "
                               "ask another admin to do it."})
            admins = _admin_membership_ids(conn, ctx.company_id)
            if admins == {str(m["id"])}:
                raise HTTPException(409, {"code": "last_admin",
                    "message": "This is the only membership with full admin "
                               "permissions — assign it to someone else first."})

        if body.remove:
            conn.execute(
                """DELETE FROM membership_roles
                   WHERE membership_id = %s AND role_id = ANY(%s::uuid[])""",
                (m["id"], body.remove))
        for rid in body.add:
            conn.execute(
                """INSERT INTO membership_roles (membership_id, role_id)
                   VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                (m["id"], rid))

        roles_after = _member_roles(conn, m["id"])
        audit(conn, ctx.company_id, ctx.user["id"], "role_change", "membership",
              m["id"], {"added": body.add, "removed": body.remove,
                       "roles_before": sorted(before_ids),
                       "roles_after": sorted(str(r["id"]) for r in roles_after)})
    return {
        "roles": [{"id": str(r["id"]), "name": r["name"], "scope": r["scope"]}
                 for r in roles_after],
        "permissions": resolve_permissions(roles_after),
    }


class SuspendIn(BaseModel):
    reason: str


@router.post("/members/{user_id}/suspend")
def suspend_member(user_id: str, body: SuspendIn,
                   ctx: AuthContext = Depends(require("company", "assign"))):
    try:
        uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(404, "Member not found")
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(422, "A reason is required")
    if str(user_id) == str(ctx.user["id"]):
        raise HTTPException(409, {"code": "self_suspend",
            "message": "You can't suspend your own account — "
                       "ask another admin to do it."})
    with get_pool().connection() as conn:
        m = _get_membership(conn, ctx.company_id, user_id)
        if not m:
            raise HTTPException(404, "Member not found")
        if m["status"] == "active":
            admins = _admin_membership_ids(conn, ctx.company_id)
            if str(m["id"]) in admins and admins == {str(m["id"])}:
                raise HTTPException(409, {"code": "last_admin",
                    "message": "This is the only active admin — "
                               "promote someone else first."})
        result = suspend_membership(conn, m["id"], ctx.user["id"], reason)
        audit(conn, ctx.company_id, ctx.user["id"], "suspend", "membership",
              m["id"], {"reason": reason})
    return {"ok": True, "status": result["status"]}


@router.post("/members/{user_id}/reactivate")
def reactivate_member(user_id: str,
                      ctx: AuthContext = Depends(require("company", "assign"))):
    try:
        uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(404, "Member not found")
    with get_pool().connection() as conn:
        m = _get_membership(conn, ctx.company_id, user_id)
        if not m:
            raise HTTPException(404, "Member not found")
        result = reactivate_membership(conn, m["id"])
        audit(conn, ctx.company_id, ctx.user["id"], "reactivate", "membership",
              m["id"])
    return {"ok": True, "status": result["status"]}


@router.post("/members/{user_id}/revoke-sessions")
def revoke_member_sessions(user_id: str,
                          ctx: AuthContext = Depends(require("company", "assign"))):
    try:
        uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(404, "Member not found")
    with get_pool().connection() as conn:
        m = _get_membership(conn, ctx.company_id, user_id)
        if not m:
            raise HTTPException(404, "Member not found")
        n = security.revoke_all_refresh_tokens(conn, user_id)
        audit(conn, ctx.company_id, ctx.user["id"], "revoke_sessions", "membership",
              m["id"], {"tokens_revoked": n})
    return {"ok": True, "tokens_revoked": n}


class InviteIn(BaseModel):
    email: str
    role_ids: List[str]


@router.post("/invitations")
def create_invitation(body: InviteIn,
                      ctx: AuthContext = Depends(require("company", "assign"))):
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(422, "Invalid email address")
    if not body.role_ids:
        raise HTTPException(422, "At least one role is required")
    with get_pool().connection() as conn:
        for rid in body.role_ids:
            ok = conn.execute(
                """SELECT 1 FROM roles
                   WHERE id = %s AND (company_id IS NULL OR company_id = %s)
                     AND deleted_at IS NULL""",
                (rid, ctx.company_id),
            ).fetchone()
            if not ok:
                raise HTTPException(422, f"Unknown role: {rid}")
        already = conn.execute(
            """SELECT 1 FROM memberships m JOIN users u ON u.id = m.user_id
               WHERE m.company_id = %s AND u.email = %s AND m.deleted_at IS NULL""",
            (ctx.company_id, email),
        ).fetchone()
        if already:
            raise HTTPException(409, "This person is already a member")
        token = security.new_url_token()
        inv = conn.execute(
            """INSERT INTO invitations (company_id, email, role_ids, token,
                                        invited_by, expires_at)
               VALUES (%s, %s, %s::uuid[], %s, %s, %s) RETURNING id""",
            (ctx.company_id, email, body.role_ids, token, ctx.user["id"],
             dt.datetime.now(dt.timezone.utc)
             + dt.timedelta(days=config.INVITE_TTL_DAYS)),
        ).fetchone()
        audit(conn, ctx.company_id, ctx.user["id"], "assign", "invitation",
              inv["id"], {"email": email})
    # The token goes in the invite email once a mailer exists; returned for now
    # so the admin can hand the link to the invitee directly.
    return {"invitation_id": str(inv["id"]), "token": token}


@router.delete("/invitations/{invitation_id}")
def revoke_invitation(invitation_id: str,
                      ctx: AuthContext = Depends(require("company", "assign"))):
    """F6 mitigation: a pending invite (and its token) can be killed at any
    time. Full resolution of F6 = email delivery instead of returned tokens."""
    try:
        uuid.UUID(invitation_id)
    except ValueError:
        raise HTTPException(404, "Invitation not found")
    with get_pool().connection() as conn:
        row = conn.execute(
            """UPDATE invitations SET status = 'revoked'
               WHERE id = %s AND company_id = %s AND status = 'pending'
               RETURNING id""",
            (invitation_id, ctx.company_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "No pending invitation with that id")
        audit(conn, ctx.company_id, ctx.user["id"], "delete", "invitation",
              invitation_id)
    return {"ok": True}


@router.get("/invitations")
def list_invitations(limit: int = Query(100, ge=1, le=500),
                     offset: int = Query(0, ge=0),
                     ctx: AuthContext = Depends(require("company", "view"))):
    with get_pool().connection() as conn:
        rows = conn.execute(
            """SELECT id, email, status, expires_at, created_at FROM invitations
               WHERE company_id = %s ORDER BY created_at DESC
               LIMIT %s OFFSET %s""",
            (ctx.company_id, limit, offset),
        ).fetchall()
    return [{**r, "id": str(r["id"])} for r in rows]


@router.get("/audit")
def audit_trail(response: Response,
                subject_type: Optional[str] = None,
                subject_id: Optional[str] = None,
                actor: Optional[str] = None,
                action: Optional[str] = None,
                date_from: Optional[dt.datetime] = Query(None, alias="from"),
                date_to: Optional[dt.datetime] = Query(None, alias="to"),
                limit: int = Query(100, ge=1, le=500),
                offset: int = Query(0, ge=0),
                ctx: AuthContext = Depends(require("company", "view"))):
    """The company audit trail ("who deleted that inspection?"). Admin-only
    (company:view). Optional subject filters; newest first. `actor`/`action`/
    `from`/`to` added for the admin users/roles surface — `subject_type`/
    `subject_id` (pre-existing, tested) map to what that surface calls
    'target'; kept as-is rather than renamed."""
    with get_pool().connection() as conn:
        where = "company_id = %s"
        params = [ctx.company_id]
        if subject_type:
            where += " AND subject_type = %s"
            params.append(subject_type)
        if subject_id:
            try:
                uuid.UUID(subject_id)
            except ValueError:
                raise HTTPException(422, "subject_id must be a UUID")
            where += " AND subject_id = %s"
            params.append(subject_id)
        if actor:
            try:
                uuid.UUID(actor)
            except ValueError:
                raise HTTPException(422, "actor must be a UUID")
            where += " AND user_id = %s"
            params.append(actor)
        if action:
            where += " AND action = %s"
            params.append(action)
        if date_from:
            where += " AND at >= %s"
            params.append(date_from)
        if date_to:
            where += " AND at <= %s"
            params.append(date_to)
        total = conn.execute(
            f"SELECT count(*) AS n FROM audit_log WHERE {where}",
            params).fetchone()["n"]
        rows = conn.execute(
            f"""SELECT id, user_id, action, subject_type, subject_id,
                       details, ip, user_agent, at
                FROM audit_log WHERE {where}
                ORDER BY at DESC LIMIT %s OFFSET %s""",
            params + [limit, offset]).fetchall()
    response.headers["X-Total-Count"] = str(total)
    return [{**r,
             "user_id": str(r["user_id"]) if r["user_id"] else None,
             "subject_id": str(r["subject_id"]) if r["subject_id"] else None}
            for r in rows]
