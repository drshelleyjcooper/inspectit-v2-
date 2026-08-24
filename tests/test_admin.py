"""Platform admin portal: bootstrap flag, access control, create user,
password reset, disable, stats."""
import uuid

import pytest


def _signup(client, tag):
    r = client.post("/auth/signup", json={
        "company_name": f"Adm Co {tag}", "name": f"Admin {tag}",
        "email": f"adm-{tag}@example.com", "password": "password123"})
    assert r.status_code == 200, r.text
    return r.json()


def _auth(tok):
    return {"Authorization": "Bearer " + tok}


@pytest.fixture(scope="module")
def admin(client):
    """A signed-up user promoted via the PLATFORM_ADMIN_EMAILS mechanism."""
    tag = uuid.uuid4().hex[:6]
    t = _signup(client, tag)
    from api import config
    from api.db import get_pool
    from api.routers.admin import promote_platform_admins
    config.PLATFORM_ADMIN_EMAILS.append(f"adm-{tag}@example.com")
    with get_pool().connection() as conn:
        assert promote_platform_admins(conn) == [f"adm-{tag}@example.com"]
        assert promote_platform_admins(conn) == []          # idempotent
    return {"tag": tag, "hdr": _auth(t["access_token"]), "company_id": t["company_id"],
            "user_id": t["user_id"]}


def test_non_admin_is_refused(client):
    t = _signup(client, uuid.uuid4().hex[:6])
    r = client.get("/admin/stats", headers=_auth(t["access_token"]))
    assert r.status_code == 403
    assert client.get("/admin/stats").status_code == 401


def test_stats_shape(client, admin):
    r = client.get("/admin/stats", headers=admin["hdr"])
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["totals"]["users"] >= 2
    assert s["totals"]["platform_admins"] >= 1
    assert "logins_24h" in s["activity"]
    assert isinstance(s["signups_daily"], list)
    assert any(c["id"] == admin["company_id"] for c in s["top_companies"])


def test_login_records_last_login(client, admin):
    r = client.post("/auth/login", json={"email": f"adm-{admin['tag']}@example.com",
                                         "password": "password123"})
    assert r.status_code == 200
    r = client.get("/admin/users?q=" + f"adm-{admin['tag']}", headers=admin["hdr"])
    me = [u for u in r.json() if u["id"] == admin["user_id"]][0]
    assert me["last_login_at"] is not None
    assert me["is_platform_admin"] is True


def test_create_user_in_existing_company_generated_password(client, admin):
    email = f"new-{uuid.uuid4().hex[:6]}@example.com"
    r = client.post("/admin/users", headers=admin["hdr"], json={
        "email": email, "name": "New Person",
        "company_id": admin["company_id"], "role": "Viewer"})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["password_generated"] is True and len(out["password"]) >= 12
    assert out["memberships"][0]["company_id"] == admin["company_id"]
    assert out["memberships"][0]["roles"] == ["Viewer"]

    # ...and that password actually works.
    r = client.post("/auth/login", json={"email": email, "password": out["password"]})
    assert r.status_code == 200
    me = client.get("/me", headers=_auth(r.json()["access_token"])).json()
    assert me["memberships"][0]["company_id"] == admin["company_id"]

    # Duplicate email -> 409
    r = client.post("/admin/users", headers=admin["hdr"], json={
        "email": email, "name": "Dup", "password": "password123"})
    assert r.status_code == 409


def test_create_user_with_new_company(client, admin):
    email = f"owner-{uuid.uuid4().hex[:6]}@example.com"
    r = client.post("/admin/users", headers=admin["hdr"], json={
        "email": email, "name": "Owner", "password": "password123",
        "company_name": "Brand New Co"})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["password_generated"] is False
    assert out["memberships"][0]["company_name"] == "Brand New Co"
    assert out["memberships"][0]["roles"] == ["Company Administrator"]
    r = client.get("/admin/companies?q=Brand", headers=admin["hdr"])
    assert any(c["name"] == "Brand New Co" and c["members"] == 1 for c in r.json())


def test_reset_password_signs_out_and_works(client, admin):
    email = f"rst-{uuid.uuid4().hex[:6]}@example.com"
    uid = client.post("/admin/users", headers=admin["hdr"], json={
        "email": email, "name": "Reset Me", "password": "oldpassword1"}).json()["id"]
    old = client.post("/auth/login", json={"email": email, "password": "oldpassword1"}).json()

    r = client.post(f"/admin/users/{uid}/reset-password", headers=admin["hdr"], json={})
    assert r.status_code == 200, r.text
    newpw = r.json()["password"]

    assert client.post("/auth/login", json={"email": email, "password": "oldpassword1"}).status_code == 401
    assert client.post("/auth/login", json={"email": email, "password": newpw}).status_code == 200
    # old refresh token revoked
    assert client.post("/auth/refresh", json={"refresh_token": old["refresh_token"]}).status_code == 401

    # explicit password
    r = client.post(f"/admin/users/{uid}/reset-password", headers=admin["hdr"],
                    json={"password": "chosenpass9"})
    assert r.json()["password"] == "chosenpass9"
    assert client.post("/auth/login", json={"email": email, "password": "chosenpass9"}).status_code == 200

    assert client.post(f"/admin/users/{uuid.uuid4()}/reset-password",
                       headers=admin["hdr"], json={}).status_code == 404


def test_disable_and_enable(client, admin):
    email = f"dis-{uuid.uuid4().hex[:6]}@example.com"
    uid = client.post("/admin/users", headers=admin["hdr"], json={
        "email": email, "name": "Disable Me", "password": "password123"}).json()["id"]
    tok = client.post("/auth/login", json={"email": email, "password": "password123"}).json()

    r = client.patch(f"/admin/users/{uid}", headers=admin["hdr"], json={"disabled": True})
    assert r.status_code == 200 and r.json()["disabled_at"]
    assert client.post("/auth/login", json={"email": email, "password": "password123"}).status_code == 403
    assert client.get("/me", headers=_auth(tok["access_token"])).status_code == 403

    r = client.patch(f"/admin/users/{uid}", headers=admin["hdr"], json={"disabled": False})
    assert r.json()["disabled_at"] is None
    assert client.post("/auth/login", json={"email": email, "password": "password123"}).status_code == 200

    # can't lock yourself out
    r = client.patch(f"/admin/users/{admin['user_id']}", headers=admin["hdr"], json={"disabled": True})
    assert r.status_code == 422
    r = client.patch(f"/admin/users/{admin['user_id']}", headers=admin["hdr"],
                     json={"is_platform_admin": False})
    assert r.status_code == 422


def test_admin_page_is_served(client):
    r = client.get("/web/admin.html")
    assert r.status_code == 200
    assert "Inspectit admin" in r.text
    assert r.headers.get("cache-control") == "no-cache"
