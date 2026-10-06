"""Trial lifecycle: 7-day reminder, suspension at the end, lift on subscribe/extend."""
import datetime as dt
import uuid

import pytest

PW = "password123"


def _tag():
    return uuid.uuid4().hex[:8]


@pytest.fixture()
def outbox(monkeypatch):
    from api import config, mailer
    box = []
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_not_real")   # enables the cycle
    monkeypatch.setattr(mailer, "send_mail",
                        lambda subject, body, reply_to=None, to=None:
                        box.append({"subject": subject, "body": body, "to": to}) or True)
    return box


@pytest.fixture(scope="module")
def platform_admin(client):
    from api import config
    from api.db import get_pool
    from api.routers.admin import promote_platform_admins
    t = _tag()
    d = client.post("/auth/signup", json={
        "company_name": f"Plat {t}", "name": "Plat", "email": f"plat-{t}@example.com",
        "password": PW}).json()
    config.PLATFORM_ADMIN_EMAILS.append(f"plat-{t}@example.com")
    with get_pool().connection() as conn:
        promote_platform_admins(conn)
    return {"Authorization": "Bearer " + d["access_token"]}


def _trial_company(client, days_left):
    """A website-style trial company with an admin, ending in `days_left` days."""
    from api.db import get_pool
    t = _tag()
    r = client.post("/auth/trial-signup", json={
        "name": f"Owner {t}", "email": f"owner-{t}@example.com", "password": PW,
        "who": "org", "org": f"Trial Co {t}"})
    assert r.status_code == 200, r.text
    d = r.json()
    set_trial_end(d["company_id"], days_left)
    return {"cid": d["company_id"], "email": f"owner-{t}@example.com",
            "hdr": {"Authorization": "Bearer " + d["access_token"]}, "tag": t}


def set_trial_end(cid, days_left):
    from api.db import get_pool
    with get_pool().connection() as conn:
        conn.execute(
            """UPDATE companies SET trial_ends_at = now() + make_interval(secs => %s),
                      trial_reminder_sent_at = NULL, trial_ended_notice_sent_at = NULL
               WHERE id = %s""", (days_left * 86400, cid))


def _run():
    from api import trial
    return trial.run_trial_cycle()


def _to(outbox, email):
    return [m for m in outbox if m["to"] == email]


# ---------- pure state ----------

def test_access_state_pure():
    from api import config
    from api.trial import access_state, blocked
    now = dt.datetime.now(dt.timezone.utc)
    d = lambda n: dt.timedelta(days=n)
    g, r = config.TRIAL_GRACE_DAYS, config.DATA_RETENTION_DAYS
    assert (g, r) == (7, 30)
    assert access_state(None, None) == "none"
    assert access_state(now + d(1), None) == "trialing"
    assert access_state(now - d(1), None) == "grace"
    assert access_state(now - d(g - 1), None) == "grace"
    assert access_state(now - d(g + 1), None) == "suspended"
    assert access_state(now - d(g + r - 1), None) == "suspended"
    assert access_state(now - d(g + r + 1), None) == "expired"
    assert access_state(now - d(99), now) == "subscribed"
    assert access_state(None, now) == "subscribed"
    assert [blocked(x) for x in ("trialing", "grace", "suspended", "expired")] == [False, False, True, True]


# ---------- suspension ----------

def test_active_trial_has_access_and_suspended_does_not(client):
    c = _trial_company(client, 10)
    assert client.get(f"/companies/{c['cid']}/members", headers=c["hdr"]).status_code == 200
    set_trial_end(c["cid"], -1)                       # ended yesterday: grace, still full access
    assert client.get(f"/companies/{c['cid']}/members", headers=c["hdr"]).status_code == 200
    me = client.get("/me", headers=c["hdr"]).json()["memberships"][0]
    assert me["access"] == "grace" and me["grace_ends_at"]
    set_trial_end(c["cid"], -8)                       # grace over: paused
    r = client.get(f"/companies/{c['cid']}/members", headers=c["hdr"])
    assert r.status_code == 402
    assert "trial has ended" in r.json()["detail"]
    me = client.get("/me", headers=c["hdr"]).json()["memberships"][0]
    assert me["access"] == "suspended" and me["subscribed"] is False   # /me still works


