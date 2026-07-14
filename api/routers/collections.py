"""Collection-level sync for the existing single-file app (phase 2).

The app keeps localStorage as its working cache and mirrors each data key
here: pull on boot, push on change. Keys are the app's K-keys without the
"inspectit." prefix. Concurrency: PUT may send base_updated_at; a mismatch
returns 409 with the server copy (the app then takes the server version).

Permission mapping: each key belongs to a module; GET needs module:view,
PUT needs module:edit — and both require a company-scope grant, because a
whole-collection blob can't be filtered per assignment. (Assigned-scope
roles like inspectors will use the per-record API when that lands.)
"""
import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from ..db import audit, get_pool
from ..permissions import AuthContext, company_member

router = APIRouter(prefix="/companies/{company_id}", tags=["collections"])

MAX_COLLECTION_BYTES = 25 * 1024 * 1024

KEY_MODULE = {
    "vehicles": "vehicles",
    "inspections": "vehicle_inspections",
    "tickets": "vehicle_repairs",
    "vehicleMaintenance": "vehicle_maintenance",
    "vehicleMaintTemplates": "vehicle_maintenance",
    "vehicleMaintSpend": "vehicle_maintenance",
    "vehicleWarranties": "vehicle_warranties",
    "properties": "properties",
    "propertyInspections": "property_inspections",
    "propertyTickets": "property_repairs",
    "propertyMaintenance": "property_maintenance",
    "propertyMaintTemplates": "property_maintenance",
    "propertyMaintSpend": "property_maintenance",
    "propertyWarranties": "property_warranties",
    "projects": "projects",
    "projectMeta": "projects",
    "diagram.auto": "vehicle_inspections",
    "diagram.van": "vehicle_inspections",
    "diagram.comm": "vehicle_inspections",
    "profile": None,   # special-cased: GET any member, PUT company:edit
}

# Local-only keys that must never reach the server.
FORBIDDEN_KEYS = {"account", "session", "users", "cloud"}


def _check(ctx: AuthContext, key: str, action: str):
    key = key.split("inspectit.", 1)[-1]
    if key in FORBIDDEN_KEYS or key not in KEY_MODULE:
        raise HTTPException(422, f"'{key}' is not a syncable collection")
    module = KEY_MODULE[key]
    if module is None:   # profile
        if action == "view":
            return key
        if ctx.grant_scope("company", "edit") == "company":
            return key
        raise HTTPException(403, "Requires company:edit")
    if ctx.grant_scope(module, action) != "company":
        raise HTTPException(403,
            f"Requires company-wide {module}:{action} (collection sync is not "
            f"available to assigned-scope roles)")
    return key


class PutCollectionIn(BaseModel):
    data: Any
    base_updated_at: Optional[str] = None


@router.get("/collections")
def index(ctx: AuthContext = Depends(company_member)):
    """Sync index: every collection the caller may view, with timestamps."""
    with get_pool().connection() as conn:
        rows = conn.execute(
            """SELECT key, updated_at FROM app_collections
               WHERE company_id = %s ORDER BY key""",
            (ctx.company_id,),
        ).fetchall()
    out = []
    for r in rows:
        try:
            _check(ctx, r["key"], "view")
        except HTTPException:
            continue
        out.append({"key": r["key"], "updated_at": r["updated_at"].isoformat()})
    return out


@router.get("/collections/{key}")
def get_collection(key: str, ctx: AuthContext = Depends(company_member)):
    key = _check(ctx, key, "view")
    with get_pool().connection() as conn:
        row = conn.execute(
            """SELECT data, updated_at FROM app_collections
               WHERE company_id = %s AND key = %s""",
            (ctx.company_id, key),
        ).fetchone()
    if not row:
        raise HTTPException(404, "No data for this collection yet")
    return {"key": key, "data": row["data"],
            "updated_at": row["updated_at"].isoformat()}


@router.put("/collections/{key}")
def put_collection(key: str, body: PutCollectionIn,
                   ctx: AuthContext = Depends(company_member)):
    key = _check(ctx, key, "edit")
    size = len(json.dumps(body.data))
    if size > MAX_COLLECTION_BYTES:
        raise HTTPException(413, "Collection too large")
    with get_pool().connection() as conn:
        current = conn.execute(
            """SELECT data, updated_at FROM app_collections
               WHERE company_id = %s AND key = %s""",
            (ctx.company_id, key),
        ).fetchone()
        if body.base_updated_at is not None and current is not None \
                and current["updated_at"].isoformat() != body.base_updated_at:
            return_data = {"detail": "conflict",
                           "server_data": current["data"],
                           "server_updated_at":
                               current["updated_at"].isoformat()}
            raise HTTPException(409, return_data)
        row = conn.execute(
            """INSERT INTO app_collections (company_id, key, data, updated_by)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (company_id, key)
               DO UPDATE SET data = EXCLUDED.data,
                             updated_by = EXCLUDED.updated_by
               RETURNING updated_at""",
            (ctx.company_id, key, Jsonb(body.data), ctx.user["id"]),
        ).fetchone()
        audit(conn, ctx.company_id, ctx.user["id"], "update", "collection",
              None, {"key": key, "bytes": size})
    return {"key": key, "updated_at": row["updated_at"].isoformat()}
