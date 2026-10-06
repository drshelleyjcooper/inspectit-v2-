"""Calendar subscription feed: due-date rules (mirroring the app), the private
link, role limits, preferences and the ICS output."""
import datetime as dt
import json
import re

from api import calendar_feed as cf

STATE = {}
D = dt.date


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


# ------------------------------------------------------------ engine ----

def test_builtins_match_app():
    """api/maint_builtins.json must equal the app's built-in templates."""
    s = (cf.Path(__file__).resolve().parent.parent / "web" / "inspectit-app.html").read_text()

    def lit(name):
        i = s.index("const " + name + " = {")
        j = s.index("\n};", i)
        body = s[s.index("{", i):j + 2]
        body = re.sub(r"//[^\n]*", "", body)
        body = re.sub(r"([{,]\s*)([A-Za-z_]\w*)\s*:", r'\1"\2":', body)
        body = re.sub(r",(\s*[}\]])", r"\1", body)
        return json.loads(body)

    assert cf.BUILTINS["property"] == lit("PROP_MAINT_BUILTIN")
    assert cf.BUILTINS["vehicle"] == lit("VEH_MAINT_BUILTIN")


def test_interval_matches_js_month_overflow():
    assert cf.add_interval(D(2026, 1, 31), "monthly") == D(2026, 3, 3)
    assert cf.add_interval(D(2024, 2, 29), "annual") == D(2025, 3, 1)
    assert cf.add_interval(D(2026, 10, 6), "weekly") == D(2026, 10, 13)
    assert cf.add_interval(D(2026, 8, 31), "semiannual") == D(2027, 3, 3)
    assert cf.add_interval(D(2026, 1, 1), "none") is None


def test_pm_status_and_seasons():
    today = D(2026, 10, 6)
    assert cf.pm_status({"freq": "monthly"}, "2026-08-01", today) == {"state": "overdue", "due": D(2026, 9, 1)}
    assert cf.pm_status({"freq": "monthly"}, "2026-09-10", today)["state"] == "soon"
    assert cf.pm_status({"freq": "annual"}, "2026-09-10", today)["state"] == "ok"
    assert cf.pm_status({"freq": "annual"}, "", today) == {"state": "never", "due": None}
    # seasonal: next season start strictly after last done
    assert cf.pm_status({"freq": "season:fall"}, "2025-09-15", today)["due"] == D(2026, 9, 1)
    assert cf.pm_status({"freq": "season:fall"}, "2025-09-15", today)["state"] == "overdue"
    assert cf.pm_status({"freq": "season:spring"}, "", today) == {"state": "never", "due": D(2027, 3, 1)}


def test_vehicle_status_miles_and_dates():
    today = D(2026, 10, 6)
    oil = {"name": "Oil", "freq": "semiannual", "miles": 5000}
    assert cf.veh_status(oil, {"d": "2026-06-01", "o": 80000}, 85100, today)["state"] == "overdue"
    st = cf.veh_status(oil, {"d": "2026-06-01", "o": 80000}, 84600, today)
    assert st["state"] == "soon" and st["miles_due"] == 85000 and st["date_due"] == D(2026, 12, 1)
    assert cf.veh_status({"freq": "none", "miles": 5000}, "", 1000, today)["state"] == "never"


def test_ticket_due_default_14_days():
    assert cf.ticket_due({"date": "2026-09-01"}) == D(2026, 9, 15)
    assert cf.ticket_due({"date": "2026-09-01", "dueBy": "2026-12-01"}) == D(2026, 12, 1)
    assert cf.ticket_due({}) is None


def test_inspection_schedule():
    today = D(2026, 10, 6)
    insp = [{"date": "2026-08-01", "odometer": 50000}]
    st = cf.insp_sched_status({"freq": "monthly"}, insp, None, today, True)
    assert st["state"] == "overdue" and st["date_due"] == D(2026, 9, 1)
    st = cf.insp_sched_status({"miles": 3000}, insp, 52800, today, True)
    assert st["state"] == "soon" and st["miles_due"] == 53000
    # never inspected: due on the start date (or today)
    st = cf.insp_sched_status({"freq": "quarterly", "start": "2026-11-01"}, [], None, today, False)
    assert st["date_due"] == D(2026, 11, 1) and st["state"] == "ok"
    assert cf.insp_sched_status({"freq": "none"}, insp, None, today, True) is None
    # mileage is ignored for properties
    assert cf.insp_sched_status({"miles": 3000}, insp, None, today, False) is None


