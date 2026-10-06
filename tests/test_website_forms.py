"""Marketing-site forms: free-trial sign-up, demo requests, admin triage."""
import uuid

import pytest


def _tag():
    return uuid.uuid4().hex[:8]


def _trial(client, **over):
    t = _tag()
    body = {"name": "Jane Smith", "email": f"trial-{t}@example.com",
            "password": "password123"}
    body.update(over)
    return client.post("/auth/trial-signup", json=body), body


# ---------- trial sign-up ----------

def test_trial_signup_minimal_individual(client):
    r, body = _trial(client)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["access_token"] and d["refresh_token"] and d["trial_ends_at"]
    me = client.get("/me", headers={"Authorization": "Bearer " + d["access_token"]})
    assert me.status_code == 200, me.text


def test_trial_dates_and_company_name(client):
    from api.db import get_pool
    r, _ = _trial(client, who="org", org="Acme Fleet", track="both", size="6-25")
    assert r.status_code == 200, r.text
    with get_pool().connection() as conn:
        c = conn.execute(
            """SELECT name, signup_info,
                      trial_ends_at > now() + interval '29 days' AS ok_start,
                      trial_ends_at < now() + interval '31 days' AS ok_end
               FROM companies WHERE id = %s""", (r.json()["company_id"],)).fetchone()
    assert c["name"] == "Acme Fleet"
    assert c["signup_info"] == {"track": "both", "who": "org", "size": "6-25"}
    assert c["ok_start"] and c["ok_end"]


def test_individual_gets_personal_company_name(client):
    from api.db import get_pool
    r, _ = _trial(client, name="Sam Lee", who="me")
    with get_pool().connection() as conn:
        name = conn.execute("SELECT name FROM companies WHERE id = %s",
                            (r.json()["company_id"],)).fetchone()["name"]
    assert name == "Sam Lee's account"


def test_org_chosen_but_blank_falls_back(client):
    from api.db import get_pool
    r, _ = _trial(client, name="Pat Doe", who="org", org="  ")
    with get_pool().connection() as conn:
        name = conn.execute("SELECT name FROM companies WHERE id = %s",
                            (r.json()["company_id"],)).fetchone()["name"]
    assert name == "Pat Doe's account"


def test_trial_duplicate_email_message(client):
    r, body = _trial(client)
    assert r.status_code == 200
    r2 = client.post("/auth/trial-signup", json=body)
    assert r2.status_code == 409
    assert r2.json()["detail"] == "That email already has an account. Sign in instead?"


@pytest.mark.parametrize("over,code", [
    ({"password": "short"}, 422),
    ({"email": "not-an-email"}, 422),
    ({"name": ""}, 422),
    ({"track": "boats"}, 422),
    ({"size": "7"}, 422),
])
def test_trial_validation(client, over, code):
    r, _ = _trial(client, **over)
    assert r.status_code == code, r.text


def test_plain_signup_has_no_trial(client):
    from api.db import get_pool
    t = _tag()
    r = client.post("/auth/signup", json={
        "company_name": f"Plain {t}", "name": "Plain", "email": f"plain-{t}@example.com",
        "password": "password123"})
    assert r.status_code == 200 and "trial_ends_at" not in r.json()
    with get_pool().connection() as conn:
        v = conn.execute("SELECT trial_ends_at FROM companies WHERE id = %s",
                         (r.json()["company_id"],)).fetchone()["trial_ends_at"]
    assert v is None


# ---------- demo requests ----------

@pytest.fixture()
def fresh_demo_limiter():
    from api.routers.public import demo_limiter
    demo_limiter.reset()
    yield demo_limiter
    demo_limiter.reset()


def test_demo_request_stored(client, fresh_demo_limiter):
    t = _tag()
    r = client.post("/public/demo-requests", json={
        "name": "Dana", "email": f"Dana-{t}@Example.com", "count": 12})
    assert r.status_code == 201 and r.json() == {"ok": True}
    from api.db import get_pool
    with get_pool().connection() as conn:
        row = conn.execute("SELECT * FROM demo_requests WHERE email = %s",
                           (f"dana-{t}@example.com",)).fetchone()
    assert row["name"] == "Dana" and row["asset_count"] == 12 and row["status"] == "new"


