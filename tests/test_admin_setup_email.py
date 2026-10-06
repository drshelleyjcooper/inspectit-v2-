"""Admin-created accounts: emailed set-password link instead of a handed-over password."""
import uuid

import pytest

PW = "password123"


def _tag():
    return uuid.uuid4().hex[:8]


@pytest.fixture()
def outbox(monkeypatch):
    from api import mailer
    box = []
    monkeypatch.setattr(mailer, "send_mail",
                        lambda subject, body, reply_to=None, to=None:
                        box.append({"subject": subject, "body": body, "to": to}) or True)
    return box


@pytest.fixture(scope="module")
def admin_hdr(client):
    from api import config
    from api.db import get_pool
    from api.routers.admin import promote_platform_admins
    t = _tag()
    d = client.post("/auth/signup", json={
        "company_name": f"Setup Admin {t}", "name": "Setup Admin",
        "email": f"setup-admin-{t}@example.com", "password": PW}).json()
    config.PLATFORM_ADMIN_EMAILS.append(f"setup-admin-{t}@example.com")
    with get_pool().connection() as conn:
        promote_platform_admins(conn)
    return {"Authorization": "Bearer " + d["access_token"]}


def _token(body):
    link = [w for w in body.split() if "reset-password?token=" in w][0]
    return link.split("token=")[1].split("&")[0], link


def _create(client, hdr, **over):
    t = _tag()
    body = {"name": f"New Admin {t}", "email": f"newadmin-{t}@example.com",
            "company_name": f"Fresh Co {t}", "send_setup_email": True}
    body.update(over)
    return client.post("/admin/users", headers=hdr, json=body), body


def test_create_user_emails_a_welcome_link_and_shows_no_password(client, admin_hdr, outbox):
    r, body = _create(client, admin_hdr)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["setup_email_sent"] is True
    assert "password" not in d and "setup_link" not in d      # nobody handles a password
    assert len(outbox) == 1
    m = outbox[0]
    assert m["to"] == body["email"]
    assert m["subject"] == "Welcome to Inspectit.app: set your password"
    assert body["company_name"] in m["body"] and "Company Administrator" in m["body"]
    assert "7 days" in m["body"]
    token, link = _token(m["body"])
    assert link.endswith("&welcome=1")


def test_welcome_link_sets_first_password_once_without_a_changed_notice(client, admin_hdr, outbox):
    r, body = _create(client, admin_hdr)
    token, _ = _token(outbox[0]["body"])
    outbox.clear()
    assert client.post("/auth/reset", json={"token": token, "password": "myfirstpass1"}).status_code == 200
    assert outbox == []                                       # no "password changed" email for a welcome
    login = client.post("/auth/login", json={"email": body["email"], "password": "myfirstpass1"})
    assert login.status_code == 200
    me = client.get("/me", headers={"Authorization": "Bearer " + login.json()["access_token"]}).json()
    assert me["memberships"][0]["company_name"] == body["company_name"]
    assert client.post("/auth/reset", json={"token": token, "password": "another12345"}).status_code == 400


def test_welcome_link_expires(client, admin_hdr, outbox):
    from api import security
    from api.db import get_pool
    r, body = _create(client, admin_hdr)
    token, _ = _token(outbox[0]["body"])
    with get_pool().connection() as conn:
        conn.execute("UPDATE password_resets SET expires_at = now() - interval '1 minute' WHERE token_hash = %s",
                     (security.sha256(token),))
    assert client.post("/auth/reset", json={"token": token, "password": "toolate12345"}).status_code == 400


def test_unsendable_email_falls_back_to_the_link(client, admin_hdr, monkeypatch):
    from api import mailer
    monkeypatch.setattr(mailer, "send_mail", lambda *a, **k: False)
    r, body = _create(client, admin_hdr)
    d = r.json()
    assert r.status_code == 201 and d["setup_email_sent"] is False
    assert "reset-password?token=" in d["setup_link"] and d["setup_link"].endswith("&welcome=1")
    token = d["setup_link"].split("token=")[1].split("&")[0]
    assert client.post("/auth/reset", json={"token": token, "password": "viafallback1"}).status_code == 200