def test_company_without_trial_is_never_suspended(client):
    t = _tag()
    d = client.post("/auth/signup", json={"company_name": f"Legacy {t}", "name": "L",
                                          "email": f"legacy-{t}@example.com", "password": PW}).json()
    hdr = {"Authorization": "Bearer " + d["access_token"]}
    assert client.get(f"/companies/{d['company_id']}/members", headers=hdr).status_code == 200
    assert client.get("/me", headers=hdr).json()["memberships"][0]["access"] == "none"


def test_subscribing_lifts_suspension_and_unsubscribing_restores_it(client, platform_admin):
    c = _trial_company(client, -9)
    url = f"/companies/{c['cid']}/members"
    assert client.get(url, headers=c["hdr"]).status_code == 402
    r = client.patch(f"/admin/companies/{c['cid']}/subscription", headers=platform_admin,
                     json={"active": True})
    assert r.status_code == 200 and r.json()["access"] == "subscribed"
    assert client.get(url, headers=c["hdr"]).status_code == 200
    client.patch(f"/admin/companies/{c['cid']}/subscription", headers=platform_admin,
                 json={"active": False})
    assert client.get(url, headers=c["hdr"]).status_code == 402


def test_extend_trial_lifts_suspension_and_rearms_emails(client, platform_admin, outbox):
    c = _trial_company(client, -1)
    _run()
    assert len(_to(outbox, c["email"])) == 1 and "ended" in _to(outbox, c["email"])[0]["subject"]
    set_trial_end(c["cid"], -9)
    assert client.get(f"/companies/{c['cid']}/members", headers=c["hdr"]).status_code == 402
    r = client.post(f"/admin/companies/{c['cid']}/extend-trial", headers=platform_admin,
                    json={"days": 14})
    assert r.status_code == 200 and r.json()["access"] == "trialing"
    assert client.get(f"/companies/{c['cid']}/members", headers=c["hdr"]).status_code == 200
    assert client.post(f"/admin/companies/{c['cid']}/extend-trial", headers=platform_admin,
                       json={"days": 0}).status_code == 422
    assert client.post(f"/admin/companies/{uuid.uuid4()}/extend-trial", headers=platform_admin,
                       json={"days": 5}).status_code == 404


def test_admin_company_actions_require_platform_admin(client):
    c = _trial_company(client, 5)
    assert client.patch(f"/admin/companies/{c['cid']}/subscription", headers=c["hdr"],
                        json={"active": True}).status_code == 403


def test_admin_list_shows_access_state(client, platform_admin):
    c = _trial_company(client, -10)
    rows = client.get(f"/admin/companies?q=Trial Co {c['tag']}", headers=platform_admin).json()
    assert rows[0]["access"] == "suspended" and rows[0]["subscribed_at"] is None


# ---------- reminder + ended emails ----------

def test_reminder_within_a_week_sent_once_to_admins(client, outbox):
    c = _trial_company(client, 6)
    far = _trial_company(client, 20)
    res = _run()
    mine = _to(outbox, c["email"])
    assert len(mine) == 1
    assert "ends in a week" in mine[0]["subject"]
    assert f"Trial Co {c['tag']}" in mine[0]["body"]
    assert "#pricing" in mine[0]["body"]
    assert _to(outbox, far["email"]) == []
    outbox.clear()
    _run()                                   # second pass: nothing new for c
    assert _to(outbox, c["email"]) == []


def test_only_company_admins_are_emailed(client, outbox):
    c = _trial_company(client, 3)
    roles = {x["name"]: x["id"] for x in
             client.get(f"/companies/{c['cid']}/roles", headers=c["hdr"]).json()}
    viewer = f"viewer-{_tag()}@example.com"
    inv = client.post(f"/companies/{c['cid']}/invitations", headers=c["hdr"],
                      json={"email": viewer, "role_ids": [roles["Viewer"]]}).json()
    assert client.post("/auth/invitations/accept", json={
        "token": inv["token"], "name": "Vi", "password": PW}).status_code == 200
    outbox.clear()
    _run()
    assert len(_to(outbox, c["email"])) == 1
    assert _to(outbox, viewer) == []


