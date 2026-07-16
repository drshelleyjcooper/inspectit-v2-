"""F1–F3 hardening tests. Named zz_ so the rate-limit integration test runs
after the rest of the suite (it tightens the shared limiter)."""
import time

import pytest

STATE = {}


# ---------- F2: production config gate (pure function) ----------

def test_production_gate():
    from api.config import check_production_config as check

    # development: anything goes
    check(False, None, True, ["*"])

    # production, fully configured: passes
    check(True, "secret", False, ["https://app.example.com"])

    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        check(True, None, False, ["https://app.example.com"])
    with pytest.raises(RuntimeError, match="DEV_MODE"):
        check(True, "secret", True, ["https://app.example.com"])
    with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS"):
        check(True, "secret", False, ["*"])
    with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS"):
        check(True, "secret", False, [])


# ---------- F1: origin parsing + dev default ----------

def test_parse_origins():
    from api.config import parse_origins
    assert parse_origins("https://a.com, https://b.com") == \
        ["https://a.com", "https://b.com"]
    assert parse_origins("") == []
    assert parse_origins(" , ") == []


def test_cors_dev_default_wildcard(client):
    r = client.options("/auth/login", headers={
        "Origin": "http://localhost:8765",
        "Access-Control-Request-Method": "POST"})
    assert r.headers.get("access-control-allow-origin") == "*"


# ---------- F3: rate limiter ----------

def test_rate_limiter_window_expiry():
    from api.ratelimit import RateLimiter
    from fastapi import HTTPException

    rl = RateLimiter(max_requests=2, window_s=0.05)
    rl.check("ip1"); rl.check("ip1")
    with pytest.raises(HTTPException) as exc:
        rl.check("ip1")
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers
    rl.check("ip2")                # other clients unaffected
    time.sleep(0.06)
    rl.check("ip1")                # window expired -> allowed again


def test_auth_routes_are_rate_limited(client):
    from api.ratelimit import auth_limiter

    orig_max = auth_limiter.max_requests
    auth_limiter.reset()
    auth_limiter.max_requests = 3
    try:
        for _ in range(3):
            r = client.post("/auth/login", json={
                "email": "nobody@example.com", "password": "wrong"})
            assert r.status_code == 401
        r = client.post("/auth/login", json={
            "email": "nobody@example.com", "password": "wrong"})
        assert r.status_code == 429
        assert "Retry-After" in r.headers
    finally:
        auth_limiter.max_requests = orig_max
        auth_limiter.reset()


# ---------- quality-check fixes on the rate limiter ----------

def test_client_ip_uses_last_forwarded_entry():
    """First X-Forwarded-For entries are client-spoofable; the proxy appends
    the real address last."""
    from api.ratelimit import client_ip

    class FakeClient:
        host = "10.0.0.9"

    class FakeRequest:
        client = FakeClient()
        def __init__(self, xff):
            self.headers = {"x-forwarded-for": xff} if xff else {}

    assert client_ip(FakeRequest("1.2.3.4")) == "1.2.3.4"
    assert client_ip(FakeRequest("spoofed.evil, 5.6.7.8")) == "5.6.7.8"
    assert client_ip(FakeRequest(None)) == "10.0.0.9"


def test_rate_limiter_cleanup_drops_stale_keys():
    import time as _t
    from api.ratelimit import RateLimiter

    rl = RateLimiter(max_requests=5, window_s=0.01)
    for i in range(10001):
        rl._hits.setdefault(f"old{i}", __import__("collections").deque()).append(
            _t.monotonic())
    _t.sleep(0.02)          # everything now stale
    rl.check("fresh")       # triggers cleanup
    assert len(rl._hits) == 1 and "fresh" in rl._hits


# ---------- F4: request-body size limit ----------

def _run_middleware(mw, scope):
    import asyncio
    sent = []
    async def send(m): sent.append(m)
    async def receive(): return {"type": "http.request", "body": b""}
    asyncio.new_event_loop().run_until_complete(mw(scope, receive, send))
    return sent


