"""GET /me — who am I, which companies, which roles/permissions."""
from fastapi import APIRouter, Depends

from ..db import get_pool
from ..permissions import current_user

router = APIRouter(tags=["me"])


@router.get("/me")
def me(user: dict = Depends(current_user)):
    with get_pool().connection() as conn:
        memberships = conn.execute(
            """SELECT m.id AS membership_id, m.company_id, c.name AS company_name
               FROM memberships m JOIN companies c ON c.id = m.company_id
               WHERE m.user_id = %s AND m.status = 'active'
                 AND m.deleted_at IS NULL AND c.deleted_at IS NULL""",
            (user["id"],),
        ).fetchall()
        out = []
        for m in memberships:
            roles = conn.execute(
                """SELECT r.id, r.name, r.scope, r.permissions
                   FROM membership_roles mr JOIN roles r ON r.id = mr.role_id
                   WHERE mr.membership_id = %s AND r.deleted_at IS NULL""",
                (m["membership_id"],),
            ).fetchall()
            # Effective permissions = union across roles (module -> actions).
            effective = {}
            for r in roles:
                for module, actions in r["permissions"].items():
                    effective.setdefault(module, set()).update(actions)
            out.append({
                "company_id": str(m["company_id"]),
                "company_name": m["company_name"],
                "roles": [{"id": str(r["id"]), "name": r["name"],
                           "scope": r["scope"]} for r in roles],
                "permissions": {k: sorted(v) for k, v in effective.items()},
            })
    return {
        "id": str(user["id"]), "email": user["email"], "name": user["name"],
        "memberships": out,
    }
