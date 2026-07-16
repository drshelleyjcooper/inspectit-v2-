"""Company membership management: roles listing, members, invitations."""
import datetime as dt
import uuid
from typing import List

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from .. import config, security
from ..db import audit, get_pool
from ..permissions import AuthContext, company_member, require

router = APIRouter(prefix="/companies/{company_id}", tags=["members"])


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
def list_members(limit: int = Query(200, ge=1, le=500),
                 offset: int = Query(0, ge=0),
                 ctx: AuthContext = Depends(company_member)):
    """Members with their roles. Needs company:view (admin) or any assign
    permission (managers must see who they can assign)."""
    can = ctx.grant_scope("company", "view") or any(
        ctx.grant_scope(m, "assign")
        for m in ("vehicles", "properties", "projects"))
    if not can:
        raise HTTPException(403, "Requires company:view or an assign permission")
    with get_pool().connection() as conn:
        rows = conn.execute(
            """SELECT m.id AS membership_id, m.status, u.id AS user_id,
                      u.name, u.email,
                      COALESCE(json_agg(json_build_object('id', r.id, 'name', r.name))
                               FILTER (WHERE r.id IS NOT NULL), '[]') AS roles
               FROM memberships m
               JOIN users u ON u.id = m.user_id
               LEFT JOIN membership_roles mr ON mr.membership_id = m.id
               LEFT JOIN roles r ON r.id = mr.role_id AND r.deleted_at IS NULL
               WHERE m.company_id = %s AND m.deleted_at IS NULL
               GROUP BY m.id, m.status, u.id, u.name, u.email
               ORDER BY u.name LIMIT %s OFFSET %s""",
            (ctx.company_id, limit, offset),
        ).fetchall()
    return [{**r, "membership_id": str(r["membership_id"]),
             "user_id": str(r["user_id"])} for r in rows]


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
                limit: int = Query(100, ge=1, le=500),
                offset: int = Query(0, ge=0),
                ctx: AuthContext = Depends(require("company", "view"))):
    """The company audit trail ("who deleted that inspection?"). Admin-only
    (company:view). Optional subject filters; newest first."""
    with get_pool().connection() as conn:
        where = "company_id = %s"
        params = [ctx.company_id]
        if subject_type:
            where += " AND subject_type = %s"
            params.append(subject_type)
        if subject_id:
            where += " AND subject_id = %s"
            params.append(subject_id)
        total = conn.execute(
            f"SELECT count(*) AS n FROM audit_log WHERE {where}",
            params).fetchone()["n"]
        rows = conn.execute(
            f"""SELECT id, user_id, action, subject_type, subject_id,
                       details, at
                FROM audit_log WHERE {where}
                ORDER BY at DESC LIMIT %s OFFSET %s""",
            params + [limit, offset]).fetchall()
    response.headers["X-Total-Count"] = str(total)
    return [{**r,
             "user_id": str(r["user_id"]) if r["user_id"] else None,
             "subject_id": str(r["subject_id"]) if r["subject_id"] else None}
            for r in rows]