def test_subscribed_company_gets_no_reminder_or_ended_notice(client, platform_admin, outbox):
    c = _trial_company(client, 2)
    client.patch(f"/admin/companies/{c['cid']}/subscription", headers=platform_admin,
                 json={"active": True})
    set_trial_end(c["cid"], 2)
    _run()
    set_trial_end(c["cid"], -40)
    _run()
    assert _to(outbox, c["email"]) == []


def test_ended_notice_sent_once(client, outbox):
    c = _trial_company(client, -1)
    _run(); _run()
    ended = _to(outbox, c["email"])
    assert len(ended) == 1 and "has ended" in ended[0]["subject"]
    assert "keep using Inspectit.app until" in ended[0]["body"]      # grace, not a cut-off
    assert "not deleted" in ended[0]["body"]


def test_failed_send_releases_claim_for_retry(client, monkeypatch):
    from api import config, mailer
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_not_real")
    c = _trial_company(client, 4)
    monkeypatch.setattr(mailer, "send_mail", lambda *a, **k: False)
    _run()
    got = []
    monkeypatch.setattr(mailer, "send_mail",
                        lambda s, b, r=None, to=None: got.append(to) or True)
    _run()
    assert c["email"] in got                  # retried and delivered


def test_cycle_skipped_when_email_not_configured(client, monkeypatch):
    from api import config
    monkeypatch.setattr(config, "RESEND_API_KEY", "")
    monkeypatch.setattr(config, "SMTP_PASSWORD", "")
    c = _trial_company(client, 3)
    assert _run() == {"skipped": True}
    from api.db import get_pool
    with get_pool().connection() as conn:     # nothing was claimed, so nothing is lost
        v = conn.execute("SELECT trial_reminder_sent_at FROM companies WHERE id=%s",
                         (c["cid"],)).fetchone()["trial_reminder_sent_at"]
    assert v is None


# ---------- grace -> paused -> deletion warning -> removal ----------

def test_full_timeline_emails_in_order_each_once(client, outbox):
    c = _trial_company(client, -1)                     # just ended
    _run()
    subjects = [m["subject"] for m in _to(outbox, c["email"])]
    assert subjects == ["Your Inspectit.app free trial has ended"]
    set_trial_end(c["cid"], -8)                        # grace over
    _prep_markers(c["cid"], ended=True)
    outbox.clear(); _run(); _run()
    paused = _to(outbox, c["email"])
    assert [m["subject"] for m in paused] == ["Your Inspectit.app account is paused"]
    assert "kept safe until" in paused[0]["body"]
    set_trial_end(c["cid"], -31)                       # a week before deletion
    _prep_markers(c["cid"], ended=True, paused=True)
    outbox.clear(); _run(); _run()
    warn = _to(outbox, c["email"])
    assert [m["subject"] for m in warn] == ["Action needed: your Inspectit.app records will be deleted"]
    assert "will be deleted on" in warn[0]["body"]


def _prep_markers(cid, ended=False, paused=False, warned=False):
    from api.db import get_pool
    with get_pool().connection() as conn:
        conn.execute(
            """UPDATE companies SET
                 trial_ended_notice_sent_at = CASE WHEN %s THEN now() END,
                 suspended_notice_sent_at   = CASE WHEN %s THEN now() END,
                 deletion_warning_sent_at   = CASE WHEN %s THEN now() - interval '8 days' END
               WHERE id = %s""", (ended, paused, warned, cid))


def _is_deleted(cid):
    from api.db import get_pool
    with get_pool().connection() as conn:
        return conn.execute("SELECT deleted_at FROM companies WHERE id = %s",
                            (cid,)).fetchone()["deleted_at"] is not None


def test_nothing_is_removed_unless_purge_switch_is_on(client, outbox):
    c = _trial_company(client, -40)
    _prep_markers(c["cid"], ended=True, paused=True, warned=True)
    res = _run()
    assert res["removed"] == 0 and not _is_deleted(c["cid"])
    assert client.get("/me", headers=c["hdr"]).json()["memberships"][0]["access"] == "expired"
    assert client.get(f"/companies/{c['cid']}/members", headers=c["hdr"]).status_code == 402


