"""Company membership management: roles listing, members, invitations."""
import datetime as dt
import uuid
from typing import List

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from .. import config, security
from ..db import audit, get_pool
from ..permissions import AuthContext, company_member, require, require_any
from ..presets import ADMIN, BLOCKED_COMBINATIONS

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
        _validate_role_ids(conn, ctx.company_id, body.role_ids)
        _assert_may_grant(conn, ctx, body.role_ids)
        _assert_no_blocked_combo(conn, ctx, email, body.role_ids)

        # An existing member may be invited again to ADD a role (§4.3) — one
        # person, one sign-in, however many domains they work in. Only refuse
        # when there is nothing new to add.
        held = _held_role_names(conn, ctx.company_id, email)
        wanted = _role_names(conn, ctx.company_id, body.role_ids)
        if wanted <= held:
            raise HTTPException(
                409, "This person already has "
                     + ("that role" if len(wanted) == 1 else "those roles"))

        # Supersede any pending invitation for this address rather than
        # stacking a second one: _held_role_names reads pending invitations, so
        # duplicates would make the combination check drift (§7.4 item 5).
        conn.execute(
            """UPDATE invitations SET status = 'revoked'
               WHERE company_id = %s AND email = %s AND status = 'pending'""",
            (ctx.company_id, email))

        token = security.new_url_token()
        inv = conn.execute(
            """INSERT INTO invitations (company_id, email, role_ids, token,
                                        invited_by, expires_at)
               VALUES (%s, %s, %s::uuid[], %s, %s, %s) RETURNING id""",
            (ctx.company_id, email, list(set(body.role_ids)), token,
             ctx.user["id"],
             dt.datetime.now(dt.timezone.utc)
             + dt.timedelta(days=config.INVITE_TTL_DAYS)),
        ).fetchone()
        audit(conn, ctx.company_id, ctx.user["id"], "assign", "invitation",
              inv["id"], {"email": email, "roles": sorted(wanted)})
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
                     ctx: AuthContext = Depends(
                         require_any(("company", "view"),
                                     ("company", "assign")))):
    """Domain managers hold company:assign but not company:view, so under v2.0
    they could create an invitation and then not see it. The roles come back
    too — the UI showed "No role" because they were never returned."""
    with get_pool().connection() as conn:
        rows = conn.execute(
            """SELECT i.id, i.email, i.status, i.expires_at, i.created_at,
                      i.role_ids,
                      COALESCE(array_agg(r.name ORDER BY r.name)
                               FILTER (WHERE r.id IS NOT NULL), '{}') AS role_names
               FROM invitations i
               LEFT JOIN roles r ON r.id = ANY(i.role_ids)
                                AND r.deleted_at IS NULL
               WHERE i.company_id = %s
               GROUP BY i.id
               ORDER BY i.created_at DESC
               LIMIT %s OFFSET %s""",
            (ctx.company_id, limit, offset),
        ).fetchall()
    return [{**r, "id": str(r["id"]),
             "role_ids": [str(x) for x in (r["role_ids"] or [])]} for r in rows]


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
            try:
                uuid.UUID(subject_id)
            except ValueError:
                raise HTTPException(422, "subject_id must be a UUID")
            where += " AND subject_id = %s"
            params.append(subject_id)
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


class MemberPatch(BaseModel):
    role_ids: Optional[List[str]] = None
    status: Optional[str] = None          # 'active' | 'suspended'
    can_grant_viewers: Optional[bool] = None   # admin-only (§2.5)


