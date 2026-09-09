"""Collection-sync endpoint tests (phase 2). Runs after test_phase1 in file
order, but is self-contained: creates its own company + users."""

STATE = {}


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def test_setup_company(client):
    r = client.post("/auth/signup", json={
        "company_name": "SyncCo", "name": "Sam Admin",
        "email": "sam@syncco.com", "password": "syncpass123"})
    assert r.status_code == 200
    STATE["cid"] = r.json()["company_id"]
    STATE["admin"] = r.json()["access_token"]

    roles = client.get(f"/companies/{STATE['cid']}/roles",
                       headers=_auth(STATE["admin"])).json()
    byname = {x["name"]: x["id"] for x in roles}
    for role, key in [("Viewer", "viewer"), ("Property Inspector", "inspector")]:
        inv = client.post(f"/companies/{STATE['cid']}/invitations",
                          headers=_auth(STATE["admin"]),
                          json={"email": f"{key}@syncco.com",
                                "role_ids": [byname[role]]}).json()
        acc = client.post("/auth/invitations/accept",
                          json={"token": inv["token"], "name": key.title(),
                                "password": "password123"}).json()
        STATE[key] = acc["access_token"]


def test_put_and_get_collection(client):
    cid = STATE["cid"]
    vehicles = [{"id": "v1", "vehicleId": "VAN-9", "type": "van"}]
    r = client.put(f"/companies/{cid}/collections/vehicles",
                   headers=_auth(STATE["admin"]), json={"data": vehicles})
    assert r.status_code == 200, r.text
    STATE["veh_ts"] = r.json()["updated_at"]

    r = client.get(f"/companies/{cid}/collections/vehicles",
                   headers=_auth(STATE["admin"]))
    assert r.status_code == 200
    assert r.json()["data"] == vehicles

    # prefix form also accepted
    r = client.put(f"/companies/{cid}/collections/inspectit.properties",
                   headers=_auth(STATE["admin"]),
                   json={"data": [{"id": "p1", "propertyId": "U-1"}]})
    assert r.status_code == 200

    idx = client.get(f"/companies/{cid}/collections",
                     headers=_auth(STATE["admin"])).json()
    assert {e["key"] for e in idx} == {"vehicles", "properties"}


def test_conflict_detection(client):
    cid = STATE["cid"]
    # stale base timestamp -> 409 with server copy
    r = client.put(f"/companies/{cid}/collections/vehicles",
                   headers=_auth(STATE["admin"]),
                   json={"data": [], "base_updated_at": "2000-01-01T00:00:00"})
    assert r.status_code == 409
    assert r.json()["detail"]["server_data"][0]["vehicleId"] == "VAN-9"
    # matching base timestamp -> accepted
    r = client.put(f"/companies/{cid}/collections/vehicles",
                   headers=_auth(STATE["admin"]),
                   json={"data": [{"id": "v1", "vehicleId": "VAN-9",
                                   "plate": "NEW"}],
                         "base_updated_at": STATE["veh_ts"]})
    assert r.status_code == 200


def test_permissions(client):
    cid = STATE["cid"]
    # Viewer: company-scope view -> can GET, cannot PUT.
    assert client.get(f"/companies/{cid}/collections/vehicles",
                      headers=_auth(STATE["viewer"])).status_code == 200
    assert client.put(f"/companies/{cid}/collections/vehicles",
                      headers=_auth(STATE["viewer"]),
                      json={"data": []}).status_code == 403
    # Property Inspector: company scope since v2.1, so blob sync works. Under
    # assigned scope this was a 403 and an inspector signed in to nothing.
    assert client.get(f"/companies/{cid}/collections/properties",
                      headers=_auth(STATE["inspector"])).status_code == 200
    # Read, not write: no create/edit on properties.
    assert client.put(f"/companies/{cid}/collections/properties",
                      headers=_auth(STATE["inspector"]),
                      json={"data": []}).status_code == 403
    # profile: any member may GET; only company:edit may PUT.
    client.put(f"/companies/{cid}/collections/profile",
               headers=_auth(STATE["admin"]), json={"data": {"company": "SyncCo"}})
    assert client.get(f"/companies/{cid}/collections/profile",
                      headers=_auth(STATE["inspector"])).status_code == 200
    assert client.put(f"/companies/{cid}/collections/profile",
                      headers=_auth(STATE["viewer"]),
                      json={"data": {}}).status_code == 403


def test_forbidden_and_unknown_keys(client):
    cid = STATE["cid"]
    for key in ("account", "session", "users", "cloud", "nonsense"):
        r = client.put(f"/companies/{cid}/collections/{key}",
                       headers=_auth(STATE["admin"]), json={"data": {}})
        assert r.status_code == 422, key
    assert client.get(f"/companies/{cid}/collections/projects",
                      headers=_auth(STATE["admin"])).status_code == 404


def test_import_accepts_real_export_format(client):
    """The app's Export file stores values as JSON strings — must parse."""
    cid = STATE["cid"]
    payload = {"type": "inspectit-backup", "data": {
        "inspectit.vehicles":
            '[{"id":"vs1","vehicleId":"STR-1","type":"auto"}]',
        "inspectit.diagram.auto": "data:image/png;base64,aGVsbG8=",
    }}
    r = client.post(f"/companies/{cid}/import/backup",
                    headers=_auth(STATE["admin"]), json=payload)
    assert r.status_code == 200, r.text
    got = r.json()["imported"]
    assert got.get("vehicles") == 1
    assert got.get("files") == 1


