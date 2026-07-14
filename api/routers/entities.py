"""Read endpoints for vehicles/properties/projects with assignment scoping.

Phase 1 ships list endpoints (enough to prove the permission + scope model
end-to-end); full CRUD arrives with the app data-layer swap in phase 2.
"""
from fastapi import APIRouter, Depends

from ..db import get_pool
from ..permissions import AuthContext, require

router = APIRouter(prefix="/companies/{company_id}", tags=["entities"])


def _scoped_list(ctx: AuthContext, module: str, subject_type: str, sql: str):
    scope = ctx.grant_scope(module, "view")  # require() already ensured non-None
    with get_pool().connection() as conn:
        rows = conn.execute(sql, (ctx.company_id,)).fetchall()
        if scope == "assigned":
            visible = ctx.visible_subject_ids(conn, subject_type)
            rows = [r for r in rows if r["id"] in visible]
    return [{**r, "id": str(r["id"])} for r in rows]


@router.get("/vehicles")
def list_vehicles(ctx: AuthContext = Depends(require("vehicles", "view"))):
    return _scoped_list(ctx, "vehicles", "vehicle",
        """SELECT id, vehicle_id, plate, make_model, vtype, current_odometer
           FROM vehicles WHERE company_id = %s AND deleted_at IS NULL
           ORDER BY vehicle_id""")


@router.get("/properties")
def list_properties(ctx: AuthContext = Depends(require("properties", "view"))):
    return _scoped_list(ctx, "properties", "property",
        """SELECT id, property_id, ptype, street, city, state, zip
           FROM properties WHERE company_id = %s AND deleted_at IS NULL
           ORDER BY property_id""")


@router.get("/projects")
def list_projects(ctx: AuthContext = Depends(require("projects", "view"))):
    return [{**p, "property_id": str(p["property_id"])} for p in _scoped_list(
        ctx, "projects", "project",
        """SELECT id, property_id, name, project_date, project_type, status
           FROM projects WHERE company_id = %s AND deleted_at IS NULL
           ORDER BY name""")]
