"""POST /companies/{id}/import/backup — one-time ingestion of the app's
Export-backup JSON (the localStorage snapshot).

Deliberately lenient: the export's record shapes evolved over months, so every
field access is guarded, unknown keys are skipped (and reported), and the whole
import runs in a single transaction — either everything lands or nothing does.

Accepted body: the export payload itself, or anything wrapping it in a "data"
object. Keys may or may not carry the "inspectit." prefix.
"""
import re
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends
from psycopg.types.json import Jsonb

from ..db import audit, get_pool
from ..permissions import AuthContext, require
from ..storage import parse_data_url, store_file

router = APIRouter(prefix="/companies/{company_id}", tags=["import"])

_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")

OPTION_LIST_MAP = {
    "types": "project_types",
    "contractorTypes": "contractor_types",
    "costCategories": "cost_categories",
    "permitTypes": "permit_types",
    "finalTypes": "final_types",
    "scopeTypes": "scope_types",
    "paymentTypes": "payment_types",
}


def _date(v) -> Any:
    if isinstance(v, str):
        m = _DATE_RE.match(v.strip())
        if m:
            return m.group(1)
    return None


def _money(v) -> Any:
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    if isinstance(v, str):
        cleaned = re.sub(r"[^0-9.\-]", "", v)
        try:
            return round(float(cleaned), 2)
        except ValueError:
            return None
    return None


def _int(v) -> Any:
    try:
        return int(float(str(v).replace(",", "")))
    except (ValueError, TypeError):
        return None


