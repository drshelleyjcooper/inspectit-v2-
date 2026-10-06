"""Calendar reminders: due items computed from the app's synced collections,
rendered as an ICS feed (RFC 5545) that Google / Apple / Outlook subscribe to.

One-way only: the feed is a read-only snapshot. Each calendar app re-downloads
the whole file when it polls, so an item that is done disappears and a moved
due date moves the existing event (stable UIDs in Detailed mode).

Due-date rules MIRROR the app (web/inspectit-app.html) — keep them in sync:
  pmAddInterval / pmNextSeason / pmStatus / vehStatus   -> maintenance
  inspSchedStatus                                         -> inspection schedules
  ticketDue                                               -> repair tickets
  warrStatus                                              -> warranties
tests/test_calendar.py checks the built-in templates against the app file.

Links: /cal/<token>.ics where token = b64url(feed id) + b64url(HMAC(secret,
id:nonce)). No user id, email or company is in the URL, and the database alone
(id + nonce, no secret) can't rebuild a link.
"""
import base64
import datetime as dt
import hashlib
import hmac
import json
import re
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from . import config

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

BUILTINS = json.loads((Path(__file__).parent / "maint_builtins.json").read_text())

# category -> the permission modules that feed it (each needs company-scope view)
CATEGORY_MODULES = {
    "maintenance": ("vehicle_maintenance", "property_maintenance"),
    "inspections": ("vehicle_inspections", "property_inspections"),
    "repairs": ("vehicle_repairs", "property_repairs"),
    "projects": ("projects",),
    "warranties": ("vehicle_warranties", "property_warranties"),
    "registration": ("vehicle_maintenance",),
}
CATEGORIES = list(CATEGORY_MODULES)

DEFAULT_PREFS = {
    "categories": {"maintenance": True, "inspections": True, "repairs": True,
                   "projects": True, "warranties": False,
                   "registration": False},
    "detail": "basic",          # basic | detailed
    "alert_days": 7,            # -1 = no alert, 0 = same day, 1, 7
    "show_overdue": True,       # overdue items sit on today until done
    "filter": {"mode": "all", "vehicles": [], "properties": []},
    "tz": "UTC",
}
ALERT_CHOICES = (-1, 0, 1, 7)
MAX_FILTER_IDS = 5000

PM_SOON_DAYS = 14
VEH_SOON_MILES = 500
TICKET_OVERDUE_DAYS = 14
DATE_FREQS = ("weekly", "monthly", "quarterly", "semiannual", "annual")
SEASON_START = {"spring": (3, 1), "summer": (6, 1), "fall": (9, 1),
                "winter": (12, 1)}

# The collections a feed reads (app K-key names, as stored in app_collections).
SOURCE_KEYS = [
    "vehicles", "properties", "projects",
    "inspections", "propertyInspections",
    "vehicleInspSchedule", "propertyInspSchedule",
    "tickets", "propertyTickets",
    "vehicleMaintenance", "vehicleMaintTemplates",
    "propertyMaintenance", "propertyMaintTemplates",
    "vehicleWarranties", "propertyWarranties",
]


# ---------------------------------------------------------------- prefs ----

def allowed_categories(ctx) -> dict:
    """{category: [modules the member may view company-wide]} — only the
    categories with at least one such module. Role limits apply here."""
    out = {}
    for cat, mods in CATEGORY_MODULES.items():
        ok = [m for m in mods if ctx.grant_scope(m, "view") == "company"]
        if ok:
            out[cat] = ok
    return out


def _valid_tz(name) -> bool:
    if not isinstance(name, str) or not name or len(name) > 64 or ZoneInfo is None:
        return False
    try:
        ZoneInfo(name)
        return True
    except Exception:
        return False


