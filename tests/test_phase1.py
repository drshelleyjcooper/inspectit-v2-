"""Phase-1 end-to-end: signup -> login -> roles -> import -> invite ->
scoped visibility -> permission denials -> password reset -> token rotation.

Tests run in file order and share state via STATE.
"""
import base64

STATE = {}

PDF_BYTES = b"%PDF-1.4 test"
PDF_URL = "data:application/pdf;base64," + base64.b64encode(PDF_BYTES).decode()
PNG_URL = ("data:image/png;base64,"
           + base64.b64encode(b"\x89PNG\r\n\x1a\nfakepng").decode())

BACKUP = {
    "data": {
        "inspectit.profile": {"company": "Cooper Inspections", "phone": "555-1234"},
        "inspectit.vehicles": [
            {"id": "v1", "vehicleId": "TRUCK-1", "plate": "ABC123",
             "makeModel": "Ford F-150", "type": "auto"},
        ],
        "inspectit.properties": [
            {"id": "p1", "propertyId": "UNIT-100", "type": "residential",
             "street": "12 Main St", "city": "Springfield", "state": "IL",
             "zip": "62701"},
            {"id": "p2", "propertyId": "UNIT-200", "type": "commercial"},
        ],
        "inspectit.inspections": {
            "v1": [{"date": "2026-06-01", "odometer": 84000,
                    "template": "__auto__",
                    "inspection": {"inspector": "Brandon", "signature": PNG_URL},
                    "cats": [{"name": "Engine", "items": []}]}],
        },
        "inspectit.propertyInspections": {
            "p1": [{"date": "2026-06-15", "type": "property-inspection",
                    "property": {"propertyId": "UNIT-100"}}],
        },
        "inspectit.tickets": {
            "v1": [{"date": "2026-05-20", "desc": "Brake pads",
                    "cost": "$250.00", "status": "done",
                    "attachments": [{"name": "invoice.pdf",
                                     "type": "application/pdf",
                                     "size": len(PDF_BYTES),
                                     "dataURL": PDF_URL}]}],
        },
        "inspectit.propertyTickets": {
            "p1": [{"date": "2026-04-10", "desc": "Water heater", "cost": 1600}],
        },
        "inspectit.vehicleMaintenance": {
            "v1": {"__odo__": 90000,
                   "Engine::Oil change": {"d": "2026-06-01", "o": 84000}},
        },
        "inspectit.propertyMaintenance": {"p1": {"HVAC::Air Filters": "2026-07-04"}},
        "inspectit.vehicleMaintSpend": {
            "v1": [{"date": "2026-06-01", "item": "Engine::Oil change",
                    "cost": 89.99, "odo": 84000}],
        },
        "inspectit.propertyMaintSpend": {
            "p1": [{"date": "2026-07-04", "item": "HVAC::Air Filters", "cost": 18.5},
                   {"date": "2026-06-04", "item": "HVAC::Air Filters",
                    "cost": "17.99"}],
        },
        "inspectit.propertyMaintTemplates": {
            "__custom1__": {"label": "My Schedule", "cats": []},
        },
        "inspectit.vehicleWarranties": {
            "v1": [{"item": "Battery", "vendor": "AutoZone",
                    "purchaseDate": "2025-01-15", "warrantyExp": "2027-01-15",
                    "notes": "3yr",
                    "attachments": [{"name": "warranty.pdf",
                                     "type": "application/pdf",
                                     "size": len(PDF_BYTES),
                                     "dataURL": PDF_URL}]}],
        },
        "inspectit.propertyWarranties": {
            "p1": [{"item": "Water Heater", "warrantyExp": "2026-08-01"}],
        },
        "inspectit.projects": [
            {"id": "pr1", "propertyId": "p1", "name": "Kitchen Remodel",
             "date": "2026-06-01", "type": "Lifestyle Improvement",
             "status": "active", "goals": "Modern kitchen",
             "initialBudget": "10000", "revisedBudget": "12,500",
             "scope": [{"id": "s1", "stype": "Scope of Project",
                        "desc": "Full remodel",
                        "atts": [{"name": "plans.pdf",
                                  "type": "application/pdf",
                                  "size": len(PDF_BYTES), "dataURL": PDF_URL}]}],
             "estimates": [
                 {"id": "e1", "category": "Labor", "desc": "GC", "amount": "7000"},
                 {"id": "e2", "category": "Material", "amount": 5000}],
             "contractors": [{"id": "c1", "ctype": "General Contractor",
                              "company": "ACME", "notes": "good",
                              "docs": {"insurance": [{"name": "ins.pdf",
                                                      "type": "application/pdf",
                                                      "size": len(PDF_BYTES),
                                                      "dataURL": PDF_URL}]}}],
             "payments": [
                 {"id": "pay1", "desc": "Deposit", "amount": "3000",
                  "due": "2026-06-05", "ptype": "Check", "paid": True,
                  "atts": [{"name": "receipt.pdf", "type": "application/pdf",
                            "size": len(PDF_BYTES), "dataURL": PDF_URL}]},
                 {"id": "pay2", "desc": "Final", "amount": 9000,
                  "due": "2026-09-01", "paid": False}],
             "permits": [{"id": "pm1", "ptype": "Building",
                          "status": "approved", "atts": []}],
             "finalRecords": [{"id": "f1", "rtype": "Warranties",
                               "desc": "GC warranty"}]},
        ],
        "inspectit.projectMeta": {"types": ["Custom Type"],
                                  "costCategories": ["Disposal Fees"],
                                  "paymentTypes": ["Zelle"]},
        "inspectit.diagram.auto": PNG_URL,
        "inspectit.account": {"username": "brandon", "password": "x"},
        "inspectit.session": "1",
    }
}


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_signup_creates_company_and_admin(client):
    r = client.post("/auth/signup", json={
        "company_name": "Cooper Inspections", "name": "Brandon",
        "email": "brandon@example.com", "password": "hunter2secure"})
    assert r.status_code == 200, r.text
    body = r.json()
    STATE["cid"] = body["company_id"]
    STATE["admin"] = body["access_token"]
    STATE["admin_refresh"] = body["refresh_token"]

    me = client.get("/me", headers=_auth(STATE["admin"])).json()
    m = me["memberships"][0]
    assert m["roles"][0]["name"] == "Company Administrator"
    assert "assign" in m["permissions"]["company"]
    assert "delete" in m["permissions"]["vehicles"]


