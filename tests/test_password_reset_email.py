"""Password-reset emails: sent to the account (not the owner), no account
enumeration, link points at our pages, 'changed' notice, pages served safely."""
import uuid

import pytest


@pytest.fixture()
def outbox(monkeypatch):
    from api import mailer
    box = []
    monkeypatch.setattr(mailer, "send_mail",
                        lambda subject, body, reply_to=None, to=None:
                        box.append({"subject": subject, "body": body, "to": to}) or True)
    return box


def _user(client, tag=None, name="Rita Reset"):
    tag = tag or uuid.uuid4().hex[:8]
    email = f"reset-{tag}@example.com"
    r = client.post("/auth/signup", json={
        "company_name": f"Reset Co {tag}", "name": name, "email": email,
        "password": "oldpassword1"})
    assert r.status_code == 200, r.text
    return email


def test_forgot_emails_the_account_with_a_working_link(client, outbox):
    from api import config
    email = _user(client)
    r = client.post("/auth/forgot", json={"email": email})
    assert r.status_code == 200
    assert len(outbox) == 1
    m = outbox[0]
    assert m["to"] == email                      # to the user, not info@
    assert "Rita Reset" in m["body"]
    link = [w for w in m["body"].split() if "reset-password?token=" in w][0]
    assert link.startswith(config.APP_BASE_URL + "/reset-password?token=")
    token = link.split("token=")[1]
    assert token == r.json()["dev_reset_token"]   # DEV_MODE echo matches the emailed one
    assert "60 minutes" in m["body"]


def test_unknown_email_sends_nothing_but_looks_identical(client, outbox):
    known = _user(client)
    a = client.post("/auth/forgot", json={"email": known})
    n_after_known = len(outbox)
    b = client.post("/auth/forgot", json={"email": f"nobody-{uuid.uuid4().hex}@example.com"})
    c = client.post("/auth/forgot", json={"email": "not-an-email"})
    assert a.status_code == b.status_code == c.status_code == 200
    assert len(outbox) == n_after_known           # nothing extra sent
    assert "dev_reset_token" not in b.json()


def test_disabled_user_gets_no_reset_email(client, outbox):
    from api.db import get_pool
    email = _user(client)
    with get_pool().connection() as conn:
        conn.execute("UPDATE users SET disabled_at = now() WHERE email = %s", (email,))
    assert client.post("/auth/forgot", json={"email": email}).status_code == 200
    assert outbox == []


def test_reset_sends_changed_notice_and_link_is_single_use(client, outbox):
    email = _user(client)
    token = client.post("/auth/forgot", json={"email": email}).json()["dev_reset_token"]
    outbox.clear()
    assert client.post("/auth/reset", json={"token": token, "password": "brandnew123"}).status_code == 200
    assert len(outbox) == 1 and outbox[0]["to"] == email
    assert "changed" in outbox[0]["subject"].lower()
    assert "brandnew123" not in outbox[0]["body"]
    outbox.clear()
    assert client.post("/auth/reset", json={"token": token, "password": "another1234"}).status_code == 400
    assert outbox == []
    assert client.post("/auth/login", json={"email": email, "password": "brandnew123"}).status_code == 200


def test_mailer_to_override_and_default(monkeypatch):
    from api import config, mailer
    monkeypatch.setattr(config, "RESEND_API_KEY", "")
    monkeypatch.setattr(config, "SMTP_PASSWORD", "x")
    m = mailer.build_message("s", "b", to="user@example.com\r\nBcc: evil@x.y")
    assert m["To"] == "user@example.com Bcc: evil@x.y" or "\n" not in m["To"]
    assert mailer.build_message("s", "b")["To"] == "info@inspectit.app"


def test_pages_served_with_privacy_headers(client):
    for path in ("/forgot-password", "/reset-password"):
        r = client.get(path + "?token=abc")
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["x-robots-tag"] == "noindex"
        assert r.headers["cache-control"] == "no-cache"
        assert 'name="robots" content="noindex"' in r.text
    assert 'id="reset-form"' in client.get("/reset-password").text
    assert 'id="forgot-form"' in client.get("/forgot-password").text
