"""Stage 2: admin users/roles endpoints. Contract in
docs/admin-users-api.md. Tests run in file order and share state via STATE,
matching the convention in test_phase1.py.
"""
from psycopg.types.json import Jsonb

from api.db import get_pool

STATE = {}


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def test_setup(client):
    r = client.post("/auth/signup", json={
        "company_name": "AdminAPI Co", "name": "Admin One",
        "email": "admapi-admin1@example.com", "password": "adminpass123"})
    assert r.status_code == 200, r.text
    body = r.json()
    STATE["cid"] = body["company_id"]
    STATE["admin1_uid"] = body["user_id"]
    STATE["admin1_access"] = body["access_token"]

    roles = client.get(f"/companies/{STATE['cid']}/roles",
                       headers=_auth(STATE["admin1_access"])).json()
    STATE["admin_role"] = next(r["id"] for r in roles
                              if r["name"] == "Company Administrator")
    STATE["viewer_role"] = next(r["id"] for r in roles if r["name"] == "Viewer")

    inv = client.post(f"/companies/{STATE['cid']}/invitations",
                      headers=_auth(STATE["admin1_access"]),
                      json={"email": "admapi-admin2@example.com",
                            "role_ids": [STATE["admin_role"]]}).json()
    acc = client.post("/auth/invitations/accept", json={
        "token": inv["token"], "name": "Admin Two", "password": "adminpass222"
    }).json()
    STATE["admin2_uid"] = acc["user_id"]
    STATE["admin2_access"] = acc["access_token"]

    inv2 = client.post(f"/companies/{STATE['cid']}/invitations",
                       headers=_auth(STATE["admin1_access"]),
                       json={"email": "admapi-viewer1@example.com",
                             "role_ids": [STATE["viewer_role"]]}).json()
    acc2 = client.post("/auth/invitations/accept", json={
        "token": inv2["token"], "name": "Viewer One", "password": "viewerpass1"
    }).json()
    STATE["viewer1_uid"] = acc2["user_id"]
    STATE["viewer1_access"] = acc2["access_token"]


def test_list_members_filters(client):
    h = _auth(STATE["admin1_access"])
    cid = STATE["cid"]

    r = client.get(f"/companies/{cid}/members", headers=h)
    assert r.status_code == 200
    assert int(r.headers["x-total-count"]) == 3
    assert len(r.json()) == 3

    r = client.get(f"/companies/{cid}/members?q=viewer", headers=h)
    assert int(r.headers["x-total-count"]) == 1
    assert r.json()[0]["email"] == "admapi-viewer1@example.com"

    r = client.get(f"/companies/{cid}/members?role={STATE['admin_role']}", headers=h)
    assert int(r.headers["x-total-count"]) == 2

    r = client.get(f"/companies/{cid}/members?status=suspended", headers=h)
    assert int(r.headers["x-total-count"]) == 0

    assert client.get(f"/companies/{cid}/members?status=bogus",
                      headers=h).status_code == 422
    assert client.get(f"/companies/{cid}/members?role=not-a-uuid",
                      headers=h).status_code == 422


