"""The company record itself: read it, rename it.

Everything else about a company lives in collections (the app's data) or in
members/roles. This router only covers the row in `companies`, which until
now could be created (signup) but never changed.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import audit, get_pool
from ..permissions import AuthContext, company_member, require

router = APIRouter(prefix="/companies/{company_id}", tags=["company"])

NAME_MAX = 120


class CompanyPatch(BaseModel):
    name: str


def _row(conn, company_id):
    row = conn.execute(
        """SELECT id, name, address, city, state, zip, phone, email,
                  created_at, updated_at
           FROM companies WHERE id = %s AND deleted_at IS NULL""",
        (company_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Company not found")
    return {**row, "id": str(row["id"])}


@router.get("")
def get_company(ctx: AuthContext = Depends(company_member)):
    """Any active member may read the company record."""
    with get_pool().connection() as conn:
        return _row(conn, ctx.company_id)


@router.patch("")
def rename_company(body: CompanyPatch,
                   ctx: AuthContext = Depends(require("company", "edit"))):
    """Rename the company. Needs company:edit, which the presets give to the
    Company Administrator and the Manager only (USER-ROLES-SPEC §4.1)."""
    name = " ".join(body.name.split())
    if not name:
        raise HTTPException(422, "Company name can't be blank")
    if len(name) > NAME_MAX:
        raise HTTPException(422, f"Company name is limited to {NAME_MAX} characters")
    with get_pool().connection() as conn:
        before = _row(conn, ctx.company_id)
        if before["name"] != name:
            conn.execute(
                "UPDATE companies SET name = %s, updated_at = now() WHERE id = %s",
                (name, ctx.company_id))
            audit(conn, ctx.company_id, ctx.user["id"], "edit", "company",
                  ctx.company_id, {"field": "name", "from": before["name"],
                                   "to": name})
        return _row(conn, ctx.company_id)
