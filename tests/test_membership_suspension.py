"""Stage 1 of the admin users/roles work: suspension is the primary account
control, deletion stays a separate later concern. These tests exercise the
core db.suspend_membership / db.reactivate_membership functions directly
(no HTTP endpoint exists yet — that's Stage 2) plus the auth-middleware
change in permissions.company_member that rejects a suspended membership
with a distinct 403 code instead of a generic one.
"""
from api.db import get_pool, reactivate_membership, suspend_membership

STATE = {}


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _membership_id(company_id, user_id):
    """/me doesn't expose membership_id (that's a Stage 2 detail-endpoint
    concern) — fetch it directly for test setup."""
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT id FROM memberships WHERE company_id = %s AND user_id = %s",
            (company_id, user_id)).fetchone()
    return str(row["id"])


def test_setup_company_and_member(client):
    r = client.post("/auth/signup", json={
        "company_name": "Suspend Test Co", "name": "Admin One",
        "email": "suspend-admin@example.com", "password": "adminpass123",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    STATE["cid"] = body["company_id"]
    STATE["admin_uid"] = body["user_id"]
    STATE["admin_access"] = body["access_token"]
    STATE["admin_refresh"] = body["refresh_token"]

    STATE["admin_mid"] = _membership_id(STATE["cid"], STATE["admin_uid"])

    # A second, ordinary member to suspend/reactivate (keeps the admin
    # account untouched so later tests aren't tangled with last-admin logic,
    # which is a Stage 2 guard, not this stage's concern).
    roles = client.get(f"/companies/{STATE['cid']}/roles",
                       headers=_auth(STATE["admin_access"])).json()
    viewer_role = next(r["id"] for r in roles if r["name"] == "Viewer")
    inv = client.post(f"/companies/{STATE['cid']}/invitations",
                      headers=_auth(STATE["admin_access"]),
                      json={"email": "member@example.com",
                            "role_ids": [viewer_role]}).json()
    accept = client.post("/auth/invitations/accept", json={
        "token": inv["token"], "name": "Member One", "password": "memberpass1",
    })
    assert accept.status_code == 200, accept.text
    STATE["member_uid"] = accept.json()["user_id"]
    STATE["member_access"] = accept.json()["access_token"]
    STATE["member_refresh"] = accept.json()["refresh_token"]
    STATE["member_mid"] = _membership_id(STATE["cid"], STATE["member_uid"])


def test_member_can_access_before_suspension(client):
    r = client.get(f"/companies/{STATE['cid']}/roles",
                   headers=_auth(STATE["member_access"]))
    assert r.status_code == 200


def test_suspend_flips_status_and_kills_refresh_token(client):
    with get_pool().connection() as conn:
        result = suspend_membership(conn, STATE["member_mid"],
                                    STATE["admin_uid"], "policy violation")
    assert result["status"] == "suspended"

    with get_pool().connection() as conn:
        row = conn.execute(
            """SELECT status, suspended_at, suspended_by, suspend_reason
               FROM memberships WHERE id = %s""",
            (STATE["member_mid"],)).fetchone()
    assert row["status"] == "suspended"
    assert row["suspended_at"] is not None
    assert str(row["suspended_by"]) == STATE["admin_uid"]
    assert row["suspend_reason"] == "policy violation"

    # The refresh token issued before suspension is dead immediately — not
    # just at access-token expiry.
    r = client.post("/auth/refresh", json={"refresh_token": STATE["member_refresh"]})
    assert r.status_code == 401


def test_suspended_member_rejected_with_distinct_code(client):
    r = client.get(f"/companies/{STATE['cid']}/roles",
                   headers=_auth(STATE["member_access"]))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "account_suspended"


def test_suspend_is_idempotent(client):
    with get_pool().connection() as conn:
        result = suspend_membership(conn, STATE["member_mid"],
                                    STATE["admin_uid"], "second call")
    # No-op: returns the already-suspended row, doesn't overwrite the reason.
    assert result["status"] == "suspended"
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT suspend_reason FROM memberships WHERE id = %s",
            (STATE["member_mid"],)).fetchone()
    assert row["suspend_reason"] == "policy violation"


def test_reactivate_clears_fields_and_restores_access(client):
    with get_pool().connection() as conn:
        result = reactivate_membership(conn, STATE["member_mid"])
    assert result["status"] == "active"

    with get_pool().connection() as conn:
        row = conn.execute(
            """SELECT status, suspended_at, suspended_by, suspend_reason
               FROM memberships WHERE id = %s""",
            (STATE["member_mid"],)).fetchone()
    assert row["status"] == "active"
    assert row["suspended_at"] is None
    assert row["suspended_by"] is None
    assert row["suspend_reason"] is None

    # Reactivation does not resurrect the old refresh token...
    r = client.post("/auth/refresh", json={"refresh_token": STATE["member_refresh"]})
    assert r.status_code == 401
    # ...but a fresh login works again.
    r = client.post("/auth/login", json={
        "email": "member@example.com", "password": "memberpass1"})
    assert r.status_code == 200
    fresh_access = r.json()["access_token"]
    r = client.get(f"/companies/{STATE['cid']}/roles", headers=_auth(fresh_access))
    assert r.status_code == 200


def test_reactivate_is_idempotent(client):
    with get_pool().connection() as conn:
        result = reactivate_membership(conn, STATE["member_mid"])
    assert result["status"] == "active"


def test_suspend_unknown_membership_returns_none(client):
    with get_pool().connection() as conn:
        result = suspend_membership(
            conn, "00000000-0000-0000-0000-000000000000", STATE["admin_uid"], "x")
    assert result is None


def test_not_a_member_still_uses_distinct_code(client):
    """Different situation, different code — company_member must not collapse
    'never a member' and 'suspended member' into the same string anymore."""
    r = client.post("/auth/signup", json={
        "company_name": "Other Co", "name": "Outsider",
        "email": "outsider@example.com", "password": "outsiderpass1",
    })
    outsider_access = r.json()["access_token"]
    r = client.get(f"/companies/{STATE['cid']}/roles",
                   headers=_auth(outsider_access))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "not_a_member"