def test_token_roundtrip_and_tamper():
    fid = "5b3c0f0e-1111-4c2a-9d4e-123456789abc"
    tok = cf.make_token(fid, "n1")
    assert len(tok) == 54
    parsed = cf.parse_token(tok)
    assert str(parsed[0]) == fid and cf.token_matches(fid, "n1", parsed[1])
    assert not cf.token_matches(fid, "n2", parsed[1])      # reset kills old link
    assert cf.parse_token(tok[:-1]) is None
    assert cf.parse_token("../../etc/passwd") is None
    assert fid not in tok


def test_prefs_normalized():
    p = cf.normalize_prefs({"categories": {"warranties": 1, "bogus": True},
                            "detail": "evil", "alert_days": 3, "tz": "Mars/Base",
                            "filter": {"mode": "selected", "vehicles": ["v1", {"x": 1}]}})
    assert p["categories"]["warranties"] is True and "bogus" not in p["categories"]
    assert p["detail"] == "basic" and p["alert_days"] == 7 and p["tz"] == "UTC"
    assert p["filter"] == {"mode": "selected", "vehicles": ["v1"], "properties": []}
    assert cf.normalize_prefs(None) == cf.DEFAULT_PREFS


def test_ics_escaping_and_folding():
    ev = [{"uid": "u@inspectit.app", "date": D(2026, 10, 13),
           "summary": "A, B; C\\ — " + "é" * 60, "description": "line1\nline2",
           "category": "Maintenance", "alarm": "-P6DT15H"}]
    out = cf.render_ics(ev, dt.datetime(2026, 10, 6, tzinfo=dt.timezone.utc))
    assert "\r\n" in out and "SUMMARY:A\\, B\\; C\\\\" in out
    assert "DESCRIPTION:line1\\nline2" in out
    for line in out.split("\r\n"):
        assert len(line.encode()) <= 75
    assert "DTSTART;VALUE=DATE:20261013" in out and "DTEND;VALUE=DATE:20261014" in out
    assert "TRIGGER:-P6DT15H" in out


# ------------------------------------------------------------ e2e ----

def test_setup(client):
    r = client.post("/auth/signup", json={
        "company_name": "CalCo", "name": "Cal Admin",
        "email": "admin@calco.com", "password": "calpass123"})
    assert r.status_code == 200, r.text
    STATE["cid"] = r.json()["company_id"]
    STATE["admin"] = r.json()["access_token"]
    roles = client.get(f"/companies/{STATE['cid']}/roles", headers=_auth(STATE["admin"])).json()
    byname = {x["name"]: x["id"] for x in roles}
    for role, key in [("Vehicle Inspector", "vinsp"), ("Property Maintenance", "pmaint")]:
        inv = client.post(f"/companies/{STATE['cid']}/invitations", headers=_auth(STATE["admin"]),
                          json={"email": f"{key}@calco.com", "role_ids": [byname[role]]}).json()
        acc = client.post("/auth/invitations/accept",
                          json={"token": inv["token"], "name": key, "password": "password123"}).json()
        STATE[key] = acc["access_token"]

    cid, h = STATE["cid"], _auth(STATE["admin"])
    today = dt.date.today()
    iso = lambda d: d.isoformat()
    put = lambda k, v: client.put(f"/companies/{cid}/collections/{k}", headers=h, json={"data": v})
    assert put("vehicles", [{"id": "v1", "vehicleId": "TRK-1", "plate": "ABC123",
                             "makeModel": "2021 Ford F-150", "type": "auto"},
                            {"id": "v2", "vehicleId": "TRK-2", "type": "van"}]).status_code == 200
    assert put("properties", [{"id": "p1", "propertyId": "U-1", "address": "12 Elm St"}]).status_code == 200
    put("vehicleMaintenance", {"v1": {"Engine::Oil & Filter Change": {"d": iso(today - dt.timedelta(days=200)), "o": 1000},
                                      "Compliance::Registration Renewal": {"d": iso(today - dt.timedelta(days=360))}}})
    put("propertyMaintenance", {"p1": {"__tmpl__": "Mine", "Roof::Inspect roof": iso(today - dt.timedelta(days=20))}})
    put("propertyMaintTemplates", {"Mine": {"name": "Mine", "categories": [
        {"group": "Roof", "items": [{"name": "Inspect roof", "freq": "monthly"}]}]}})
    put("inspections", {"v1": [{"date": iso(today - dt.timedelta(days=40)), "odometer": 1000}]})
    put("vehicleInspSchedule", {"v1": {"freq": "monthly"}, "v2": {"freq": "annual", "start": iso(today + dt.timedelta(days=30))}})
    put("tickets", {"v1": [{"id": "T-1", "date": iso(today - dt.timedelta(days=20)), "status": "pending", "description": "Brakes squeal"},
                           {"id": "T-2", "date": iso(today), "dueBy": iso(today + dt.timedelta(days=10)), "status": "pending"},
                           {"id": "T-3", "date": iso(today - dt.timedelta(days=90)), "status": "complete"}]})
    put("vehicleWarranties", {"v1": [{"id": "w1", "item": "Powertrain", "warrantyExp": iso(today + dt.timedelta(days=20))}]})
    put("projects", [{"id": "pj1", "propertyId": "p1", "name": "Ramp", "status": "active",
                      "payments": [{"id": "pay1", "desc": "Deposit", "amount": "500", "due": iso(today + dt.timedelta(days=5)), "paid": False},
                                   {"id": "pay2", "due": iso(today), "paid": True}]}])