def _first(d: dict, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return default


class Importer:
    def __init__(self, conn, ctx: AuthContext):
        self.conn = conn
        self.ctx = ctx
        self.cid = ctx.company_id
        self.uid = ctx.user["id"]
        self.counts: Dict[str, int] = {}
        self.notes = []
        self.vehicle_ids: Dict[str, str] = {}   # old app id -> uuid
        self.property_ids: Dict[str, str] = {}

    def bump(self, key, n=1):
        self.counts[key] = self.counts.get(key, 0) + n

    # ---------- files ----------

    def file_from_data_url(self, data_url, filename, kind=None,
                           attached_to_type=None, attached_to_id=None):
        parsed = parse_data_url(data_url)
        if not parsed:
            return None
        mime, data = parsed
        fid = store_file(self.conn, self.cid, self.uid, filename, mime, data,
                         kind=kind, attached_to_type=attached_to_type,
                         attached_to_id=attached_to_id)
        self.bump("files")
        return fid

    def import_atts(self, atts, attached_to_type, attached_to_id):
        """App attachment lists: [{name, type, size, dataURL}] -> [file_id]."""
        out = []
        for att in atts if isinstance(atts, list) else []:
            if not isinstance(att, dict):
                continue
            fid = self.file_from_data_url(
                att.get("dataURL"), att.get("name") or "attachment",
                attached_to_type=attached_to_type, attached_to_id=attached_to_id)
            if fid:
                out.append(fid)
        return out

    # ---------- entities ----------

    def import_vehicles(self, items):
        for v in items if isinstance(items, list) else []:
            if not isinstance(v, dict):
                continue
            row = self.conn.execute(
                """INSERT INTO vehicles (company_id, vehicle_id, plate,
                                         make_model, vtype)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (self.cid, str(_first(v, "vehicleId", "id", default="?")),
                 _first(v, "plate"), _first(v, "makeModel", "make_model"),
                 _first(v, "type", "vtype", default="auto")),
            ).fetchone()
            new_id = str(row["id"])
            if v.get("id") is not None:
                self.vehicle_ids[str(v["id"])] = new_id
            photo = self.file_from_data_url(v.get("photo"), "vehicle-photo",
                                            kind="image",
                                            attached_to_type="vehicle",
                                            attached_to_id=new_id)
            if photo:
                self.conn.execute(
                    "UPDATE vehicles SET photo_file_id = %s WHERE id = %s",
                    (photo, new_id))
            self.bump("vehicles")

    def import_properties(self, items):
        for p in items if isinstance(items, list) else []:
            if not isinstance(p, dict):
                continue
            row = self.conn.execute(
                """INSERT INTO properties (company_id, property_id, ptype,
                                           street, city, state, zip)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (self.cid, str(_first(p, "propertyId", "id", default="?")),
                 _first(p, "type", "ptype", default="residential"),
                 _first(p, "street", "address", "streetAddress"),
                 _first(p, "city"), _first(p, "state"), _first(p, "zip")),
            ).fetchone()
            new_id = str(row["id"])
            if p.get("id") is not None:
                self.property_ids[str(p["id"])] = new_id
            photo = self.file_from_data_url(p.get("photo"), "property-photo",
                                            kind="image",
                                            attached_to_type="property",
                                            attached_to_id=new_id)
            if photo:
                self.conn.execute(
                    "UPDATE properties SET photo_file_id = %s WHERE id = %s",
                    (photo, new_id))
            self.bump("properties")

    # ---------- per-entity record maps  {old_entity_id: [records]} ----------

    def _entity_cols(self, kind, new_id):
        return ("vehicle_id" if kind == "vehicle" else "property_id"), new_id

    def _iter_entity_records(self, data, kind):
        """Yields (new_entity_uuid, record) from an object map keyed by old id."""
        idmap = self.vehicle_ids if kind == "vehicle" else self.property_ids
        if not isinstance(data, dict):
            return
        for old_id, records in data.items():
            new_id = idmap.get(str(old_id))
            if not new_id:
                self.notes.append(f"skipped records for unknown {kind} {old_id}")
                continue
            if isinstance(records, list):
                for rec in records:
                    if isinstance(rec, dict):
                        yield new_id, rec

    def import_inspections(self, data, kind):
        for new_id, rec in self._iter_entity_records(data, kind):
            insp = rec.get("inspection") if isinstance(rec.get("inspection"), dict) else {}
            sig = self.file_from_data_url(insp.get("signature"),
                                          "signature.png", kind="signature")
            col, val = self._entity_cols(kind, new_id)
            self.conn.execute(
                f"""INSERT INTO inspections (company_id, kind, {col}, inspected_at,
                       inspector_name, template_key, odometer, overall_condition,
                       results, signature_file_id)
                    VALUES (%s, %s, %s, COALESCE(%s::date, CURRENT_DATE),
                            %s, %s, %s, %s, %s, %s)""",
                (self.cid, kind, val,
                 _date(_first(rec, "date", "inspected_at",
                              default=_first(insp, "date"))),
                 _first(insp, "inspector", "inspectorName",
                        default=_first(rec, "inspector")),
                 _first(rec, "template", "templateKey"),
                 _int(_first(rec, "odometer", default=_first(insp, "odometer"))),
                 _first(rec, "overall", "overallCondition",
                        default=_first(insp, "overall")),
                 Jsonb(rec), sig),
            )
            self.bump(f"{kind}_inspections")

    def import_tickets(self, data, kind):
        for new_id, t in self._iter_entity_records(data, kind):
            col, val = self._entity_cols(kind, new_id)
            row = self.conn.execute(
                f"""INSERT INTO repair_tickets (company_id, kind, {col},
                        ticket_date, description, status, odometer, cost, details)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (self.cid, kind, val, _date(_first(t, "date", "ticketDate")),
                 _first(t, "desc", "description", "issue"),
                 _first(t, "status"), _int(t.get("odometer")),
                 _money(t.get("cost")), Jsonb(t)),
            ).fetchone()
            self.import_atts(t.get("attachments"), "repair_ticket",
                             str(row["id"]))
            self.bump(f"{kind}_tickets")

    def import_maintenance_state(self, data, kind):
        idmap = self.vehicle_ids if kind == "vehicle" else self.property_ids
        if not isinstance(data, dict):
            return
        for old_id, items in data.items():
            new_id = idmap.get(str(old_id))
            if not new_id or not isinstance(items, dict):
                continue
            col, val = self._entity_cols(kind, new_id)
            for item_key, state in items.items():
                if item_key == "__odo__":   # vehicle current-odometer override
                    odo = _int(state)
                    if odo is not None and kind == "vehicle":
                        self.conn.execute(
                            "UPDATE vehicles SET current_odometer = %s WHERE id = %s",
                            (odo, new_id))
                    continue
                if isinstance(state, dict):   # vehicle shape {d, o}
                    last_done, odo_at = _date(state.get("d")), _int(state.get("o"))
                else:                          # property shape: bare ISO string
                    last_done, odo_at = _date(state), None
                if not last_done:
                    continue
                self.conn.execute(
                    f"""INSERT INTO maintenance_state (company_id, kind, {col},
                            item_key, last_done, odometer_at)
                        VALUES (%s, %s, %s, %s, %s, %s)""",
                    (self.cid, kind, val, item_key, last_done, odo_at))
                self.bump(f"{kind}_maintenance_items")

    def import_maintenance_spend(self, data, kind):
        for new_id, entry in self._iter_entity_records(data, kind):
            spend_date = _date(entry.get("date"))
            cost = _money(entry.get("cost"))
            if spend_date is None or cost is None:
                continue
            col, val = self._entity_cols(kind, new_id)
            self.conn.execute(
                f"""INSERT INTO maintenance_spend (company_id, kind, {col},
                        item_key, spend_date, cost, odometer)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (self.cid, kind, val, str(entry.get("item") or "?"),
                 spend_date, cost, _int(entry.get("odo"))))
            self.bump(f"{kind}_spend")

    def import_maintenance_templates(self, data, kind):
        items = (data.items() if isinstance(data, dict)
                 else enumerate(data) if isinstance(data, list) else [])
        for key, tmpl in items:
            if not isinstance(tmpl, dict):
                continue
            self.conn.execute(
                """INSERT INTO maintenance_schedules (company_id, kind, label,
                                                      template)
                   VALUES (%s, %s, %s, %s)""",
                (self.cid, kind,
                 str(_first(tmpl, "label", "name", default=key)), Jsonb(tmpl)))
            self.bump(f"{kind}_maintenance_templates")

    def import_warranties(self, data, kind):
        for new_id, w in self._iter_entity_records(data, kind):
            col, val = self._entity_cols(kind, new_id)
            row = self.conn.execute(
                f"""INSERT INTO warranties (company_id, kind, {col}, item, vendor,
                        purchase_date, warranty_exp, last_serviced, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (self.cid, kind, val, str(_first(w, "item", default="?")),
                 _first(w, "vendor"), _date(w.get("purchaseDate")),
                 _date(w.get("warrantyExp")), _date(w.get("lastServiced")),
                 _first(w, "notes")),
            ).fetchone()
            self.import_atts(w.get("attachments"), "warranty", str(row["id"]))
            self.bump(f"{kind}_warranties")

    # ---------- projects ----------

    def _section_rows(self, rows, project_id, type_key):
        """Normalize a project section list; atts -> stored file ids."""
        out = []
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict):
                continue
            clean = {k: v for k, v in r.items() if k not in ("atts", "attachments")}
            clean.setdefault(type_key, r.get("label"))
            clean["file_ids"] = self.import_atts(
                r.get("atts") or r.get("attachments"), "project", project_id)
            out.append(clean)
        return out

    def import_projects(self, items):
        for pr in items if isinstance(items, list) else []:
            if not isinstance(pr, dict):
                continue
            prop_uuid = self.property_ids.get(str(pr.get("propertyId")))
            if not prop_uuid:
                self.notes.append(
                    f"skipped project '{pr.get('name')}' — unknown property")
                continue
            status = pr.get("status")
            if status not in ("active", "hold", "done"):
                status = "active"
            row = self.conn.execute(
                """INSERT INTO projects (company_id, property_id, name,
                        project_date, goals, project_type, status,
                        initial_budget, revised_budget)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (self.cid, prop_uuid, str(_first(pr, "name", default="Project")),
                 _date(pr.get("date")), _first(pr, "goals"),
                 _first(pr, "type"), status,
                 _money(pr.get("initialBudget")), _money(pr.get("revisedBudget"))),
            ).fetchone()
            pid = str(row["id"])

            for e in pr.get("estimates") or []:
                if not isinstance(e, dict):
                    continue
                self.conn.execute(
                    """INSERT INTO project_estimates (company_id, project_id,
                            category, description, amount)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (self.cid, pid, _first(e, "category", "label"),
                     _first(e, "desc", "description"), _money(e.get("amount"))))
            for p in pr.get("payments") or []:
                if not isinstance(p, dict):
                    continue
                receipt_ids = self.import_atts(p.get("atts"), "project", pid)
                self.conn.execute(
                    """INSERT INTO project_payments (company_id, project_id,
                            description, amount, due_date, payment_type, paid,
                            paid_date, receipt_file_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (self.cid, pid, _first(p, "desc", "description"),
                     _money(p.get("amount")), _date(p.get("due")),
                     _first(p, "ptype"), bool(p.get("paid")),
                     _date(p.get("due")) if p.get("paid") else None,
                     receipt_ids[0] if receipt_ids else None))

            sections = {
                "scope": self._section_rows(pr.get("scope"), pid, "stype"),
                "contractors": [],
                "permits": self._section_rows(pr.get("permits"), pid, "ptype"),
                "final_records": self._section_rows(
                    pr.get("finalRecords"), pid, "rtype"),
            }
            for c in pr.get("contractors") or []:
                if not isinstance(c, dict):
                    continue
                docs = {}
                for slot, atts in (c.get("docs") or {}).items():
                    docs[slot] = self.import_atts(atts, "project", pid)
                clean = {k: v for k, v in c.items() if k != "docs"}
                clean["docs"] = docs
                sections["contractors"].append(clean)
            self.conn.execute(
                "UPDATE projects SET sections = %s WHERE id = %s",
                (Jsonb(sections), pid))
            self.bump("projects")

    def import_option_lists(self, meta):
        if not isinstance(meta, dict):
            return
        for app_key, list_key in OPTION_LIST_MAP.items():
            values = meta.get(app_key)
            if isinstance(values, list) and values:
                self.conn.execute(
                    """INSERT INTO option_lists (company_id, list_key, items)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (company_id, list_key)
                       DO UPDATE SET items = EXCLUDED.items""",
                    (self.cid, list_key, [str(v) for v in values]))
                self.bump("option_lists")

    def import_profile(self, profile):
        if not isinstance(profile, dict):
            return
        updates = {
            "name": _first(profile, "company", "companyName"),
            "address": _first(profile, "address"),
            "city": _first(profile, "city"),
            "state": _first(profile, "state"),
            "zip": _first(profile, "zip"),
            "phone": _first(profile, "phone"),
            "email": _first(profile, "email"),
        }
        updates = {k: v for k, v in updates.items() if v}
        if updates:
            sets = ", ".join(f"{k} = %s" for k in updates)
            self.conn.execute(
                f"UPDATE companies SET {sets} WHERE id = %s",
                (*updates.values(), self.cid))
            self.bump("company_profile")


@router.post("/import/backup")
def import_backup(body: dict = Body(...),
                  ctx: AuthContext = Depends(require("company", "edit"))):
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    # Strip the "inspectit." prefix so both key styles work.
    data = {k.split("inspectit.", 1)[-1]: v for k, v in data.items()}

    with get_pool().connection() as conn:   # one transaction: all-or-nothing
        imp = Importer(conn, ctx)
        imp.import_profile(data.get("profile"))
        imp.import_vehicles(data.get("vehicles"))
        imp.import_properties(data.get("properties"))
        imp.import_inspections(data.get("inspections"), "vehicle")
        imp.import_inspections(data.get("propertyInspections"), "property")
        imp.import_tickets(data.get("tickets"), "vehicle")
        imp.import_tickets(data.get("propertyTickets"), "property")
        imp.import_maintenance_state(data.get("vehicleMaintenance"), "vehicle")
        imp.import_maintenance_state(data.get("propertyMaintenance"), "property")
        imp.import_maintenance_spend(data.get("vehicleMaintSpend"), "vehicle")
        imp.import_maintenance_spend(data.get("propertyMaintSpend"), "property")
        imp.import_maintenance_templates(
            data.get("vehicleMaintTemplates"), "vehicle")
        imp.import_maintenance_templates(
            data.get("propertyMaintTemplates"), "property")
        imp.import_warranties(data.get("vehicleWarranties"), "vehicle")
        imp.import_warranties(data.get("propertyWarranties"), "property")
        imp.import_projects(data.get("projects"))
        imp.import_option_lists(data.get("projectMeta"))
        for key, value in data.items():   # per-type diagram graphics
            if key.startswith("diagram.") and isinstance(value, str):
                imp.file_from_data_url(value, f"{key}.png", kind="diagram")
        handled = {"profile", "vehicles", "properties", "inspections",
                   "propertyInspections", "tickets", "propertyTickets",
                   "vehicleMaintenance", "propertyMaintenance",
                   "vehicleMaintSpend", "propertyMaintSpend",
                   "vehicleMaintTemplates", "propertyMaintTemplates",
                   "vehicleWarranties", "propertyWarranties", "projects",
                   "projectMeta", "account", "session", "users"}
        for key in data:
            if key not in handled and not key.startswith("diagram."):
                imp.notes.append(f"unrecognized key skipped: {key}")
        audit(conn, ctx.company_id, ctx.user["id"], "create", "import", None,
              {"counts": imp.counts})
        return {"imported": imp.counts, "notes": imp.notes}