def normalize_prefs(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    p = json.loads(json.dumps(DEFAULT_PREFS))
    cats = raw.get("categories")
    if isinstance(cats, dict):
        for c in CATEGORIES:
            if c in cats:
                p["categories"][c] = bool(cats[c])
    if raw.get("detail") in ("basic", "detailed"):
        p["detail"] = raw["detail"]
    try:
        a = int(raw.get("alert_days", p["alert_days"]))
        if a in ALERT_CHOICES:
            p["alert_days"] = a
    except (TypeError, ValueError):
        pass
    if "show_overdue" in raw:
        p["show_overdue"] = bool(raw["show_overdue"])
    f = raw.get("filter")
    if isinstance(f, dict):
        if f.get("mode") in ("all", "selected"):
            p["filter"]["mode"] = f["mode"]
        for k in ("vehicles", "properties"):
            ids = f.get(k)
            if isinstance(ids, list):
                p["filter"][k] = [str(x)[:100] for x in ids
                                  if isinstance(x, (str, int))][:MAX_FILTER_IDS]
    if _valid_tz(raw.get("tz")):
        p["tz"] = raw["tz"]
    return p


def today_in(tz_name) -> dt.date:
    now = dt.datetime.now(dt.timezone.utc)
    if _valid_tz(tz_name):
        now = now.astimezone(ZoneInfo(tz_name))
    return now.date()


# ---------------------------------------------------------------- token ----

def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _mac(feed_id, nonce: str) -> bytes:
    return hmac.new(config.CALENDAR_SECRET.encode(),
                    f"cal:{feed_id}:{nonce}".encode(),
                    hashlib.sha256).digest()[:24]


def new_nonce() -> str:
    import secrets
    return secrets.token_hex(16)


def make_token(feed_id, nonce: str) -> str:
    return _b64u(uuid.UUID(str(feed_id)).bytes) + _b64u(_mac(feed_id, nonce))


def parse_token(token: str):
    """-> (feed_id uuid, mac bytes) or None. Never raises."""
    if not isinstance(token, str) or len(token) != 54 \
            or not re.fullmatch(r"[A-Za-z0-9_-]+", token):
        return None
    try:
        fid = uuid.UUID(bytes=_unb64u(token[:22]))
        mac = _unb64u(token[22:])
    except Exception:
        return None
    return (fid, mac) if len(mac) == 24 else None


def token_matches(feed_id, nonce: str, mac: bytes) -> bool:
    return hmac.compare_digest(_mac(feed_id, nonce), mac)


def feed_links(feed_id, nonce: str) -> dict:
    """Subscription links for the settings screen. Built from APP_BASE_URL,
    never from the request's Host header."""
    https = f"{config.APP_BASE_URL}/cal/{make_token(feed_id, nonce)}.ics"
    webcal = re.sub(r"^https?://", "webcal://", https)
    enc = quote(https, safe="")
    return {
        "url": https,
        "webcal": webcal,
        "google": "https://calendar.google.com/calendar/r?cid=" + quote(webcal, safe=""),
        "outlook": f"https://outlook.live.com/calendar/0/addfromweb?url={enc}&name=Inspectit.app",
        "outlook_work": f"https://outlook.office.com/calendar/0/addfromweb?url={enc}&name=Inspectit.app",
    }


# ----------------------------------------------------- due-date engine ----

def parse_iso(s) -> Optional[dt.date]:
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s if isinstance(s, str) else "")
    if not m:
        return None
    try:
        return dt.date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


def _add_months(d: dt.date, n: int) -> dt.date:
    """JS Date.setMonth semantics: day overflow rolls into the next month
    (Jan 31 + 1 month = Mar 3, Feb 29 + 12 months = Mar 1)."""
    y, m0 = divmod(d.month - 1 + n, 12)
    return dt.date(d.year + y, m0 + 1, 1) + dt.timedelta(days=d.day - 1)


def add_interval(last: dt.date, freq: str) -> Optional[dt.date]:
    if last is None:
        return None
    if freq == "weekly":
        return last + dt.timedelta(days=7)
    months = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}.get(freq)
    return _add_months(last, months) if months else None


