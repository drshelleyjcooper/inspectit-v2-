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
    # Property Inspector: assigned scope -> blob sync denied even for view.
    assert client.get(f"/companies/{cid}/collections/properties",
                      headers=_auth(STATE["inspector"])).status_code == 403
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