def test_demo_honeypot_stores_nothing(client, fresh_demo_limiter):
    t = _tag()
    r = client.post("/public/demo-requests", json={
        "name": "Bot", "email": f"bot-{t}@example.com", "website": "http://spam"})
    assert r.status_code == 201
    from api.db import get_pool
    with get_pool().connection() as conn:
        n = conn.execute("SELECT count(*) AS n FROM demo_requests WHERE email = %s",
                         (f"bot-{t}@example.com",)).fetchone()["n"]
    assert n == 0


@pytest.mark.parametrize("body", [
    {"name": "", "email": "a@b.co"},
    {"name": "A", "email": "nope"},
    {"name": "A", "email": "a@b.co", "count": 0},
    {"name": "A", "email": "a@b.co", "count": "many"},
])
def test_demo_validation(client, fresh_demo_limiter, body):
    assert client.post("/public/demo-requests", json=body).status_code == 422


def test_demo_rate_limited(client, fresh_demo_limiter):
    from api import config
    codes = [client.post("/public/demo-requests",
                         json={"name": "R", "email": f"r{i}@example.com"}).status_code
             for i in range(config.DEMO_RATE_LIMIT + 2)]
    assert codes[:config.DEMO_RATE_LIMIT] == [201] * config.DEMO_RATE_LIMIT
    assert codes[-1] == 429


# ---------- admin triage ----------

def test_admin_demo_requests_flow(client, fresh_demo_limiter):
    from api import config
    from api.db import get_pool
    from api.routers.admin import promote_platform_admins
    t = _tag()
    su = client.post("/auth/signup", json={
        "company_name": f"Ops {t}", "name": "Ops", "email": f"ops-{t}@example.com",
        "password": "password123"}).json()
    hdr = {"Authorization": "Bearer " + su["access_token"]}
    # not an admin yet
    assert client.get("/admin/demo-requests", headers=hdr).status_code == 403
    config.PLATFORM_ADMIN_EMAILS.append(f"ops-{t}@example.com")
    with get_pool().connection() as conn:
        promote_platform_admins(conn)

    client.post("/public/demo-requests", json={"name": "Lee", "email": f"lee-{t}@example.com"})
    rows = client.get("/admin/demo-requests?status=new", headers=hdr).json()
    mine = [r for r in rows if r["email"] == f"lee-{t}@example.com"]
    assert len(mine) == 1 and mine[0]["handled_at"] is None
    rid = mine[0]["id"]

    r = client.patch(f"/admin/demo-requests/{rid}", headers=hdr, json={"status": "contacted"})
    assert r.status_code == 200 and r.json()["status"] == "contacted" and r.json()["handled_at"]
    assert client.patch(f"/admin/demo-requests/{rid}", headers=hdr,
                        json={"status": "bogus"}).status_code == 422
    assert client.patch(f"/admin/demo-requests/{uuid.uuid4()}", headers=hdr,
                        json={"status": "closed"}).status_code == 404
    r = client.patch(f"/admin/demo-requests/{rid}", headers=hdr, json={"status": "new"})
    assert r.json()["handled_at"] is None


def test_admin_dashboard_fields_for_demos_and_trials(client):
    """List count header, stats counters and the company trial date the admin page shows."""
    from api import config
    from api.db import get_pool
    from api.routers.admin import promote_platform_admins
    from api.routers.public import demo_limiter
    demo_limiter.reset()
    t = _tag()
    su = client.post("/auth/signup", json={
        "company_name": f"Dash {t}", "name": "Dash", "email": f"dash-{t}@example.com",
        "password": "password123"}).json()
    hdr = {"Authorization": "Bearer " + su["access_token"]}
    config.PLATFORM_ADMIN_EMAILS.append(f"dash-{t}@example.com")
    with get_pool().connection() as conn:
        promote_platform_admins(conn)
    before = client.get("/admin/stats", headers=hdr).json()["totals"]
    client.post("/public/demo-requests", json={"name": "Q", "email": f"q-{t}@example.com"})
    r, _ = _trial(client, who="org", org=f"Trial Org {t}")
    after = client.get("/admin/stats", headers=hdr).json()["totals"]
    assert after["demo_requests_new"] == before["demo_requests_new"] + 1
    assert after["trials_active"] == before["trials_active"] + 1
    lst = client.get("/admin/demo-requests?status=new&limit=1", headers=hdr)
    assert lst.status_code == 200
    assert int(lst.headers["X-Total-Count"]) == after["demo_requests_new"]
    assert len(lst.json()) == 1
    cos = client.get(f"/admin/companies?q=Trial Org {t}", headers=hdr).json()
    assert len(cos) == 1 and cos[0]["trial_ends_at"] is not None
    demo_limiter.reset()