def test_duplicate_signup_and_bad_login(client):
    assert client.post("/auth/signup", json={
        "company_name": "X", "name": "Y", "email": "brandon@example.com",
        "password": "hunter2secure"}).status_code == 409
    assert client.post("/auth/login", json={
        "email": "brandon@example.com", "password": "wrong"}).status_code == 401
    assert client.get("/me").status_code == 401  # no token


def test_role_presets_seeded(client):
    r = client.get(f"/companies/{STATE['cid']}/roles",
                   headers=_auth(STATE["admin"]))
    assert r.status_code == 200
    roles = {x["name"]: x for x in r.json()}
    assert len([x for x in r.json() if x["is_preset"]]) == 8
    assert roles["Property Inspector"]["scope"] == "assigned"
    assert roles["Viewer"]["scope"] == "company"
    STATE["prop_inspector_role"] = roles["Property Inspector"]["id"]


def test_import_backup(client, storage_dir):
    r = client.post(f"/companies/{STATE['cid']}/import/backup",
                    headers=_auth(STATE["admin"]), json=BACKUP)
    assert r.status_code == 200, r.text
    got = r.json()["imported"]
    expected = {
        "vehicles": 1, "properties": 2,
        "vehicle_inspections": 1, "property_inspections": 1,
        "vehicle_tickets": 1, "property_tickets": 1,
        "vehicle_maintenance_items": 1, "property_maintenance_items": 1,
        "vehicle_spend": 1, "property_spend": 2,
        "property_maintenance_templates": 1,
        "vehicle_warranties": 1, "property_warranties": 1,
        "projects": 1, "option_lists": 3, "company_profile": 1,
    }
    for k, v in expected.items():
        assert got.get(k) == v, f"{k}: expected {v}, got {got.get(k)} ({got})"
    # signature + ticket pdf + warranty pdf + scope pdf + contractor pdf
    # + payment receipt + diagram png = 7
    assert got["files"] == 7

    # binaries actually landed on disk, content-intact
    pdfs = list(storage_dir.rglob("*.pdf"))
    assert pdfs and pdfs[0].read_bytes() == PDF_BYTES

    vehicles = client.get(f"/companies/{STATE['cid']}/vehicles",
                          headers=_auth(STATE["admin"])).json()
    assert vehicles[0]["vehicle_id"] == "TRUCK-1"
    assert vehicles[0]["current_odometer"] == 90000  # __odo__ override applied

    props = client.get(f"/companies/{STATE['cid']}/properties",
                       headers=_auth(STATE["admin"])).json()
    assert {p["property_id"] for p in props} == {"UNIT-100", "UNIT-200"}
    STATE["prop_uuid"] = next(p["id"] for p in props
                              if p["property_id"] == "UNIT-100")

    projects = client.get(f"/companies/{STATE['cid']}/projects",
                          headers=_auth(STATE["admin"])).json()
    assert projects[0]["name"] == "Kitchen Remodel"