def test_setup_email_and_password_together_are_refused(client, admin_hdr, outbox):
    r, _ = _create(client, admin_hdr, password="chosen-password-1")
    assert r.status_code == 422 and outbox == []


def test_without_the_option_behaviour_is_unchanged(client, admin_hdr, outbox):
    r, body = _create(client, admin_hdr, send_setup_email=False)
    d = r.json()
    assert r.status_code == 201 and d["password"] and "setup_email_sent" not in d
    assert outbox == []
    assert client.post("/auth/login", json={"email": body["email"], "password": d["password"]}).status_code == 200


def test_user_without_company_gets_a_generic_welcome(client, admin_hdr, outbox):
    t = _tag()
    r = client.post("/admin/users", headers=admin_hdr, json={
        "name": "No Co", "email": f"noco-{t}@example.com", "send_setup_email": True})
    assert r.status_code == 201 and r.json()["setup_email_sent"] is True
    assert "Your role" not in outbox[0]["body"]


# ---------- emailed reset link for an existing user ----------

def test_admin_can_email_a_reset_link_without_changing_the_password(client, admin_hdr, outbox):
    r, body = _create(client, admin_hdr, send_setup_email=False)
    pw = r.json()["password"]
    uid = r.json()["id"]
    outbox.clear()
    r2 = client.post(f"/admin/users/{uid}/reset-password", headers=admin_hdr, json={"send_email": True})
    assert r2.status_code == 200 and r2.json()["setup_email_sent"] is True
    assert "password" not in r2.json()
    m = outbox[0]
    assert m["to"] == body["email"] and m["subject"] == "Reset your Inspectit.app password"
    assert "7 days" in m["body"]
    # nothing changed yet: the old password still works
    assert client.post("/auth/login", json={"email": body["email"], "password": pw}).status_code == 200
    token, link = _token(m["body"])
    assert "welcome=1" not in link
    outbox.clear()
    assert client.post("/auth/reset", json={"token": token, "password": "freshpass1234"}).status_code == 200
    assert len(outbox) == 1 and "changed" in outbox[0]["subject"].lower()   # a real reset still notifies
    assert client.post("/auth/login", json={"email": body["email"], "password": pw}).status_code == 401


def test_emailed_reset_link_fallback_refusals_and_defaults(client, admin_hdr, outbox, monkeypatch):
    from api import mailer
    r, body = _create(client, admin_hdr, send_setup_email=False)
    uid = r.json()["id"]
    assert client.post(f"/admin/users/{uid}/reset-password", headers=admin_hdr,
                       json={"send_email": True, "password": "chosen-password-1"}).status_code == 422
    assert client.post(f"/admin/users/{uuid.uuid4()}/reset-password", headers=admin_hdr,
                       json={"send_email": True}).status_code == 404
    monkeypatch.setattr(mailer, "send_mail", lambda *a, **k: False)
    d = client.post(f"/admin/users/{uid}/reset-password", headers=admin_hdr, json={"send_email": True}).json()
    assert d["setup_email_sent"] is False and "reset-password?token=" in d["setup_link"]
    client.patch(f"/admin/users/{uid}", headers=admin_hdr, json={"disabled": True})
    assert client.post(f"/admin/users/{uid}/reset-password", headers=admin_hdr,
                       json={"send_email": True}).status_code == 409
    # the original "set a password here" mode still works
    r3, b3 = _create(client, admin_hdr, send_setup_email=False)
    p = client.post(f"/admin/users/{r3.json()['id']}/reset-password", headers=admin_hdr, json={})
    assert p.status_code == 200 and p.json()["password"]


def test_human_duration():
    from api.mailer import human_duration as h
    assert [h(60), h(30), h(120), h(1440), h(2880), h(7 * 1440)] == [
        "60 minutes", "30 minutes", "2 hours", "1 day", "2 days", "7 days"]