def next_season(season: str, after: Optional[dt.date], today: dt.date) -> dt.date:
    mo, day = SEASON_START.get(season, SEASON_START["spring"])
    if after:
        y = after.year
        cand = dt.date(y, mo, day)
        while cand <= after:
            y += 1
            cand = dt.date(y, mo, day)
    else:
        y = today.year
        cand = dt.date(y, mo, day)
        while cand < today:
            y += 1
            cand = dt.date(y, mo, day)
    return cand


def _soon_or_ok(due, today):
    if due is None:
        return "ok"
    return "soon" if due <= today + dt.timedelta(days=PM_SOON_DAYS) else "ok"


def pm_status(item: dict, last_iso, today: dt.date) -> dict:
    """Mirror of the app's pmStatus. -> {state, due}"""
    freq = item.get("freq") or "annual"
    last = parse_iso(last_iso)
    if freq.startswith("season:"):
        due = next_season(freq[7:], last, today)
        if not last_iso:
            return {"state": "never", "due": due}
        if due < today:
            return {"state": "overdue", "due": due}
        return {"state": _soon_or_ok(due, today), "due": due}
    if not last_iso:
        return {"state": "never", "due": None}
    due = add_interval(last, freq)
    if due and due < today:
        return {"state": "overdue", "due": due}
    return {"state": _soon_or_ok(due, today), "due": due}


