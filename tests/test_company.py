"""GET/PATCH /companies/{id}: read and rename the company record.

Rename needs company:edit — Company Administrator and Manager hold it; the
domain managers, inspectors and viewers do not (USER-ROLES-SPEC §4.1).
"""
import uuid

import pytest

PW = "test-password-123"


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _signup(client, company="Rename Co"):
    email = f"admin-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/auth/signup", json={
        "company_name": company, "name": "Admin", "email": email, "password": PW})
    assert r.status_code == 200, r.text
    d = r.json()
    return d["company_id"], d["access_token"]


def _roles(client, cid, token):
    r = client.get(f"/companies/{cid}/roles", headers=_auth(token))
    assert r.status_code == 200, r.text
    return {row["name"]: row["id"] for row in r.json()}


def _add_member(client, cid, admin_token, role_name):
    rmap = _roles(client, cid, admin_token)
    email = f"m-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post(f"/companies/{cid}/invitations", headers=_auth(admin_token),
                    json={"email": email, "role_ids": [rmap[role_name]]})
    assert r.status_code == 200, r.text
    a = client.post("/auth/invitations/accept", json={
        "token": r.json()["token"], "name": role_name, "password": PW})
    assert a.status_code == 200, a.text
    return a.json()["access_token"]


@pytest.fixture
def company(client):
    cid, tok = _signup(client)
    return cid, tok


def test_any_member_can_read_the_company(client, company):
    cid, tok = company
    viewer = _add_member(client, cid, tok, "Viewer")
    r = client.get(f"/companies/{cid}", headers=_auth(viewer))
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Rename Co"
    assert r.json()["id"] == cid


def test_administrator_renames_and_me_reflects_it(client, company):
    cid, tok = company
    r = client.patch(f"/companies/{cid}", headers=_auth(tok),
                     json={"name": "  Rename   Co  Test "})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Rename Co Test"      # whitespace collapsed
    me = client.get("/me", headers=_auth(tok)).json()
    names = {m["company_id"]: m["company_name"] for m in me["memberships"]}
    assert names[cid] == "Rename Co Test"
    audit = client.get(f"/companies/{cid}/audit", headers=_auth(tok))
    assert audit.status_code == 200, audit.text
    hits = [a for a in audit.json() if a.get("subject_type") == "company"
            and (a.get("details") or {}).get("field") == "name"]
    assert hits and hits[0]["details"]["to"] == "Rename Co Test"


def test_manager_may_rename(client, company):
    cid, tok = company
    mgr = _add_member(client, cid, tok, "Manager")
    r = client.patch(f"/companies/{cid}", headers=_auth(mgr),
                     json={"name": "Manager Renamed"})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("role", ["Vehicle Manager", "Vehicle Inspector", "Viewer"])
def test_other_roles_may_not_rename(client, company, role):
    cid, tok = company
    other = _add_member(client, cid, tok, role)
    r = client.patch(f"/companies/{cid}", headers=_auth(other),
                     json={"name": "Nope"})
    assert r.status_code == 403, r.text
    assert client.get(f"/companies/{cid}", headers=_auth(tok)).json()["name"] == "Rename Co"


def test_blank_and_overlong_names_are_rejected(client, company):
    cid, tok = company
    assert client.patch(f"/companies/{cid}", headers=_auth(tok),
                        json={"name": "   "}).status_code == 422
    assert client.patch(f"/companies/{cid}", headers=_auth(tok),
                        json={"name": "x" * 121}).status_code == 422


def test_non_member_cannot_read_or_rename(client, company):
    cid, _ = company
    _, other_tok = _signup(client, "Other Co")
    assert client.get(f"/companies/{cid}", headers=_auth(other_tok)).status_code == 403
    assert client.patch(f"/companies/{cid}", headers=_auth(other_tok),
                        json={"name": "Hijack"}).status_code == 403
