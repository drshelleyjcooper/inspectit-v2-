"""Authentication + authorization dependencies.

Every protected endpoint runs, in order:
  1. valid access token  -> current user
  2. active membership in the company (path param company_id)
  3. union-of-roles permission check for (module, action)
  4. scope check via assignments when EVERY granting role is scope='assigned'

The subject_type used for assignment checks is derived from the module name:
vehicle_* -> the row's vehicle, property_* -> the row's property,
projects -> the project.

Delegated granting (v2.0) adds a fifth question the module x action matrix
can't answer: *which roles may this member issue?* That comes from
roles.grants, widened by roles.viewer_grants when the membership carries
can_grant_viewers. See USER-ROLES-SPEC v2.0 §4.2.
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
    roles: list = field(default_factory=list)   # [{id,name,scope,permissions,
                                                #   grants,viewer_grants}]
    can_grant_viewers: bool = False

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

    # --- delegated granting ------------------------------------------------

    def grantable_roles(self) -> set:
        """Role NAMES this member may put in an invitation or a role change.

        Union across held roles, since effective permissions are a union: a
        member who is both Vehicle Manager and Property Manager may issue both
        domains' inspectors. Viewer grants fold in only when the administrator
        set the flag on this membership.
        """
        names = set()
        for role in self.roles:
            names |= set(role.get("grants") or [])
            if self.can_grant_viewers:
                names |= set(role.get("viewer_grants") or [])
        return names

    def may_grant(self, role_names) -> bool:
        return set(role_names) <= self.grantable_roles()

    def is_company_admin(self) -> bool:
        """Holds company:admin — Company Administrator or Manager.

        This is the line the inspector+maintenance block draws (§2.3): the
        block exists to stop a DOMAIN manager assembling a near-peer out of two
        narrower grants, and the domain managers are exactly the grant-capable
        roles that lack company:admin.
        """
        return self.grant_scope("company", "admin") is not None


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
    if user.get("disabled_at"):
        raise HTTPException(403, "This account has been disabled")
    return user


def platform_admin(user: dict = Depends(current_user)) -> dict:
    """Cross-tenant operator access (the /admin portal). Not a company role:
    it is the users.is_platform_admin flag, set via PLATFORM_ADMIN_EMAILS or
    by another platform admin."""
    if not user.get("is_platform_admin"):
        raise HTTPException(403, "Platform admin access required")
    return user


def company_member(company_id: str, user: dict = Depends(current_user)) -> AuthContext:
    try:
        uuid.UUID(company_id)
    except ValueError:
        raise HTTPException(404, "Company not found")
    with get_pool().connection() as conn:
        m = conn.execute(
            """SELECT id, can_grant_viewers FROM memberships
               WHERE company_id = %s AND user_id = %s AND status = 'active'
                 AND deleted_at IS NULL""",
            (company_id, user["id"]),
        ).fetchone()
        if not m:
            raise HTTPException(403, "Not a member of this company")
        roles = conn.execute(
            """SELECT r.id, r.name, r.scope, r.permissions,
                      r.grants, r.viewer_grants
               FROM membership_roles mr JOIN roles r ON r.id = mr.role_id
               WHERE mr.membership_id = %s AND r.deleted_at IS NULL""",
            (m["id"],),
        ).fetchall()
    return AuthContext(user=user, company_id=company_id,
                       membership_id=str(m["id"]), roles=roles,
                       can_grant_viewers=bool(m["can_grant_viewers"]))


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


def require_any(*pairs):
    """Passes if the member holds ANY of the given (module, action) pairs.

    Needed since v2.0: the domain managers hold company:assign but not
    company:view, so routes that serve both them and administrators can no
    longer key on a single permission.
    """
    def dep(ctx: AuthContext = Depends(company_member)) -> AuthContext:
        if any(ctx.grant_scope(m, a) is not None for m, a in pairs):
            return ctx
        wanted = " or ".join(f"{m}:{a}" for m, a in pairs)
        raise HTTPException(403, f"Requires {wanted}")
    return dep