def _num(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def veh_status(item: dict, val, odo: Optional[float], today: dt.date) -> dict:
    """Mirror of the app's vehStatus. -> {state, date_due, miles_due}"""
    freq = item.get("freq")
    has_date = bool(freq) and freq != "none"
    miles = _num(item.get("miles"))
    has_miles = miles is not None and miles > 0
    val = val if isinstance(val, dict) else {}
    d = val.get("d") or ""
    o = _num(val.get("o"))
    states, date_due, miles_due = [], None, None
    if has_date:
        ds = pm_status(item, d, today)
        date_due = ds["due"]
        states.append(ds["state"])
    if has_miles:
        if o is None:
            states.append("never")
        else:
            miles_due = o + miles
            if odo is None:
                states.append("ok")
            elif odo >= miles_due:
                states.append("overdue")
            elif miles_due - odo <= VEH_SOON_MILES:
                states.append("soon")
            else:
                states.append("ok")
    for s in ("overdue", "never", "soon"):
        if s in states:
            state = s
            break
    else:
        state = "ok" if states else "never"
    return {"state": state, "date_due": date_due, "miles_due": miles_due}


def vehicle_odometer(state_map: dict, inspections: list) -> Optional[float]:
    """Mirror of the vehicle scheduler's ctx(): manual override, else the most
    recent inspection that recorded an odometer."""
    override = state_map.get("__odo__") if isinstance(state_map, dict) else None
    if override is not None and override != "":
        return _num(override)
    for r in inspections or []:
        if isinstance(r, dict) and r.get("odometer") is not None:
            return _num(r["odometer"])
    return None


def insp_sched_status(sched: dict, inspections: list, odo, today: dt.date,
                      vehicle: bool) -> Optional[dict]:
    """Mirror of the app's inspSchedStatus. Next inspection due by date
    (last inspection + interval, or the start date if none yet) and/or by
    mileage (vehicles: last inspection odometer + interval)."""
    if not isinstance(sched, dict):
        return None
    freq = sched.get("freq") if sched.get("freq") in DATE_FREQS else None
    miles = _num(sched.get("miles")) if vehicle else None
    if miles is not None and miles <= 0:
        miles = None
    if not freq and miles is None:
        return None
    last = next((r for r in inspections or [] if isinstance(r, dict)), None)
    last_date = None
    if last:
        last_date = parse_iso(last.get("date")) or parse_iso(str(last.get("savedAt") or "")[:10])
    date_due = miles_due = None
    states = []
    if freq:
        date_due = add_interval(last_date, freq) if last_date else (parse_iso(sched.get("start")) or today)
        states.append("overdue" if date_due < today else _soon_or_ok(date_due, today))
    if miles is not None:
        last_odo = _num(last.get("odometer")) if last else None
        if last_odo is not None:
            miles_due = last_odo + miles
            if odo is not None and odo >= miles_due:
                states.append("overdue")
            elif odo is not None and miles_due - odo <= VEH_SOON_MILES:
                states.append("soon")
            else:
                states.append("ok")
    if not states:
        return None
    state = next((s for s in ("overdue", "soon") if s in states), "ok")
    return {"state": state, "date_due": date_due, "miles_due": miles_due}


def ticket_due(t: dict) -> Optional[dt.date]:
    """Mirror of the app's ticketDue: the optional 'Due by' date, else
    TICKET_OVERDUE_DAYS after the ticket date."""
    due = parse_iso(t.get("dueBy"))
    if due:
        return due
    d = parse_iso(t.get("date")) or parse_iso(str(t.get("createdAt") or "")[:10])
    return d + dt.timedelta(days=TICKET_OVERDUE_DAYS) if d else None


def is_compliance(group: str, name: str) -> bool:
    return (str(group).strip().lower() == "compliance"
            or bool(re.search(r"registration|renewal", str(name), re.I)))


# ------------------------------------------------------ item collection ----

def _as_json(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _dict(v):
    v = _as_json(v)
    return v if isinstance(v, dict) else {}


def _list(v):
    v = _as_json(v)
    return v if isinstance(v, list) else []


def _never_done(val) -> bool:
    if isinstance(val, dict):      # vehicle: {d: date, o: odometer}
        return not val.get("d") and val.get("o") in (None, "")
    return not val                 # property: "YYYY-MM-DD"


def _template_for(state_map, saved_templates, builtin):
    name = state_map.get("__tmpl__") if isinstance(state_map, dict) else None
    t = saved_templates.get(name) if isinstance(name, str) else None
    return t if isinstance(t, dict) and isinstance(t.get("categories"), list) else builtin


def _miles(n) -> str:
    return f"{int(round(n)):,}"


def collect_items(data: dict, cats: dict, prefs: dict, today: dt.date) -> list:
    """All due items for the enabled + allowed categories.
    data: {collection key: value}; cats: allowed_categories() filtered to the
    ones the user switched on. Each item: {cat, side, eid, key, date, overdue,
    entity, what, details[]}."""
    vehicles = {str(v.get("id")): v for v in _list(data.get("vehicles")) if isinstance(v, dict)}
    properties = {str(p.get("id")): p for p in _list(data.get("properties")) if isinstance(p, dict)}
    flt = prefs["filter"]
    sel_v = set(flt["vehicles"]) if flt["mode"] == "selected" else None
    sel_p = set(flt["properties"]) if flt["mode"] == "selected" else None

    def keep(side, eid):
        if side == "vehicle":
            return eid in vehicles and (sel_v is None or eid in sel_v)
        return eid in properties and (sel_p is None or eid in sel_p)

    def label(side, eid):
        if side == "vehicle":
            v = vehicles.get(eid, {})
            return " · ".join(x for x in (str(v.get("vehicleId") or "").strip(),
                                         str(v.get("makeModel") or "").strip()) if x) or "Vehicle"
        p = properties.get(eid, {})
        return " · ".join(x for x in (str(p.get("propertyId") or "").strip(),
                                     str(p.get("address") or "").strip()) if x) or "Property"

    def mods(cat):
        return cats.get(cat) or []

    items = []

    def add(cat, side, eid, key, date, overdue, what, details):
        items.append({"cat": cat, "side": side, "eid": eid, "key": key,
                      "date": date, "overdue": overdue, "entity": label(side, eid),
                      "what": what, "details": details})

    veh_insp = _dict(data.get("inspections"))
    prop_insp = _dict(data.get("propertyInspections"))
    veh_state = _dict(data.get("vehicleMaintenance"))

    def odo_for(eid):
        return vehicle_odometer(_dict(veh_state.get(eid)), _list(veh_insp.get(eid)))

    # --- maintenance + registration/renewal (scheduler items) ---
    want_maint = "maintenance" in cats
    want_reg = "registration" in cats
    for side, mod, skey, tkey, builtin in (
            ("vehicle", "vehicle_maintenance", "vehicleMaintenance",
             "vehicleMaintTemplates", BUILTINS["vehicle"]),
            ("property", "property_maintenance", "propertyMaintenance",
             "propertyMaintTemplates", BUILTINS["property"])):
        m_ok = want_maint and mod in mods("maintenance")
        r_ok = want_reg and mod in mods("registration")
        if not (m_ok or r_ok):
            continue
        states = _dict(data.get(skey))
        saved = _dict(data.get(tkey))
        ents = vehicles if side == "vehicle" else properties
        for eid in ents:
            if not keep(side, eid):
                continue
            sm = _dict(states.get(eid))
            tpl = _template_for(sm, saved, builtin)
            odo = odo_for(eid) if side == "vehicle" else None
            for cat_ in tpl.get("categories") or []:
                if not isinstance(cat_, dict):
                    continue
                group = str(cat_.get("group") or "")
                for it in cat_.get("items") or []:
                    if not isinstance(it, dict):
                        continue
                    name = str(it.get("name") or "Item")
                    cat = "registration" if (side == "vehicle" and is_compliance(group, name)) else "maintenance"
                    if (cat == "maintenance" and not m_ok) or (cat == "registration" and not r_ok):
                        continue
                    key = f"{group}::{name}"
                    val = sm.get(key)
                    # Never done yet ("Never done" in the app): no history to
                    # count from, so nothing goes on the calendar.
                    if _never_done(val):
                        continue
                    details = []
                    if side == "vehicle":
                        st = veh_status(it, val, odo, today)
                        date_due, miles_due = st["date_due"], st["miles_due"]
                        if st["state"] == "overdue":
                            date = date_due if (date_due and date_due < today) else today
                        elif date_due:
                            date = date_due
                        elif st["state"] == "soon":
                            date = today
                        else:
                            continue
                        if miles_due is not None:
                            details.append(f"Due at {_miles(miles_due)} mi"
                                           + (f" (now {_miles(odo)} mi)" if odo is not None else ""))
                        overdue = st["state"] == "overdue"
                    else:
                        st = pm_status(it, val if isinstance(val, str) else "", today)
                        if not st["due"]:
                            continue
                        date, overdue = st["due"], st["state"] == "overdue"
                    add(cat, side, eid, key, date, overdue, name, details)

    # --- inspection schedules ---
    if "inspections" in cats:
        for side, mod, skey, insp in (
                ("vehicle", "vehicle_inspections", "vehicleInspSchedule", veh_insp),
                ("property", "property_inspections", "propertyInspSchedule", prop_insp)):
            if mod not in mods("inspections"):
                continue
            for eid, sched in _dict(data.get(skey)).items():
                if not keep(side, eid):
                    continue
                odo = odo_for(eid) if side == "vehicle" else None
                st = insp_sched_status(_dict(sched), _list(insp.get(eid)), odo, today,
                                       side == "vehicle")
                if not st:
                    continue
                details = []
                if st["miles_due"] is not None:
                    details.append(f"Due at {_miles(st['miles_due'])} mi"
                                   + (f" (now {_miles(odo)} mi)" if odo is not None else ""))
                if st["state"] == "overdue":
                    date = st["date_due"] if (st["date_due"] and st["date_due"] < today) else today
                elif st["date_due"]:
                    date = st["date_due"]
                elif st["state"] == "soon":
                    date = today
                else:
                    continue
                add("inspections", side, eid, "inspection", date,
                    st["state"] == "overdue", "Inspection", details)

    # --- repair tickets ---
    if "repairs" in cats:
        for side, mod, skey in (("vehicle", "vehicle_repairs", "tickets"),
                                ("property", "property_repairs", "propertyTickets")):
            if mod not in mods("repairs"):
                continue
            for eid, lst in _dict(data.get(skey)).items():
                if not keep(side, eid):
                    continue
                for t in _list(lst):
                    if not isinstance(t, dict) or t.get("status") == "complete":
                        continue
                    due = ticket_due(t)
                    if not due:
                        continue
                    details = [f"Ticket {t.get('id')}"] if t.get("id") else []
                    desc = str(t.get("description") or "").strip()
                    if desc:
                        details.append(desc[:300] + ("…" if len(desc) > 300 else ""))
                    if t.get("vendor"):
                        details.append(f"Vendor: {t['vendor']}")
                    if not t.get("dueBy"):
                        details.append(f"No due date set — flagged {TICKET_OVERDUE_DAYS} days after the ticket date")
                    add("repairs", side, eid, f"ticket::{t.get('id')}", due,
                        due < today, "Repair ticket", details)

    # --- warranties (stay on their expiry date; never moved to today) ---
    if "warranties" in cats:
        for side, mod, skey in (("vehicle", "vehicle_warranties", "vehicleWarranties"),
                                ("property", "property_warranties", "propertyWarranties")):
            if mod not in mods("warranties"):
                continue
            for eid, lst in _dict(data.get(skey)).items():
                if not keep(side, eid):
                    continue
                for w in _list(lst):
                    if not isinstance(w, dict):
                        continue
                    exp = parse_iso(w.get("warrantyExp"))
                    if not exp:
                        continue
                    details = [f"Vendor: {w['vendor']}"] if w.get("vendor") else []
                    add("warranties", side, eid, f"warranty::{w.get('id')}", exp,
                        False, f"Warranty: {w.get('item') or 'item'}", details)

    # --- project payment due dates ---
    if "projects" in cats and "projects" in mods("projects"):
        for pr in _list(data.get("projects")):
            if not isinstance(pr, dict) or pr.get("status") == "done":
                continue
            eid = str(pr.get("propertyId") or "")
            if not keep("property", eid):
                continue
            for pay in pr.get("payments") or []:
                if not isinstance(pay, dict) or pay.get("paid"):
                    continue
                due = parse_iso(pay.get("due"))
                if not due:
                    continue
                details = [f"Project: {pr.get('name') or 'Untitled'}"]
                if pay.get("desc"):
                    details.append(str(pay["desc"]))
                amt = _num(pay.get("amount"))
                if amt:
                    details.append(f"Amount: ${amt:,.2f}")
                add("projects", "property", eid, f"payment::{pr.get('id')}::{pay.get('id')}",
                    due, due < today, "Project payment", details)
    return items


# ------------------------------------------------------------------ ICS ----

BASIC_TITLE = {
    ("maintenance", "vehicle"): "Vehicle maintenance due",
    ("maintenance", "property"): "Property maintenance due",
    ("registration", "vehicle"): "Vehicle registration/renewal due",
    ("inspections", "vehicle"): "Vehicle inspection due",
    ("inspections", "property"): "Property inspection due",
    ("repairs", "vehicle"): "Vehicle repair due",
    ("repairs", "property"): "Property repair due",
    ("projects", "property"): "Project payment due",
    ("warranties", "vehicle"): "Vehicle warranty expires",
    ("warranties", "property"): "Property warranty expires",
}
CAT_LABEL = {"maintenance": "Maintenance", "registration": "Registration/Renewal",
             "inspections": "Inspection", "repairs": "Repair",
             "projects": "Project", "warranties": "Warranty"}
ALARM_TRIGGER = {7: "-P6DT15H", 1: "-PT15H", 0: "PT9H"}   # 9 AM local


def _esc(s: str) -> str:
    return (str(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n"))


def _fold(line: str) -> str:
    """RFC 5545 §3.1: lines over 75 octets are folded (CRLF + space), never
    splitting a UTF-8 sequence."""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > 75:
            out.append(cur)
            cur, size = " " + ch, 1 + n
        else:
            cur += ch
            size += n
    out.append(cur)
    return "\r\n".join(out)


def _uid(company_id, *parts) -> str:
    h = hashlib.sha256("|".join([str(company_id)] + [str(p) for p in parts]).encode())
    return h.hexdigest()[:32] + "@inspectit.app"


def build_events(items: list, prefs: dict, today: dt.date, company_id) -> list:
    """-> list of event dicts {uid, date, summary, description, category, alarm}"""
    app_url = f"{config.APP_BASE_URL}/web/inspectit-app.html"
    events = []
    # "Show overdue items on today": an overdue item moves to today until it's
    # done. Warranties aren't tasks, so an expired one stays on its date.
    placed = []
    for it in items:
        if it["overdue"] and prefs["show_overdue"] and it["cat"] != "warranties" \
                and it["date"] < today:
            it = dict(it, date=today)
        placed.append(it)
    alarm = ALARM_TRIGGER.get(prefs["alert_days"])

    if prefs["detail"] == "detailed":
        for it in placed:
            expired = it["cat"] == "warranties" and it["date"] < today
            prefix = "Overdue: " if it["overdue"] else ("Expired: " if expired else "")
            what = it["what"]
            if it["cat"] == "warranties":
                what += " expired" if expired else " expires"
            lines = [f"Due: {it['date'].isoformat()}" if not it["overdue"] else "Overdue — shown on today until it's done"]
            lines += it["details"] + [f"Open Inspectit.app: {app_url}"]
            events.append({
                "uid": _uid(company_id, it["cat"], it["side"], it["eid"], it["key"]),
                "date": it["date"],
                "summary": f"{prefix}{it['entity']} — {what}",
                "description": "\n".join(lines),
                "category": CAT_LABEL[it["cat"]],
                "alarm": None if (it["overdue"] or it["date"] <= today) else alarm,
            })
        return events

    # Basic: one event per (category, side, day, overdue), no names/addresses.
    groups = {}
    for it in placed:
        k = (it["cat"], it["side"], it["date"], it["overdue"])
        groups[k] = groups.get(k, 0) + 1
    for (cat, side, date, overdue), n in sorted(groups.items(), key=lambda kv: (kv[0][2], kv[0][0], kv[0][1])):
        title = BASIC_TITLE.get((cat, side), CAT_LABEL[cat] + " due")
        if cat == "warranties" and date < today:
            title = title.replace("expires", "expired")
        summary = ("Overdue: " if overdue else "") + title + (f" ({n} items)" if n > 1 else "")
        events.append({
            "uid": _uid(company_id, "basic", cat, side, date.isoformat(), overdue),
            "date": date,
            "summary": summary,
            "description": f"Open Inspectit.app for details: {app_url}",
            "category": CAT_LABEL[cat],
            "alarm": None if (overdue or date <= today) else alarm,
        })
    return events


def render_ics(events: list, stamp: dt.datetime, paused: bool = False) -> str:
    st = stamp.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = "Inspectit.app reminders" + (" (account paused)" if paused else "")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//Inspectit.app//Reminders 1.0//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_esc(name)}",
        "X-WR-CALDESC:Due dates from Inspectit.app (read-only)",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]
    for e in events:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{e['uid']}",
            f"DTSTAMP:{st}",
            f"DTSTART;VALUE=DATE:{e['date'].strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(e['date'] + dt.timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{_esc(e['summary'])}",
            f"DESCRIPTION:{_esc(e['description'])}",
            f"CATEGORIES:{_esc(e['category'])}",
            "TRANSP:TRANSPARENT",
            "STATUS:CONFIRMED",
        ]
        if e.get("alarm"):
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY",
                      f"DESCRIPTION:{_esc(e['summary'])}",
                      f"TRIGGER:{e['alarm']}", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"