def _load_membership(conn, company_id: str, membership_id: str):
    try:
        uuid.UUID(membership_id)
    except ValueError:
        raise HTTPException(404, "Member not found")
    row = conn.execute(
        """SELECT m.id, m.status, m.user_id, u.name, u.email
           FROM memberships m JOIN users u ON u.id = m.user_id
           WHERE m.id = %s AND m.company_id = %s AND m.deleted_at IS NULL""",
        (membership_id, company_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Member not found")
    return row


def _validate_role_ids(conn, company_id: str, role_ids):
    """Same check the invitation route makes: real roles, ours or preset."""
    if not role_ids:
        raise HTTPException(422, "At least one role is required")
    for rid in role_ids:
        ok = conn.execute(
            """SELECT 1 FROM roles
               WHERE id = %s AND (company_id IS NULL OR company_id = %s)
                 AND deleted_at IS NULL""",
            (rid, company_id),
        ).fetchone()
        if not ok:
            raise HTTPException(422, f"Unknown role: {rid}")


def _role_names(conn, company_id: str, role_ids) -> set:
    rows = conn.execute(
        """SELECT name FROM roles
           WHERE id = ANY(%s::uuid[]) AND deleted_at IS NULL
             AND (company_id IS NULL OR company_id = %s)""",
        (list(role_ids), company_id),
    ).fetchall()
    return {r["name"] for r in rows}


def _assert_may_grant(conn, ctx, role_ids):
    """The caller may only issue roles in its own grantable set (§4.2).

    A hidden dropdown option is not a control: the UI filters this list, and
    this is the check that actually holds.
    """
    wanted = _role_names(conn, ctx.company_id, role_ids)
    allowed = ctx.grantable_roles()
    refused = wanted - allowed
    if refused:
        raise HTTPException(
            403, "You can't assign " + ", ".join(sorted(refused))
                 + ". You may assign: "
                 + (", ".join(sorted(allowed)) if allowed
                    else "no roles — ask an administrator."))


def _held_role_names(conn, company_id: str, email: str) -> set:
    """Roles the person already holds here, plus any on a pending invitation.

    Pending invitations count. Otherwise two invitations sent before either is
    accepted would slip the combination through (§9.2).
    """
    names = set()
    rows = conn.execute(
        """SELECT r.name FROM memberships m
           JOIN users u ON u.id = m.user_id
           JOIN membership_roles mr ON mr.membership_id = m.id
           JOIN roles r ON r.id = mr.role_id AND r.deleted_at IS NULL
           WHERE m.company_id = %s AND u.email = %s AND m.deleted_at IS NULL""",
        (company_id, email),
    ).fetchall()
    names |= {r["name"] for r in rows}
    rows = conn.execute(
        """SELECT r.name FROM invitations i
           JOIN roles r ON r.id = ANY(i.role_ids) AND r.deleted_at IS NULL
           WHERE i.company_id = %s AND i.email = %s AND i.status = 'pending'
             AND i.expires_at > now()""",
        (company_id, email),
    ).fetchall()
    return names | {r["name"] for r in rows}


def _assert_no_blocked_combo(conn, ctx, email: str, new_role_ids):
    """Block inspector + maintenance in one domain, at submission (§2.3).

    Blocking here rather than holding the grant for approval is deliberate: on
    the accumulation path a pending-approval design would revoke a working
    inspector's access the moment they accepted, and with no mailer wired up
    the request could sit unseen for days.
    """
    if ctx.is_company_admin():
        return                       # Manager and Company Administrator may
    resulting = (_held_role_names(conn, ctx.company_id, email)
                 | _role_names(conn, ctx.company_id, new_role_ids))
    for pair in BLOCKED_COMBINATIONS:
        if pair <= resulting:
            raise HTTPException(
                403, "Holding " + " and ".join(sorted(pair)) + " together needs "
                     "administrator approval. Ask an administrator to grant it.")


def _assert_may_manage(conn, ctx, membership_id: str):
    """You may not modify or remove a member holding roles you couldn't issue.

    Without this, giving the domain managers company:assign in v2.0 would let a
    Vehicle Manager rewrite the Company Administrator's roles or remove them —
    both routes depend on company:assign alone.
    """
    if ctx.is_company_admin():
        return
    rows = conn.execute(
        """SELECT r.name FROM membership_roles mr
           JOIN roles r ON r.id = mr.role_id AND r.deleted_at IS NULL
           WHERE mr.membership_id = %s""",
        (membership_id,),
    ).fetchall()
    held = {r["name"] for r in rows}
    if not held <= ctx.grantable_roles():
        raise HTTPException(
            403, "This member holds roles you can't assign, so you can't "
                 "change or remove them. Ask an administrator.")


def _other_admins(conn, company_id: str, membership_id: str) -> int:
    """Active members other than this one who could restore an administrator.

    company:assign is held by five roles now, so it no longer identifies
    someone who can restore an administrator. Keyed on the ability to GRANT
    the administrator role instead.
    """
    return conn.execute(
        """SELECT count(DISTINCT m.id) AS n
           FROM memberships m
           JOIN membership_roles mr ON mr.membership_id = m.id
           JOIN roles r ON r.id = mr.role_id AND r.deleted_at IS NULL
           WHERE m.company_id = %s AND m.id <> %s
             AND m.status = 'active' AND m.deleted_at IS NULL
             AND %s = ANY(r.grants)""",
        (company_id, membership_id, ADMIN),
    ).fetchone()["n"]


def _grants_user_management(conn, company_id: str, role_ids) -> bool:
    # Keyed on the ability to GRANT the administrator role, matching
    # _other_admins. company:assign is held by five roles now, so it no longer
    # distinguishes someone who can restore an administrator from someone who
    # merely manages their own domain (§7.5).
    return conn.execute(
        """SELECT count(*) AS n FROM roles
           WHERE id = ANY(%s::uuid[]) AND deleted_at IS NULL
             AND (company_id IS NULL OR company_id = %s)
             AND %s = ANY(grants)""",
        (list(role_ids), company_id, ADMIN),
    ).fetchone()["n"] > 0


@router.patch("/members/{membership_id}")
def update_member(membership_id: str, body: MemberPatch,
                  ctx: AuthContext = Depends(require("company", "assign"))):
    """Change a member's roles and/or suspend them.

    Refuses any change that would leave the company with nobody able to manage
    users — demoting the last administrator locks everyone out permanently, and
    there is no self-service way back.
    """
    if (body.role_ids is None and body.status is None
            and body.can_grant_viewers is None):
        raise HTTPException(422, "Nothing to change")
    if body.status is not None and body.status not in ("active", "suspended"):
        raise HTTPException(422, "status must be 'active' or 'suspended'")

    with get_pool().connection() as conn:
        row = _load_membership(conn, ctx.company_id, membership_id)
        _assert_may_manage(conn, ctx, membership_id)
        details = {"email": row["email"]}

        losing_admin = (
            (body.status == "suspended")
            or (body.role_ids is not None
                and not _grants_user_management(conn, ctx.company_id, body.role_ids))
        )
        if losing_admin and _other_admins(conn, ctx.company_id, membership_id) == 0:
            raise HTTPException(
                409, "This is the last member who can manage users. Give someone "
                     "else an administrator role first.")

        if body.role_ids is not None:
            _validate_role_ids(conn, ctx.company_id, body.role_ids)
            _assert_may_grant(conn, ctx, body.role_ids)
            _assert_no_blocked_combo(conn, ctx, row["email"], body.role_ids)
            conn.execute("DELETE FROM membership_roles WHERE membership_id = %s",
                         (membership_id,))
            for rid in body.role_ids:
                conn.execute(
                    """INSERT INTO membership_roles (membership_id, role_id)
                       VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                    (membership_id, rid))
            details["role_ids"] = list(body.role_ids)

        if body.status is not None:
            conn.execute("UPDATE memberships SET status = %s WHERE id = %s",
                         (body.status, membership_id))
            details["status"] = body.status

        if body.can_grant_viewers is not None:
            if not ctx.is_company_admin():
                raise HTTPException(403, "Only an administrator can change "
                                         "who may create viewers")
            conn.execute("UPDATE memberships SET can_grant_viewers = %s "
                         "WHERE id = %s", (body.can_grant_viewers, membership_id))
            details["can_grant_viewers"] = body.can_grant_viewers

        audit(conn, ctx.company_id, ctx.user["id"], "assign", "membership",
              membership_id, details)
    return {"ok": True}


@router.delete("/members/{membership_id}")
def remove_member(membership_id: str,
                  ctx: AuthContext = Depends(require("company", "assign"))):
    """Remove someone from this company.

    Soft delete: the users row survives untouched, because a person can belong
    to more than one company and their name is still on the records they filed.
    The membership is marked removed, which drops them from company_member on
    their next request.
    """
    with get_pool().connection() as conn:
        row = _load_membership(conn, ctx.company_id, membership_id)
        _assert_may_manage(conn, ctx, membership_id)

        if str(row["user_id"]) == str(ctx.user["id"]):
            raise HTTPException(
                409, "You can't remove yourself. Ask another administrator.")
        if _other_admins(conn, ctx.company_id, membership_id) == 0:
            raise HTTPException(
                409, "This is the last member who can manage users. Give someone "
                     "else an administrator role first.")

        # deleted_at alone is the removal signal: list_members and
        # company_member both filter on it, so status stays within whatever
        # values the column's constraint allows.
        conn.execute(
            """UPDATE memberships
               SET deleted_at = now(), updated_at = now()
               WHERE id = %s AND company_id = %s AND deleted_at IS NULL""",
            (membership_id, ctx.company_id))
        conn.execute("DELETE FROM membership_roles WHERE membership_id = %s",
                     (membership_id,))
        audit(conn, ctx.company_id, ctx.user["id"], "delete", "membership",
              membership_id, {"email": row["email"], "name": row["name"]})
    return {"ok": True}