def _feed(client, url):
    path = url.split("://", 1)[1].split("/", 1)[1]
    return client.get("/" + path)


def test_settings_default_and_role_limits(client):
    cid = STATE["cid"]
    r = client.get(f"/companies/{cid}/calendar", headers=_auth(STATE["admin"]))
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    j = r.json()
    assert j["enabled"] is False and j["links"] is None and j["has_data"] is True
    assert j["allowed"] == cf.CATEGORIES
    assert j["prefs"]["detail"] == "basic" and j["prefs"]["alert_days"] == 7
    j = client.get(f"/companies/{cid}/calendar", headers=_auth(STATE["vinsp"])).json()
    assert j["allowed"] == ["inspections"]
    j = client.get(f"/companies/{cid}/calendar", headers=_auth(STATE["pmaint"])).json()
    assert j["allowed"] == ["maintenance"]


def test_enable_detailed_feed(client):
    cid = STATE["cid"]
    prefs = {"categories": {c: True for c in cf.CATEGORIES}, "detail": "detailed",
             "alert_days": 7, "show_overdue": True, "tz": "America/Chicago"}
    r = client.put(f"/companies/{cid}/calendar", headers=_auth(STATE["admin"]), json={"prefs": prefs})
    assert r.status_code == 200, r.text
    links = r.json()["links"]
    STATE["admin_links"] = links
    assert links["webcal"].startswith("webcal://") and links["url"].endswith(".ics")
    assert "calendar.google.com" in links["google"] and "addfromweb" in links["outlook"]
    for secret in (STATE["cid"], "admin@calco.com"):
        assert secret not in links["url"]

    f = _feed(client, links["url"])
    assert f.status_code == 200, f.text
    assert f.headers["content-type"].startswith("text/calendar")
    assert "noindex" in f.headers["x-robots-tag"]
    body = f.text.replace("\r\n ", "")
    today = cf.today_in("America/Chicago").strftime("%Y%m%d")
    assert "TRK-1 · 2021 Ford F-150 — Oil & Filter Change" in body
    assert "Overdue: TRK-1 · 2021 Ford F-150 — Oil & Filter Change" in body   # 200 days > semiannual
    assert "Registration Renewal" in body
    assert "Air Filter" not in body and "Service Furnace" not in body           # never done -> not on calendar
    assert "U-1 · 12 Elm St — Inspect roof" in body                              # saved per-entity template
    assert "Overdue: TRK-1 · 2021 Ford F-150 — Inspection" in body              # monthly, last 40 days ago
    assert "TRK-2 — Inspection" in body                                          # never inspected -> start date
    assert "Overdue: TRK-1 · 2021 Ford F-150 — Repair ticket" in body           # 20 days, no due date
    assert "Brakes squeal" in body and "Ticket T-3" not in body                 # complete excluded
    assert "Warranty: Powertrain expires" in body
    assert "Project payment" in body and "Deposit" in body and "$500.00" in body
    assert f"DTSTART;VALUE=DATE:{today}" in body
    assert "TRIGGER:-P6DT15H" in body
    # overdue items sit on today and carry no alarm
    for ev in body.split("BEGIN:VEVENT")[1:]:
        if "SUMMARY:Overdue" in ev:
            assert f"DTSTART;VALUE=DATE:{today}" in ev and "VALARM" not in ev
    # ETag -> 304
    f2 = client.get("/" + links["url"].split("/", 3)[3], headers={"If-None-Match": f.headers["etag"]})
    assert f2.status_code == 304