def test_body_limit_rejects_oversize_and_chunked():
    from api.bodylimit import BodySizeLimitMiddleware

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = BodySizeLimitMiddleware(inner, max_bytes=100)
    base = {"type": "http", "path": "/x", "query_string": b""}

    over = _run_middleware(mw, {**base, "method": "POST",
                                "headers": [(b"content-length", b"101")]})
    assert over[0]["status"] == 413

    chunked = _run_middleware(mw, {**base, "method": "PUT",
                                   "headers": [(b"transfer-encoding", b"chunked")]})
    assert chunked[0]["status"] == 411

    ok = _run_middleware(mw, {**base, "method": "POST",
                              "headers": [(b"content-length", b"100")]})
    assert ok[0]["status"] == 200

    get = _run_middleware(mw, {**base, "method": "GET", "headers": []})
    assert get[0]["status"] == 200


def test_body_limit_wired_and_normal_requests_pass(client):
    from api import config
    assert config.MAX_BODY_MB == 75
    assert client.get("/health").json()["ok"] is True


# ---------- F5: migrations are idempotent under the advisory lock ----------

def test_migrations_rerun_is_noop(client):
    from api.db import run_migrations
    assert run_migrations() == []


# ---------- F6: invitation revocation ----------

def test_invitation_revocation(client):
    r = client.post("/auth/signup", json={
        "company_name": "RevokeCo", "name": "Ray",
        "email": "ray@revokeco.com", "password": "revokepass1"})
    cid, tok = r.json()["company_id"], r.json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    roles = client.get(f"/companies/{cid}/roles", headers=h).json()
    viewer = next(x["id"] for x in roles if x["name"] == "Viewer")

    inv = client.post(f"/companies/{cid}/invitations", headers=h,
                      json={"email": "gone@revokeco.com",
                            "role_ids": [viewer]}).json()

    r = client.delete(f"/companies/{cid}/invitations/{inv['invitation_id']}",
                      headers=h)
    assert r.status_code == 200

    # the token is now dead
    r = client.post("/auth/invitations/accept",
                    json={"token": inv["token"], "name": "x",
                          "password": "password123"})
    assert r.status_code == 400
    # double-revoke -> 404; garbage id -> 404 (not 500)
    assert client.delete(f"/companies/{cid}/invitations/{inv['invitation_id']}",
                         headers=h).status_code == 404
    assert client.delete(f"/companies/{cid}/invitations/not-a-uuid",
                         headers=h).status_code == 404


# ---------- F8: pagination + audit endpoint ----------

def test_pagination_and_audit(client):
    r = client.post("/auth/signup", json={
        "company_name": "PageCo", "name": "Pat",
        "email": "pat@pageco.com", "password": "pagepass123"})
    cid, tok = r.json()["company_id"], r.json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}

    vehicles = [{"id": f"v{i}", "vehicleId": f"V-{i:02d}", "type": "auto"}
                for i in range(3)]
    r = client.post(f"/companies/{cid}/import/backup", headers=h,
                    json={"data": {"inspectit.vehicles": vehicles}})
    assert r.status_code == 200

    r = client.get(f"/companies/{cid}/vehicles?limit=2", headers=h)
    assert len(r.json()) == 2
    assert r.headers["x-total-count"] == "3"
    r2 = client.get(f"/companies/{cid}/vehicles?limit=2&offset=2", headers=h)
    assert [v["vehicle_id"] for v in r2.json()] == ["V-02"]
    assert client.get(f"/companies/{cid}/vehicles?limit=9999",
                      headers=h).status_code == 422  # over MAX_PAGE

    # audit endpoint: exists, paginated, filterable, admin-gated
    r = client.get(f"/companies/{cid}/audit?limit=5", headers=h)
    assert r.status_code == 200
    assert int(r.headers["x-total-count"]) >= 2   # signup + import entries
    actions = {e["action"] for e in r.json()}
    assert "create" in actions
    STATE["pageco"] = (cid, tok)


# ---------- F9: expired-row cleanup ----------

def test_cleanup_expired_rows(client):
    import uuid as _uuid
    from api.db import cleanup_expired, get_pool

    cid, tok = STATE["pageco"]
    with get_pool().connection() as conn:
        uid = conn.execute("SELECT id FROM users WHERE email = %s",
                           ("pat@pageco.com",)).fetchone()["id"]
        conn.execute(
            """INSERT INTO refresh_tokens (jti, user_id, expires_at)
               VALUES (%s, %s, now() - interval '1 day')""",
            (_uuid.uuid4(), uid))
        conn.execute(
            """INSERT INTO password_resets (token_hash, user_id, expires_at)
               VALUES ('deadhash', %s, now() - interval '1 hour')""", (uid,))
        conn.execute(
            """INSERT INTO invitations (company_id, email, role_ids, token,
                                        invited_by, expires_at)
               VALUES (%s, 'late@x.com', '{}', 'expiredtok', %s,
                       now() - interval '1 day')""", (cid, uid))
        stats = cleanup_expired(conn)
        assert stats["refresh_tokens"] >= 1
        assert stats["password_resets"] >= 1
        assert stats["invitations_expired"] >= 1
        status = conn.execute(
            "SELECT status FROM invitations WHERE token = 'expiredtok'"
        ).fetchone()["status"]
        assert status == "expired"