# --- §4.1: delete is Company Administrator only, even through sync --------
#
# Collection sync writes whole blobs, so until 2026-09-09 the server could not
# tell a removed record from a changed one and every role with module:edit
# could erase inspections, tickets and the rest (spec §11). PUT now diffs.

def _put(client, token, key, data):
    return client.put(f"/companies/{STATE['cid']}/collections/{key}",
                      headers=_auth(token), json={"data": data})


def test_setup_manager(client):
    """Manager: edit everywhere, delete nowhere (§4.1)."""
    roles = client.get(f"/companies/{STATE['cid']}/roles",
                       headers=_auth(STATE["admin"])).json()
    rid = next(x["id"] for x in roles if x["name"] == "Manager")
    inv = client.post(f"/companies/{STATE['cid']}/invitations",
                      headers=_auth(STATE["admin"]),
                      json={"email": "mgr@syncco.com", "role_ids": [rid]}).json()
    acc = client.post("/auth/invitations/accept",
                      json={"token": inv["token"], "name": "Max Manager",
                            "password": "password123"})
    assert acc.status_code == 200, acc.text
    STATE["manager"] = acc.json()["access_token"]


def test_removing_an_id_record_needs_delete(client):
    admin, mgr = STATE["admin"], STATE["manager"]
    assert _put(client, admin, "tickets",
                {"v1": [{"id": "t1", "issue": "brakes"},
                        {"id": "t2", "issue": "wipers"}]}).status_code == 200

    r = _put(client, mgr, "tickets", {"v1": [{"id": "t1", "issue": "brakes"}]})
    assert r.status_code == 403, r.text
    assert "vehicle_repairs:delete" in r.text

    # Editing and adding are still edit.
    assert _put(client, mgr, "tickets",
                {"v1": [{"id": "t1", "issue": "brakes — done"},
                        {"id": "t2", "issue": "wipers"},
                        {"id": "t3", "issue": "tyres"}]}).status_code == 200

    # The administrator may remove, and it is audited as a delete.
    assert _put(client, admin, "tickets",
                {"v1": [{"id": "t1", "issue": "brakes — done"}]}).status_code == 200
    from api.db import get_pool
    with get_pool().connection() as conn:
        row = conn.execute(
            """SELECT details FROM audit_log
               WHERE company_id = %s AND action = 'delete'
                 AND subject_type = 'collection'
               ORDER BY at DESC LIMIT 1""",
            (STATE["cid"],)).fetchone()
    assert row and row["details"] == {"key": "tickets", "removed": 2}


def test_idless_records_count_by_length(client):
    """Inspection summaries have no id and the app deletes them by index, so
    a shorter list under the same vehicle is that many deletions. Removing
    the vehicle's whole key is all of them."""
    admin, mgr = STATE["admin"], STATE["manager"]
    two = {"v1": [{"date": "2026-09-01", "result": "pass"},
                  {"date": "2026-09-02", "result": "fail"}]}
    assert _put(client, admin, "inspections", two).status_code == 200

    r = _put(client, mgr, "inspections", {"v1": two["v1"][:1]})
    assert r.status_code == 403 and "vehicle_inspections:delete" in r.text
    r = _put(client, mgr, "inspections", {})
    assert r.status_code == 403, r.text

    # Same length, changed content, and a new vehicle's list: edit.
    assert _put(client, mgr, "inspections",
                {"v1": [{"date": "2026-09-01", "result": "pass (recheck)"},
                        {"date": "2026-09-02", "result": "fail"}],
                 "v2": [{"date": "2026-09-03", "result": "pass"}]}
                ).status_code == 200


def test_nested_project_records_count_but_attachments_do_not(client):
    admin, mgr = STATE["admin"], STATE["manager"]
    proj = [{"id": "p1", "name": "Roof", "notes": "",
             "payments": [{"id": "a", "amount": 100}, {"id": "b", "amount": 50}],
             "attachments": ["one.pdf", "two.pdf"]}]
    assert _put(client, admin, "projects", proj).status_code == 200

    fewer_payments = [dict(proj[0], payments=[{"id": "a", "amount": 100}])]
    r = _put(client, mgr, "projects", fewer_payments)
    assert r.status_code == 403 and "projects:delete" in r.text

    edited = [dict(proj[0], notes="tarps ordered", attachments=["one.pdf"])]
    assert _put(client, mgr, "projects", edited).status_code == 200


def test_maintenance_state_is_still_an_edit(client):
    """Clearing an item's 'last done' removes a dict key; that is a control
    on the scheduler, not a record, so Manager (edit, no delete) may."""
    admin, mgr = STATE["admin"], STATE["manager"]
    assert _put(client, admin, "vehicleMaintenance",
                {"v1": {"Engine::Oil": {"d": "2026-09-01", "o": 84000}}}
                ).status_code == 200
    assert _put(client, mgr, "vehicleMaintenance", {"v1": {}}).status_code == 200