def test_basic_mode_hides_names_and_groups(client):
    cid = STATE["cid"]
    r = client.put(f"/companies/{cid}/calendar", headers=_auth(STATE["admin"]),
                   json={"prefs": {"categories": {c: True for c in cf.CATEGORIES}, "detail": "basic",
                                   "show_overdue": False}})
    assert r.json()["links"]["url"] == STATE["admin_links"]["url"]    # same link
    body = _feed(client, r.json()["links"]["url"]).text.replace("\r\n ", "")
    for leak in ("TRK-1", "Ford", "Elm", "Powertrain", "Brakes", "ABC123", "Ramp", "CalCo"):
        assert leak not in body
    assert "Vehicle maintenance due" in body and "Vehicle inspection due" in body
    # show_overdue off: overdue oil change stays on its past due date
    past = [l for l in body.split("\r\n") if l.startswith("DTSTART")]
    assert any(l[-8:] < cf.today_in("UTC").strftime("%Y%m%d") for l in past)


def test_filter_selected_entities(client):
    cid = STATE["cid"]
    r = client.put(f"/companies/{cid}/calendar", headers=_auth(STATE["admin"]),
                   json={"prefs": {"categories": {c: True for c in cf.CATEGORIES}, "detail": "detailed",
                                   "filter": {"mode": "selected", "vehicles": ["v2"], "properties": []}}})
    body = _feed(client, r.json()["links"]["url"]).text.replace("\r\n ", "")
    assert "TRK-2" in body and "TRK-1" not in body and "U-1" not in body and "Project" not in body


def test_role_limits_apply_in_feed(client):
    cid = STATE["cid"]
    r = client.put(f"/companies/{cid}/calendar", headers=_auth(STATE["vinsp"]),
                   json={"prefs": {"categories": {c: True for c in cf.CATEGORIES}, "detail": "detailed"}})
    assert r.status_code == 200
    body = _feed(client, r.json()["links"]["url"]).text.replace("\r\n ", "")
    assert "Inspection" in body
    for other in ("Oil", "Repair", "Warranty", "Project", "roof"):
        assert other not in body
    STATE["vinsp_url"] = r.json()["links"]["url"]


def test_reset_and_turn_off(client):
    cid, h = STATE["cid"], _auth(STATE["admin"])
    old = STATE["admin_links"]["url"]
    r = client.post(f"/companies/{cid}/calendar/reset", headers=h)
    assert r.status_code == 200
    new = r.json()["links"]["url"]
    assert new != old
    assert _feed(client, old).status_code == 404
    assert _feed(client, new).status_code == 200
    assert client.delete(f"/companies/{cid}/calendar", headers=h).status_code == 200
    assert _feed(client, new).status_code == 404
    assert client.get(f"/companies/{cid}/calendar", headers=h).json()["enabled"] is False


def test_removed_member_loses_feed(client):
    cid, h = STATE["cid"], _auth(STATE["admin"])
    members = client.get(f"/companies/{cid}/members", headers=h).json()
    m = next(x for x in members if x.get("email") == "vinsp@calco.com")
    r = client.delete(f"/companies/{cid}/members/{m['membership_id']}", headers=h)
    assert r.status_code in (200, 204), r.text
    assert _feed(client, STATE["vinsp_url"]).status_code == 404


def test_bad_tokens_and_log_redaction(client):
    assert client.get("/cal/nope.ics").status_code == 404
    assert client.get("/cal/" + "A" * 54 + ".ics").status_code == 404
