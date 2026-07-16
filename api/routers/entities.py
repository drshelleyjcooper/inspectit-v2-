"""Read endpoints for vehicles/properties/projects with assignment scoping
and pagination (F8): ?limit= (default 100, max 500) & ?offset=, plus an
X-Total-Count header with the unpaginated count.

Phase 1/2 ship list endpoints (enough to prove the permission + scope model
end-to-end); full CRUD arrives with the app data-layer swap in a later phase.
"""
from fastapi import APIRouter, Depends, Query, Response

from ..db import get_pool
from ..permissions import AuthContext, require

router = APIRouter(prefix="/companies/{company_id}", tags=["entities"])

MAX_PAGE = 500


def _scoped_list(ctx: AuthContext, module: str, subject_type: str,
                 select_sql: str, order_by: str, response: Response,
                 limit: int, offset: int):
    """select_sql must end with: WHERE company_id = %s AND deleted_at IS NULL.
    Assigned-scope callers are filtered in SQL so pagination stays correct."""
    scope = ctx.grant_scope(module, "view")  # require() ensured non-None
    with get_pool().connection() as conn:
        params = [ctx.company_id]
        sql = select_sql
        if scope == "assigned":
            visible = ctx.visible_subject_ids(conn, subject_type)
            if not visible:
                response.headers["X-Total-Count"] = "0"
                return []
            sql += " AND id = ANY(%s)"
            params.append(list(visible))
        total = conn.execute(
            f"SELECT count(*) AS n FROM ({sql}) sub", params).fetchone()["n"]
        rows = conn.execute(
            f"{sql} ORDER BY {order_by} LIMIT %s OFFSET %s",
            params + [limit, offset]).fetchall()
    response.headers["X-Total-Count"] = str(total)
    return [{**r, "id": str(r["id"])} for r in rows]


def _page(limit: int = Query(100, ge=1, le=MAX_PAGE),
          offset: int = Query(0, ge=0)):
    return {"limit": limit, "offset": offset}


@router.get("/vehicles")
def list_vehicles(response: Response, page: dict = Depends(_page),
                  ctx: AuthContext = Depends(require("vehicles", "view"))):
    return _scoped_list(ctx, "vehicles", "vehicle",
        """SELECT id, vehicle_id, plate, make_model, vtype, current_odometer
           FROM vehicles WHERE company_id = %s AND deleted_at IS NULL""",
        "vehicle_id", response, **page)


@router.get("/properties")
def list_properties(response: Response, page: dict = Depends(_page),
                    ctx: AuthContext = Depends(require("properties", "view"))):
    return _scoped_list(ctx, "properties", "property",
        """SELECT id, property_id, ptype, street, city, state, zip
           FROM properties WHERE company_id = %s AND deleted_at IS NULL""",
        "property_id", response, **page)


@router.get("/projects")
def list_projects(response: Response, page: dict = Depends(_page),
                  ctx: AuthContext = Depends(require("projects", "view"))):
    rows = _scoped_list(ctx, "projects", "project",
        """SELECT id, property_id, name, project_date, project_type, status
           FROM projects WHERE company_id = %s AND deleted_at IS NULL""",
        "name", response, **page)
    return [{**p, "property_id": str(p["property_id"])} for p in rows]
