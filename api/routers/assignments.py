"""Assignments: managers assign users to vehicles/properties/projects.
Requires the `assign` action on the module matching the subject type."""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..db import audit, get_pool
from ..permissions import AuthContext, company_member

router = APIRouter(prefix="/companies/{company_id}", tags=["assignments"])

SUBJECT_MODULE = {"vehicle": "vehicles", "property": "properties",
                  "project": "projects"}
SUBJECT_TABLE = {"vehicle": "vehicles", "property": "properties",
                 "project": "projects"}


class AssignIn(BaseModel):
    user_id: str
    subject_type: str
    subject_id: str
    duty: str = "inspection"


@router.post("/assignments")
def create_assignment(body: AssignIn,
                      ctx: AuthContext = Depends(company_member)):
    module = SUBJECT_MODULE.get(body.subject_type)
    if not module:
        raise HTTPException(422, "subject_type must be vehicle|property|project")
    if body.duty not in ("inspection", "maintenance", "manage"):
        raise HTTPException(422, "duty must be inspection|maintenance|manage")
    if not ctx.grant_scope(module, "assign"):
        raise HTTPException(403, f"Requires {module}:assign")
    with get_pool().connection() as conn:
        subject = conn.execute(
            f"""SELECT 1 FROM {SUBJECT_TABLE[body.subject_type]}
                WHERE id = %s AND company_id = %s AND deleted_at IS NULL""",
            (body.subject_id, ctx.company_id),
        ).fetchone()
        if not subject:
            raise HTTPException(404, f"{body.subject_type} not found in this company")
        member = conn.execute(
            """SELECT 1 FROM memberships WHERE company_id = %s AND user_id = %s
               AND status = 'active' AND deleted_at IS NULL""",
            (ctx.company_id, body.user_id),
        ).fetchone()
        if not member:
            raise HTTPException(404, "User is not a member of this company")
        row = conn.execute(
            """INSERT INTO assignments (company_id, user_id, subject_type,
                                        subject_id, duty, assigned_by)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (company_id, user_id, subject_type, subject_id, duty)
                 WHERE deleted_at IS NULL
               DO UPDATE SET assigned_by = EXCLUDED.assigned_by
               RETURNING id""",
            (ctx.company_id, body.user_id, body.subject_type, body.subject_id,
             body.duty, ctx.user["id"]),
        ).fetchone()
        audit(conn, ctx.company_id, ctx.user["id"], "assign", body.subject_type,
              body.subject_id, {"assignee": body.user_id, "duty": body.duty})
    return {"assignment_id": str(row["id"])}


@router.get("/assignments")
def list_assignments(subject_type: Optional[str] = None,
                     user_id: Optional[str] = None,
                     limit: int = Query(100, ge=1, le=500),
                     offset: int = Query(0, ge=0),
                     ctx: AuthContext = Depends(company_member)):
    if user_id is not None:
        try:
            uuid.UUID(user_id)
        except ValueError:
            raise HTTPException(422, "user_id must be a UUID")
    assignable = [st for st, mod in SUBJECT_MODULE.items()
                  if ctx.grant_scope(mod, "assign")]
    if ctx.grant_scope("company", "view"):
        assignable = list(SUBJECT_MODULE)
    if not assignable:
        raise HTTPException(403, "Requires an assign permission")
    with get_pool().connection() as conn:
        rows = conn.execute(
            """SELECT id, user_id, subject_type, subject_id, duty, created_at
               FROM assignments
               WHERE company_id = %s AND deleted_at IS NULL
                 AND subject_type = ANY(%s)
                 AND (%s::text IS NULL OR subject_type = %s)
                 AND (%s::uuid IS NULL OR user_id = %s)
               ORDER BY created_at DESC LIMIT %s OFFSET %s""",
            (ctx.company_id, assignable, subject_type, subject_type,
             user_id, user_id, limit, offset),
        ).fetchall()
    return [{**r, "id": str(r["id"]), "user_id": str(r["user_id"]),
             "subject_id": str(r["subject_id"])} for r in rows]


@router.delete("/assignments/{assignment_id}")
def delete_assignment(assignment_id: str,
                      ctx: AuthContext = Depends(company_member)):
    with get_pool().connection() as conn:
        row = conn.execute(
            """SELECT subject_type FROM assignments
               WHERE id = %s AND company_id = %s AND deleted_at IS NULL""",
            (assignment_id, ctx.company_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Assignment not found")
        if not ctx.grant_scope(SUBJECT_MODULE[row["subject_type"]], "assign"):
            raise HTTPException(403, "Requires assign permission")
        conn.execute("UPDATE assignments SET deleted_at = now() WHERE id = %s",
                     (assignment_id,))
        audit(conn, ctx.company_id, ctx.user["id"], "delete", "assignment",
              assignment_id)
    return {"ok": True}
