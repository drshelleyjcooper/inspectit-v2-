"""Invitation emails: sent to the invitee with a working link, Reply-To the
inviter, rate-limited without blocking the invitation, nothing on failure."""
import uuid

import pytest

PW = "test-password-123"


@pytest.fixture()
def outbox(monkeypatch):
    from api import mailer
    box = []
    monkeypatch.setattr(mailer, "send_mail",
                        lambda subject, body, reply_to=None, to=None:
                        box.append({"subject": subject, "body": body,
                                    "reply_to": reply_to, "to": to}) or True)
    mailer.invite_mail_limiter.reset()
    yield box
    mailer.invite_mail_limiter.reset()


def _company(client):
    tag = uuid.uuid4().hex[:8]
    email = f"inv-admin-{tag}@example.com"
    r = client.post("/auth/signup", json={
        "company_name": f"Invite Co {tag}", "name": "Ada Admin", "email": email,
        "password": PW})
    d = r.json()
    hdr = {"Authorization": "Bearer " + d["access_token"]}
    roles = {x["name"]: x["id"] for x in
             client.get(f"/companies/{d['company_id']}/roles", headers=hdr).json()}
    return d["company_id"], hdr, email, roles, f"Invite Co {tag}"


def _invite(client, cid, hdr, to, role_id):
    return client.post(f"/companies/{cid}/invitations", headers=hdr,
                       json={"email": to, "role_ids": [role_id]})


def test_invitee_gets_email_with_working_link(client, outbox):
    from api import config
    cid, hdr, admin_email, roles, cname = _company(client)
    to = f"newbie-{uuid.uuid4().hex[:8]}@example.com"
    r = _invite(client, cid, hdr, to, roles["Viewer"])
    assert r.status_code == 200, r.text
    assert len(outbox) == 1
    m = outbox[0]
    assert m["to"] == to and m["reply_to"] == admin_email
    assert "Ada Admin" in m["subject"] and cname in m["subject"]
    assert "Viewer" in m["body"] and "14 days" in m["body"]
    link = [w for w in m["body"].split() if "?invite=" in w][0]
    assert link == f"{config.APP_BASE_URL}/web/inspectit-app.html?invite=" + r.json()["token"]
    # the emailed token really accepts
    acc = client.post("/auth/invitations/accept", json={
        "token": r.json()["token"], "name": "Nia Newbie", "password": PW})
    assert acc.status_code == 200, acc.text


def test_refused_invitation_sends_no_email(client, outbox):
    cid, hdr, _, roles, _ = _company(client)
    bad = client.post(f"/companies/{cid}/invitations", headers=hdr,
                      json={"email": "not-an-email", "role_ids": [roles["Viewer"]]})
    assert bad.status_code == 422
    nobody = client.post(f"/companies/{cid}/invitations", headers=hdr,
                         json={"email": f"x-{uuid.uuid4().hex[:6]}@example.com", "role_ids": []})
    assert nobody.status_code == 422
    assert outbox == []


def test_reinvite_supersedes_and_emails_new_link(client, outbox):
    cid, hdr, _, roles, _ = _company(client)
    to = f"twice-{uuid.uuid4().hex[:8]}@example.com"
    t1 = _invite(client, cid, hdr, to, roles["Viewer"]).json()["token"]
    # same role again is refused (nothing new to add) and sends nothing
    assert _invite(client, cid, hdr, to, roles["Viewer"]).status_code == 409
    assert len(outbox) == 1
    # a different role supersedes the pending invitation and emails the new link
    t2 = _invite(client, cid, hdr, to, roles["Vehicle Inspector"]).json()["token"]
    assert t1 != t2 and len(outbox) == 2
    assert t2 in outbox[1]["body"] and t1 not in outbox[1]["body"]
    assert client.post("/auth/invitations/accept",
                       json={"token": t1, "name": "A", "password": PW}).status_code == 400


def test_email_budget_skips_email_but_not_invitation(client, outbox, monkeypatch):
    from api import mailer
    from api.ratelimit import RateLimiter
    monkeypatch.setattr(mailer, "invite_mail_limiter", RateLimiter(2, 3600))
    cid, hdr, _, roles, _ = _company(client)
    codes = []
    for i in range(4):
        r = _invite(client, cid, hdr, f"burst{i}-{uuid.uuid4().hex[:6]}@example.com", roles["Viewer"])
        codes.append(r.status_code)
        assert r.json()["token"]            # invitation still created
    assert codes == [200] * 4
    assert len(outbox) == 2                 # only the budgeted ones were emailed


def test_hostile_names_cannot_inject_headers(client):
    from api import mailer
    s, b = mailer.invitation_email("Eve\r\nBcc: evil@x.y", "Co\r\nBcc: z@x.y",
                                   ["Viewer"], "https://inspectit.app/x", 14)
    msg = mailer.build_message(s, b, reply_to="eve@x.y")
    assert "\n" not in msg["Subject"] and msg["Bcc"] is None
