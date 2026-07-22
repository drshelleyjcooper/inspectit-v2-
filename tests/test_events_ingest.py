"""Stage 1 of the Analytics Dashboard work: the events table + batched
POST /companies/{cid}/events ingest. No dashboard endpoints yet — those
need require_platform_admin, which doesn't exist in this codebase yet
(see docs/analytics-api.md once Stage 2 lands).
"""
import datetime as dt

from api.db import get_pool

STATE = {}


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def test_setup(client):
    r = client.post("/auth/signup", json={
        "company_name": "Events Test Co", "name": "Owner",
        "email": "events-owner@example.com", "password": "ownerpass123"})
    assert r.status_code == 200, r.text
    body = r.json()
    STATE["cid"] = body["company_id"]
    STATE["uid"] = body["user_id"]
    STATE["access"] = body["access_token"]


def test_valid_batch_is_accepted_and_persisted(client):
    h = _auth(STATE["access"])
    r = client.post(f"/companies/{STATE['cid']}/events", headers=h, json={
        "events": [
            {"event_name": "session_start", "event_props": {},
             "session_id": "s1", "occurred_at": _now_iso()},
            {"event_name": "inspection_saved",
             "event_props": {"kind": "vehicle"},
             "session_id": "s1", "occurred_at": _now_iso()},
        ]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accepted"] == 2
    assert body["rejected"] == []

    with get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT event_name, event_props, user_id, company_id FROM events "
            "WHERE company_id = %s ORDER BY id", (STATE["cid"],)).fetchall()
    assert [r["event_name"] for r in rows] == ["session_start", "inspection_saved"]
    assert rows[1]["event_props"] == {"kind": "vehicle"}
    # Tenant/actor came from the token, never the body — body carried no
    # company_id/user_id fields at all, yet both are correctly stamped.
    assert str(rows[0]["user_id"]) == STATE["uid"]
    assert str(rows[0]["company_id"]) == STATE["cid"]


def test_unknown_event_name_is_rejected_not_the_whole_batch(client):
    h = _auth(STATE["access"])
    r = client.post(f"/companies/{STATE['cid']}/events", headers=h, json={
        "events": [
            {"event_name": "session_start", "event_props": {},
             "session_id": "s2", "occurred_at": _now_iso()},
            {"event_name": "made_up_event", "event_props": {},
             "session_id": "s2", "occurred_at": _now_iso()},
        ]})
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == 1
    assert body["rejected"] == [{"index": 1, "reason": "unknown event_name"}]


def test_oversized_props_rejected():
    from api.analytics import validate_props

    assert validate_props({"a": "x" * 300}) != ""
    assert validate_props({f"k{i}": i for i in range(20)}) != ""
    assert validate_props({"nested": {"a": 1}}) != ""
    assert validate_props({"kind": "vehicle"}) == ""


def test_oversized_props_rejected_via_endpoint(client):
    h = _auth(STATE["access"])
    r = client.post(f"/companies/{STATE['cid']}/events", headers=h, json={
        "events": [
            {"event_name": "upload_added", "event_props": {"kind": "x" * 500},
             "session_id": "s3", "occurred_at": _now_iso()},
        ]})
    assert r.status_code == 200
    assert r.json()["accepted"] == 0
    assert len(r.json()["rejected"]) == 1


def test_requires_auth(client):
    r = client.post(f"/companies/{STATE['cid']}/events", json={
        "events": [{"event_name": "session_start", "event_props": {},
                   "session_id": "s4", "occurred_at": _now_iso()}]})
    assert r.status_code == 401


def test_outsider_cannot_post_to_another_companys_events(client):
    r = client.post("/auth/signup", json={
        "company_name": "Other Events Co", "name": "Outsider",
        "email": "events-outsider@example.com", "password": "outsiderpass1"})
    outsider_access = r.json()["access_token"]
    r = client.post(f"/companies/{STATE['cid']}/events",
                    headers=_auth(outsider_access), json={
        "events": [{"event_name": "session_start", "event_props": {},
                   "session_id": "s5", "occurred_at": _now_iso()}]})
    assert r.status_code == 403


def test_empty_batch_rejected_by_schema(client):
    h = _auth(STATE["access"])
    r = client.post(f"/companies/{STATE['cid']}/events", headers=h,
                    json={"events": []})
    assert r.status_code == 422


def test_batch_over_max_size_rejected_by_schema(client):
    h = _auth(STATE["access"])
    events = [{"event_name": "session_start", "event_props": {},
              "session_id": "s6", "occurred_at": _now_iso()}] * 51
    r = client.post(f"/companies/{STATE['cid']}/events", headers=h,
                    json={"events": events})
    assert r.status_code == 422


def test_events_endpoint_is_rate_limited(client):
    from api.ratelimit import events_limiter

    orig_max = events_limiter.max_requests
    events_limiter.reset()
    events_limiter.max_requests = 2
    h = _auth(STATE["access"])
    body = {"events": [{"event_name": "session_start", "event_props": {},
                        "session_id": "s7", "occurred_at": _now_iso()}]}
    try:
        for _ in range(2):
            r = client.post(f"/companies/{STATE['cid']}/events", headers=h, json=body)
            assert r.status_code == 200
        r = client.post(f"/companies/{STATE['cid']}/events", headers=h, json=body)
        assert r.status_code == 429
        assert "Retry-After" in r.headers
    finally:
        events_limiter.max_requests = orig_max
        events_limiter.reset()
