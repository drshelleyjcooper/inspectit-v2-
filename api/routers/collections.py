"""Collection-level sync for the existing single-file app (phase 2).

The app keeps localStorage as its working cache and mirrors each data key
here: pull on boot, push on change. Keys are the app's K-keys without the
"inspectit." prefix. Concurrency: PUT may send base_updated_at; a mismatch
returns 409 with the server copy (the app then takes the server version).

Permission mapping: each key belongs to a module; GET needs module:view,
PUT needs module:edit — and both require a company-scope grant, because a
whole-collection blob can't be filtered per assignment. (Assigned-scope
roles like inspectors will use the per-record API when that lands.)

Delete (USER-ROLES-SPEC §4.1: Company Administrator only): a whole-collection
write can't say "I removed a record" — until 2026-09-09 it didn't have to,
and every role with module:edit could erase inspections, tickets and the
rest by writing the collection back without them. PUT now diffs the stored
copy against the incoming one for the record-bearing keys in DELETE_TRACKED
and requires module:delete when anything is gone (spec §11).
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

# Keys whose contents are RECORDS in the §4.1 sense — an inspection report, a
# repair ticket, a warranty, a spend entry, a vehicle, a project and its
# sub-records. Removing one is a delete. Not listed, and so still an edit:
# the maintenance state maps (clearing "last done" is an ordinary control),
# saved schedule templates, diagrams, profile and projectMeta.
DELETE_TRACKED = {
    "vehicles", "inspections", "tickets", "vehicleMaintSpend",
    "vehicleWarranties",
    "properties", "propertyInspections", "propertyTickets",
    "propertyMaintSpend", "propertyWarranties",
    "projects",
}


def _as_json(v):
    """The app's export format stores some values as JSON strings."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _has_ids(items) -> bool:
    return any(isinstance(x, dict) and "id" in x for x in items)


def _removed_nested(old_rec, new_rec) -> int:
    """Inside a matched record, only id-bearing child lists count as records
    (a project's payments, contractors, permits …). Id-less child lists —
    attachments, photos — are part of editing the record."""
    n = 0
    for k, v in old_rec.items():
        if isinstance(v, list) and _has_ids(v):
            nv = new_rec.get(k) if isinstance(new_rec, dict) else None
            n += _removed_records(v, nv if isinstance(nv, list) else [])
    return n


def _removed_records(old, new) -> int:
    """Count records present in `old` that are absent from `new`.

    Shapes seen in practice: a list of id-bearing dicts (vehicles, properties,
    projects, warranties, tickets); a dict keyed by entity id whose values are
    lists (inspections, tickets, spend); and within a record, id-bearing child
    lists. Records with an `id` are matched by it. Lists whose items have no
    id (inspection summaries, spend entries — the app removes those by index)
    count a shorter list as that many deletions; that treats "replace one
    id-less entry with another" as an edit, which is the lenient reading."""
    old, new = _as_json(old), _as_json(new)
    if isinstance(old, dict):
        if not isinstance(new, dict):
            new = {}
        return sum(_removed_records(v, new.get(k)) for k, v in old.items())
    if isinstance(old, list):
        if not isinstance(new, list):
            new = []
        if _has_ids(old):
            new_by_id = {x["id"]: x for x in new
                         if isinstance(x, dict) and "id" in x}
            n = 0
            for x in old:
                if not (isinstance(x, dict) and "id" in x):
                    continue
                if x["id"] not in new_by_id:
                    n += 1
                else:
                    n += _removed_nested(x, new_by_id[x["id"]])
            return n
        return max(0, len(old) - len(new))
    return 0


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
        removed = 0
        if key in DELETE_TRACKED and current is not None:
            removed = _removed_records(current["data"], body.data)
            if removed and ctx.grant_scope(KEY_MODULE[key], "delete") != "company":
                raise HTTPException(
                    403, f"Requires {KEY_MODULE[key]}:delete — this write "
                         f"would remove {removed} record(s), and only "
                         "Company Administrator may delete (§4.1)")
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
        if removed:
            audit(conn, ctx.company_id, ctx.user["id"], "delete", "collection",
                  None, {"key": key, "removed": removed})
    return {"key": key, "updated_at": row["updated_at"].isoformat()}
