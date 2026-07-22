"""Authentication + authorization dependencies.

Every protected endpoint runs, in order:
  1. valid access token  -> current user
  2. active membership in the company (path param company_id)
  3. union-of-roles permission check for (module, action)
  4. scope check via assignments when EVERY granting role is scope='assigned'

The subject_type used for assignment checks is derived from the module name:
vehicle_* -> the row's vehicle, property_* -> the row's property,
projects -> the project.
"""
import uuid
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request

from .db import get_pool
from .security import decode_token

MODULE_SUBJECT = {"projects": "project"}
for _m in ("vehicles", "vehicle_inspections", "vehicle_maintenance",
           "vehicle_repairs", "vehicle_warranties"):
    MODULE_SUBJECT[_m] = "vehicle"
for _m in ("properties", "property_inspections", "property_maintenance",
           "property_repairs", "property_warranties"):
    MODULE_SUBJECT[_m] = "property"


@dataclass
class AuthContext:
    user: dict
    company_id: str = ""
    membership_id: str = ""
    roles: list = field(default_factory=list)   # [{id,name,scope,permissions}]

    def grant_scope(self, module: str, action: str):
        """Returns 'company', 'assigned', or None (no grant)."""
        best = None
        for role in self.roles:
            if action in role["permissions"].get(module, []):
                if role["scope"] == "company":
                    return "company"
                best = "assigned"
        return best

    def visible_subject_ids(self, conn, subject_type: str):
        """For 'assigned'-scope access: the subject ids this user may see."""
        rows = conn.execute(
            """SELECT DISTINCT subject_id FROM assignments
               WHERE company_id = %s AND user_id = %s AND subject_type = %s
                 AND deleted_at IS NULL""",
            (self.company_id, self.user["id"], subject_type),
        ).fetchall()
        return {r["subject_id"] for r in rows}


def current_user(request: Request) -> dict:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token")
    try:
        payload = decode_token(auth[7:].strip(), "access")
    except Exception:
        raise HTTPException(401, "Invalid or expired token")
    with get_pool().connection() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = %s",
                            (payload["sub"],)).fetchone()
    if not user:
        raise HTTPException(401, "Unknown user")
    return user


def company_member(company_id: str, user: dict = Depends(current_user)) -> AuthContext:
    try:
        uuid.UUID(company_id)
    except ValueError:
        raise HTTPException(404, "Company not found")
    with get_pool().connection() as conn:
        m = conn.execute(
            """SELECT id, status FROM memberships
               WHERE company_id = %s AND user_id = %s AND deleted_at IS NULL""",
            (company_id, user["id"]),
        ).fetchone()
        if not m:
            raise HTTPException(403, {"code": "not_a_member",
                                      "message": "Not a member of this company"})
        if m["status"] != "active":
            # Rejected on the next access-token check, so a suspension takes
            # effect within one token TTL (<=30 min) even without revocation —
            # revoke_all_refresh_tokens (called by suspend_membership) makes
            # sure there's no lingering refresh token to mint a new one, either.
            raise HTTPException(403, {"code": "account_suspended",
                                      "message": "Your account has been suspended."})
        roles = conn.execute(
            """SELECT r.id, r.name, r.scope, r.permissions
               FROM membership_roles mr JOIN roles r ON r.id = mr.role_id
               WHERE mr.membership_id = %s AND r.deleted_at IS NULL""",
            (m["id"],),
        ).fetchall()
    return AuthContext(user=user, company_id=company_id,
                       membership_id=str(m["id"]), roles=roles)


def resolve_permissions(roles) -> dict:
    """Union-of-roles module -> sorted actions. Same computation /me already
    does inline; factored out so the admin member-detail endpoint (which
    resolves a *target* member's roles, not the caller's) doesn't duplicate
    it."""
    effective = {}
    for role in roles:
        for module, actions in role["permissions"].items():
            effective.setdefault(module, set()).update(actions)
    return {k: sorted(v) for k, v in effective.items()}


def require(module: str, action: str):
    """Dependency factory: asserts the member has (module, action) somewhere.

    Endpoint code must still apply assignment filtering when
    ctx.grant_scope(module, action) == 'assigned'.
    """
    def dep(ctx: AuthContext = Depends(company_member)) -> AuthContext:
        if ctx.grant_scope(module, action) is None:
            raise HTTPException(403, f"Requires {module}:{action}")
        return ctx
    return dep
