"""F1–F3 hardening tests. Named zz_ so the rate-limit integration test runs
after the rest of the suite (it tightens the shared limiter)."""
import time

import pytest


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