def test_purge_removes_only_warned_expired_unsubscribed_companies(client, outbox, monkeypatch,
                                                                  platform_admin):
    from api import config
    monkeypatch.setattr(config, "RETENTION_PURGE_ENABLED", True)
    gone = _trial_company(client, -40);       _prep_markers(gone["cid"], True, True, True)
    unwarned = _trial_company(client, -40);   _prep_markers(unwarned["cid"], True, True, False)
    paying = _trial_company(client, -40);     _prep_markers(paying["cid"], True, True, True)
    client.patch(f"/admin/companies/{paying['cid']}/subscription", headers=platform_admin,
                 json={"active": True})
    inside = _trial_company(client, -20);     _prep_markers(inside["cid"], True, True, True)
    _run()
    assert _is_deleted(gone["cid"])
    assert not _is_deleted(unwarned["cid"])      # never removed without the warning email
    assert not _is_deleted(paying["cid"])
    assert not _is_deleted(inside["cid"])        # still inside the 30 days
    # a removed company is gone for its members
    assert client.get(f"/companies/{gone['cid']}/members", headers=gone["hdr"]).status_code == 404
    assert client.get("/me", headers=gone["hdr"]).json()["memberships"] == []


def test_never_warned_and_removed_in_the_same_pass(client, outbox, monkeypatch):
    """A long-expired company gets the warning first and keeps a full week of notice."""
    from api import config
    monkeypatch.setattr(config, "RETENTION_PURGE_ENABLED", True)
    c = _trial_company(client, -60)
    _prep_markers(c["cid"], ended=True, paused=True, warned=False)
    _run()
    assert not _is_deleted(c["cid"])
    assert any("will be deleted" in m["subject"] for m in _to(outbox, c["email"]))
    from api.db import get_pool
    with get_pool().connection() as conn:                     # a week later
        conn.execute("UPDATE companies SET deletion_warning_sent_at = now() - interval '8 days' WHERE id = %s", (c["cid"],))
    _run()
    assert _is_deleted(c["cid"])


def test_extend_trial_rearms_every_stage(client, platform_admin, outbox):
    c = _trial_company(client, -40)
    _prep_markers(c["cid"], True, True, True)
    client.post(f"/admin/companies/{c['cid']}/extend-trial", headers=platform_admin, json={"days": 30})
    from api.db import get_pool
    with get_pool().connection() as conn:
        r = conn.execute("""SELECT trial_reminder_sent_at, trial_ended_notice_sent_at,
                                   suspended_notice_sent_at, deletion_warning_sent_at
                            FROM companies WHERE id = %s""", (c["cid"],)).fetchone()
    assert all(v is None for v in r.values())
    assert client.get(f"/companies/{c['cid']}/members", headers=c["hdr"]).status_code == 200


def test_admin_stats_count_grace_and_suspended(client, platform_admin):
    before = client.get("/admin/stats", headers=platform_admin).json()["totals"]
    _trial_company(client, -2)      # grace
    _trial_company(client, -10)     # suspended
    after = client.get("/admin/stats", headers=platform_admin).json()["totals"]
    assert after["trials_grace"] == before["trials_grace"] + 1
    assert after["trials_suspended"] == before["trials_suspended"] + 1


# ---------- complimentary (test / comped) accounts ----------

def _create_comped(client, platform_admin, **extra):
    t = _tag()
    body = {"name": f"Tester {t}", "email": f"tester-{t}@example.com",
            "company_name": f"Comped Co {t}", "complimentary": True}
    body.update(extra)
    r = client.post("/admin/users", headers=platform_admin, json=body)
    return r, body, t