def test_invite_and_accept_property_inspector(client):
    r = client.post(f"/companies/{STATE['cid']}/invitations",
                    headers=_auth(STATE["admin"]),
                    json={"email": "inspector@example.com",
                          "role_ids": [STATE["prop_inspector_role"]]})
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    r = client.post("/auth/invitations/accept",
                    json={"token": token, "name": "Ines Spector",
                          "password": "inspectme123"})
    assert r.status_code == 200, r.text
    STATE["inspector"] = r.json()["access_token"]
    STATE["inspector_uid"] = r.json()["user_id"]

    me = client.get("/me", headers=_auth(STATE["inspector"])).json()
    m = me["memberships"][0]
    assert m["roles"][0]["name"] == "Property Inspector"
    assert "company" not in m["permissions"]

    # token can't be reused
    assert client.post("/auth/invitations/accept",
                       json={"token": token, "name": "x",
                             "password": "12345678x"}).status_code == 400


def test_inspector_permissions_and_scoping(client):
    h = _auth(STATE["inspector"])
    cid = STATE["cid"]
    # No vehicle access at all; no invite rights; no import rights.
    assert client.get(f"/companies/{cid}/vehicles", headers=h).status_code == 403
    assert client.post(f"/companies/{cid}/invitations", headers=h,
                       json={"email": "x@x.co", "role_ids": []}).status_code == 403
    assert client.post(f"/companies/{cid}/import/backup", headers=h,
                       json={}).status_code == 403
    assert client.post(f"/companies/{cid}/assignments", headers=h,
                       json={"user_id": STATE["inspector_uid"],
                             "subject_type": "property",
                             "subject_id": STATE["prop_uuid"],
                             "duty": "inspection"}).status_code == 403

    # Assigned-only scope: sees nothing until assigned.
    assert client.get(f"/companies/{cid}/properties", headers=h).json() == []

    r = client.post(f"/companies/{cid}/assignments", headers=_auth(STATE["admin"]),
                    json={"user_id": STATE["inspector_uid"],
                          "subject_type": "property",
                          "subject_id": STATE["prop_uuid"],
                          "duty": "inspection"})
    assert r.status_code == 200, r.text

    visible = client.get(f"/companies/{cid}/properties", headers=h).json()
    assert len(visible) == 1 and visible[0]["property_id"] == "UNIT-100"


def test_outsider_cannot_touch_company(client):
    r = client.post("/auth/signup", json={
        "company_name": "Rival Co", "name": "Eve",
        "email": "eve@example.com", "password": "evilpassword"})
    outsider = r.json()["access_token"]
    assert client.get(f"/companies/{STATE['cid']}/properties",
                      headers=_auth(outsider)).status_code == 403


def test_password_reset_flow(client):
    r = client.post("/auth/forgot", json={"email": "brandon@example.com"})
    token = r.json().get("dev_reset_token")
    assert token
    assert client.post("/auth/reset", json={
        "token": token, "password": "newpassword99"}).status_code == 200
    assert client.post("/auth/login", json={
        "email": "brandon@example.com",
        "password": "hunter2secure"}).status_code == 401
    r = client.post("/auth/login", json={
        "email": "brandon@example.com", "password": "newpassword99"})
    assert r.status_code == 200
    # reset token is single-use
    assert client.post("/auth/reset", json={
        "token": token, "password": "another999"}).status_code == 400


def test_refresh_rotation(client):
    r = client.post("/auth/login", json={
        "email": "inspector@example.com", "password": "inspectme123"})
    refresh1 = r.json()["refresh_token"]
    r2 = client.post("/auth/refresh", json={"refresh_token": refresh1})
    assert r2.status_code == 200
    # old refresh token is dead after rotation
    assert client.post("/auth/refresh",
                       json={"refresh_token": refresh1}).status_code == 401
    # new one works
    assert client.post("/auth/refresh",
                       json={"refresh_token": r2.json()["refresh_token"]}
                       ).status_code == 200