def test_member_detail(client):
    h = _auth(STATE["admin1_access"])
    cid = STATE["cid"]
    r = client.get(f"/companies/{cid}/members/{STATE['viewer1_uid']}", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "active"
    assert body["suspend_reason"] is None
    assert any(role["name"] == "Viewer" for role in body["roles"])
    assert "view" in body["permissions"].get("vehicles", [])
    assert body["last_login"] is not None       # logged in via invite-accept
    # Accepting an invite itself writes an audit row (actor = the invited
    # user), so last_data_activity is populated here too — the "reads empty"
    # caveat in docs/admin-users-api.md is about collection-sync activity
    # specifically, not every audit_log entry.
    assert body["last_data_activity"] is not None
    assert isinstance(body["sessions"], list) and len(body["sessions"]) >= 1
    assert body["sessions"][0]["active"] is True

    assert client.get(
        f"/companies/{cid}/members/00000000-0000-0000-0000-000000000000",
        headers=h).status_code == 404


def test_member_detail_requires_company_view(client):
    # Viewer role has no company:view grant -> 403, not the roster endpoint's
    # looser 'or any assign' rule.
    r = client.get(f"/companies/{STATE['cid']}/members/{STATE['admin1_uid']}",
                   headers=_auth(STATE["viewer1_access"]))
    assert r.status_code == 403


def test_suspend_requires_reason(client):
    h = _auth(STATE["admin1_access"])
    r = client.post(
        f"/companies/{STATE['cid']}/members/{STATE['viewer1_uid']}/suspend",
        headers=h, json={"reason": "   "})
    assert r.status_code == 422


def test_self_suspend_guard(client):
    h = _auth(STATE["admin1_access"])
    r = client.post(
        f"/companies/{STATE['cid']}/members/{STATE['admin1_uid']}/suspend",
        headers=h, json={"reason": "test"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "self_suspend"


def test_suspend_and_reactivate_flow(client):
    h = _auth(STATE["admin1_access"])
    cid = STATE["cid"]
    r = client.post(f"/companies/{cid}/members/{STATE['viewer1_uid']}/suspend",
                    headers=h, json={"reason": "policy violation"})
    assert r.status_code == 200
    assert r.json()["status"] == "suspended"

    # Idempotent: a second suspend call is a 200 no-op, not an error, and
    # doesn't overwrite the original reason.
    r2 = client.post(f"/companies/{cid}/members/{STATE['viewer1_uid']}/suspend",
                     headers=h, json={"reason": "second call"})
    assert r2.status_code == 200
    detail = client.get(f"/companies/{cid}/members/{STATE['viewer1_uid']}",
                        headers=h).json()
    assert detail["status"] == "suspended"
    assert detail["suspend_reason"] == "policy violation"

    # Suspended member's existing token is rejected immediately, distinct code.
    r = client.get(f"/companies/{cid}/roles", headers=_auth(STATE["viewer1_access"]))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "account_suspended"

    r = client.post(f"/companies/{cid}/members/{STATE['viewer1_uid']}/reactivate",
                    headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "active"
    r2 = client.post(f"/companies/{cid}/members/{STATE['viewer1_uid']}/reactivate",
                     headers=h)
    assert r2.status_code == 200   # idempotent

    detail = client.get(f"/companies/{cid}/members/{STATE['viewer1_uid']}",
                        headers=h).json()
    assert detail["suspend_reason"] is None

    fresh = client.post("/auth/login", json={
        "email": "admapi-viewer1@example.com", "password": "viewerpass1"})
    assert fresh.status_code == 200
    STATE["viewer1_access"] = fresh.json()["access_token"]


def test_revoke_sessions_distinct_from_suspend(client):
    """revoke-sessions kills refresh tokens only — it does not touch account
    status, and (unlike suspend) an already-issued access token keeps working
    until its own TTL, since company_member's live status check never fires
    for it."""
    h = _auth(STATE["admin1_access"])
    cid = STATE["cid"]
    login = client.post("/auth/login", json={
        "email": "admapi-viewer1@example.com", "password": "viewerpass1"})
    viewer_refresh = login.json()["refresh_token"]

    r = client.post(f"/companies/{cid}/members/{STATE['viewer1_uid']}/revoke-sessions",
                    headers=h)
    assert r.status_code == 200
    assert r.json()["tokens_revoked"] >= 1

    r = client.post("/auth/refresh", json={"refresh_token": viewer_refresh})
    assert r.status_code == 401

    detail = client.get(f"/companies/{cid}/members/{STATE['viewer1_uid']}",
                        headers=h).json()
    assert detail["status"] == "active"


def test_patch_roles_add_and_remove(client):
    h = _auth(STATE["admin1_access"])
    cid = STATE["cid"]
    r = client.patch(f"/companies/{cid}/members/{STATE['viewer1_uid']}/roles",
                     headers=h, json={"add": [STATE["admin_role"]],
                                      "remove": [STATE["viewer_role"]]})
    assert r.status_code == 200
    body = r.json()
    assert {ro["name"] for ro in body["roles"]} == {"Company Administrator"}
    assert "delete" in body["permissions"].get("company", [])

    # Idempotent: re-applying the same diff (add-already-present,
    # remove-already-absent) doesn't error.
    r2 = client.patch(f"/companies/{cid}/members/{STATE['viewer1_uid']}/roles",
                      headers=h, json={"add": [STATE["admin_role"]],
                                       "remove": [STATE["viewer_role"]]})
    assert r2.status_code == 200

    r3 = client.patch(f"/companies/{cid}/members/{STATE['viewer1_uid']}/roles",
                      headers=h, json={"add": [STATE["viewer_role"]],
                                       "remove": [STATE["admin_role"]]})
    assert r3.status_code == 200
    assert {ro["name"] for ro in r3.json()["roles"]} == {"Viewer"}


def test_patch_roles_unknown_role_422(client):
    h = _auth(STATE["admin1_access"])
    r = client.patch(
        f"/companies/{STATE['cid']}/members/{STATE['viewer1_uid']}/roles",
        headers=h, json={"add": ["00000000-0000-0000-0000-000000000000"]})
    assert r.status_code == 422


def test_self_deadmin_guard(client):
    h = _auth(STATE["admin1_access"])
    r = client.patch(
        f"/companies/{STATE['cid']}/members/{STATE['admin1_uid']}/roles",
        headers=h, json={"remove": [STATE["admin_role"]]})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "self_deadmin"


def test_last_admin_guard_is_permission_based(client):
    """Construct a custom role granting company:assign but NOT
    company:delete, give it to admin2 in place of their preset role, so
    admin2 can call these endpoints without being counted as an admin. Then
    have admin2 (not admin1 — self_suspend/self_deadmin don't apply here)
    try to suspend and to de-admin admin1, the sole remaining company:delete
    holder. Both must 409 last_admin — proving the guard is computed from
    actual permissions, not the literal 'Company Administrator' name, per
    docs/admin-users-api.md."""
    cid = STATE["cid"]
    with get_pool().connection() as conn:
        custom = conn.execute(
            """INSERT INTO roles (company_id, name, scope, permissions, is_preset)
               VALUES (%s, 'Assign Only', 'company', %s, false) RETURNING id""",
            (cid, Jsonb({"company": ["assign", "view"]}))).fetchone()
        custom_role_id = str(custom["id"])
        admin2_membership_id = conn.execute(
            "SELECT id FROM memberships WHERE company_id = %s AND user_id = %s",
            (cid, STATE["admin2_uid"])).fetchone()["id"]
        conn.execute(
            "INSERT INTO membership_roles (membership_id, role_id) VALUES (%s, %s)",
            (admin2_membership_id, custom_role_id))
        conn.execute(
            "DELETE FROM membership_roles WHERE membership_id = %s AND role_id = %s",
            (admin2_membership_id, STATE["admin_role"]))

    h2 = _auth(STATE["admin2_access"])
    r = client.post(f"/companies/{cid}/members/{STATE['admin1_uid']}/suspend",
                    headers=h2, json={"reason": "test"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "last_admin"

    r = client.patch(f"/companies/{cid}/members/{STATE['admin1_uid']}/roles",
                     headers=h2, json={"remove": [STATE["admin_role"]]})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "last_admin"

    # Restore admin2 to full admin — not required by any later test, just
    # leaves the fixture data coherent.
    with get_pool().connection() as conn:
        conn.execute(
            """INSERT INTO membership_roles (membership_id, role_id)
               VALUES (%s, %s) ON CONFLICT DO NOTHING""",
            (admin2_membership_id, STATE["admin_role"]))


def test_audit_new_filters(client):
    h = _auth(STATE["admin1_access"])
    cid = STATE["cid"]
    r = client.get(f"/companies/{cid}/audit?actor={STATE['admin1_uid']}&action=suspend",
                  headers=h)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 1
    assert all(row["action"] == "suspend" for row in rows)
    assert all(row["user_id"] == STATE["admin1_uid"] for row in rows)

    assert client.get(f"/companies/{cid}/audit?actor=not-a-uuid",
                      headers=h).status_code == 422