def _login(client, email, password):
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def test_admin_can_create_a_complimentary_user(client, platform_admin):
    r, body, t = _create_comped(client, platform_admin)
    assert r.status_code == 201, r.text
    hdr = _login(client, body["email"], r.json()["password"])
    me = client.get("/me", headers=hdr).json()["memberships"][0]
    assert me["access"] == "complimentary" and me["company_name"] == body["company_name"]
    assert client.get(f"/companies/{me['company_id']}/members", headers=hdr).status_code == 200
    rows = client.get(f"/admin/companies?q={body['company_name']}", headers=platform_admin).json()
    assert rows[0]["complimentary"] is True and rows[0]["access"] == "complimentary"


def test_complimentary_is_never_paywalled_even_with_an_old_trial_date(client, platform_admin):
    r, body, _ = _create_comped(client, platform_admin)
    hdr = _login(client, body["email"], r.json()["password"])
    cid = client.get("/me", headers=hdr).json()["memberships"][0]["company_id"]
    set_trial_end(cid, -400)                          # long past retention too
    assert client.get(f"/companies/{cid}/members", headers=hdr).status_code == 200
    assert client.get("/me", headers=hdr).json()["memberships"][0]["access"] == "complimentary"


def test_complimentary_needs_a_new_company(client, platform_admin):
    t = _tag()
    no_co = client.post("/admin/users", headers=platform_admin, json={
        "name": "X", "email": f"x-{t}@example.com", "complimentary": True})
    assert no_co.status_code == 422
    existing = _trial_company(client, 5)
    on_existing = client.post("/admin/users", headers=platform_admin, json={
        "name": "Y", "email": f"y-{t}@example.com", "company_id": existing["cid"],
        "complimentary": True})
    assert on_existing.status_code == 422
    # the existing company was not touched
    assert client.get("/me", headers=existing["hdr"]).json()["memberships"][0]["access"] == "trialing"


def test_toggle_complimentary_on_an_existing_company(client, platform_admin):
    c = _trial_company(client, -9)                    # paused
    url = f"/companies/{c['cid']}/members"
    assert client.get(url, headers=c["hdr"]).status_code == 402
    r = client.patch(f"/admin/companies/{c['cid']}/complimentary", headers=platform_admin,
                     json={"active": True})
    assert r.status_code == 200 and r.json()["access"] == "complimentary"
    assert client.get(url, headers=c["hdr"]).status_code == 200
    r = client.patch(f"/admin/companies/{c['cid']}/complimentary", headers=platform_admin,
                     json={"active": False})
    assert r.json()["access"] == "suspended"
    assert client.get(url, headers=c["hdr"]).status_code == 402
    assert client.patch(f"/admin/companies/{uuid.uuid4()}/complimentary", headers=platform_admin,
                        json={"active": True}).status_code == 404
    assert client.patch(f"/admin/companies/{c['cid']}/complimentary", headers=c["hdr"],
                        json={"active": True}).status_code == 403


def test_trial_emails_and_removal_skip_complimentary(client, platform_admin, outbox, monkeypatch):
    from api import config
    monkeypatch.setattr(config, "RETENTION_PURGE_ENABLED", True)
    c = _trial_company(client, -60)
    _prep_markers(c["cid"], True, True, True)
    client.patch(f"/admin/companies/{c['cid']}/complimentary", headers=platform_admin,
                 json={"active": True})
    soon = _trial_company(client, 3)                  # would get a reminder
    client.patch(f"/admin/companies/{soon['cid']}/complimentary", headers=platform_admin,
                 json={"active": True})
    outbox.clear()
    _run()
    assert not _is_deleted(c["cid"])
    assert _to(outbox, c["email"]) == [] and _to(outbox, soon["email"]) == []


def test_stats_count_complimentary_and_exclude_them_from_trials(client, platform_admin):
    before = client.get("/admin/stats", headers=platform_admin).json()["totals"]
    c = _trial_company(client, 10)                    # counts as an active trial...
    mid = client.get("/admin/stats", headers=platform_admin).json()["totals"]
    assert mid["trials_active"] == before["trials_active"] + 1
    client.patch(f"/admin/companies/{c['cid']}/complimentary", headers=platform_admin,
                 json={"active": True})                # ...until it is made complimentary
    after = client.get("/admin/stats", headers=platform_admin).json()["totals"]
    assert after["trials_active"] == before["trials_active"]
    assert after["complimentary"] == before["complimentary"] + 1