def test_login_sweeps_users_dead_tokens(client):
    import uuid as _uuid
    from api.db import get_pool

    with get_pool().connection() as conn:
        uid = conn.execute("SELECT id FROM users WHERE email = %s",
                           ("pat@pageco.com",)).fetchone()["id"]
        conn.execute(
            """INSERT INTO refresh_tokens (jti, user_id, expires_at)
               VALUES (%s, %s, now() - interval '1 day')""",
            (_uuid.uuid4(), uid))
    r = client.post("/auth/login", json={
        "email": "pat@pageco.com", "password": "pagepass123"})
    assert r.status_code == 200
    with get_pool().connection() as conn:
        n = conn.execute(
            """SELECT count(*) AS n FROM refresh_tokens
               WHERE user_id = %s AND expires_at < now()""",
            (uid,)).fetchone()["n"]
    assert n == 0


# ---------- F10: import idempotency guard ----------

def test_reimport_blocked_without_force(client):
    cid, tok = STATE["pageco"]
    h = {"Authorization": f"Bearer {tok}"}
    payload = {"data": {"inspectit.vehicles":
                        [{"id": "vx", "vehicleId": "DUP-1", "type": "auto"}]}}

    r = client.post(f"/companies/{cid}/import/backup", headers=h, json=payload)
    assert r.status_code == 409
    assert "force" in r.json()["detail"]

    r = client.post(f"/companies/{cid}/import/backup?force=true",
                    headers=h, json=payload)
    assert r.status_code == 200
    assert r.json()["imported"]["vehicles"] == 1


# ---------- F11: unknown MIME types labeled 'bin', not 'pdf' ----------

def test_unknown_mime_kind_is_bin(client):
    from api.db import get_pool
    from api.storage import store_file

    cid, _ = STATE["pageco"]
    with get_pool().connection() as conn:
        fid = store_file(conn, cid, None, "archive.zip",
                         "application/zip", b"PKfake")
        kind = conn.execute("SELECT kind FROM files WHERE id = %s",
                            (fid,)).fetchone()["kind"]
    assert kind == "bin"


# ---------- F12: audit rows carry ip + user agent ----------

def test_audit_rows_have_request_meta(client):
    cid, tok = STATE["pageco"]
    h = {"Authorization": f"Bearer {tok}"}
    r = client.get(f"/companies/{cid}/audit?limit=1", headers=h)
    assert r.status_code == 200
    entry = r.json()[0]   # newest entry (the force-import from F10 test)
    assert entry["ip"]                       # e.g. "testclient"
    assert entry["user_agent"]               # TestClient sends "testclient"


# ---------- F13: structured access log ----------

def test_access_log_emits_json_lines(client, caplog):
    import json as _json
    import logging as _logging

    with caplog.at_level(_logging.INFO, logger="inspectit.access"):
        client.get("/me")          # unauthenticated -> 401, still logged
        client.get("/health")      # must NOT be logged (probe noise)
    lines = [_json.loads(rec.message) for rec in caplog.records
             if rec.name == "inspectit.access"]
    assert any(l["path"] == "/me" and l["status"] == 401 and "ms" in l
               for l in lines)
    assert not any(l["path"] == "/health" for l in lines)


# ---------- final QC pass: garbage UUID filters must not 500 ----------

def test_uuid_filters_reject_garbage_cleanly(client):
    cid, tok = STATE["pageco"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get(f"/companies/{cid}/audit?subject_id=garbage",
                      headers=h).status_code == 422
    assert client.get(f"/companies/{cid}/assignments?user_id=garbage",
                      headers=h).status_code == 422
    # valid uuid filters still work
    import uuid as _uuid
    assert client.get(f"/companies/{cid}/audit?subject_id={_uuid.uuid4()}",
                      headers=h).status_code == 200
