"""Email notifications: disabled by default, SMTP calls mocked, no header injection."""
import uuid

import pytest


class FakeSMTP:
    sent = []
    logins = []
    tls = False

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port
        self.__class__.sent = self.__class__.sent  # shared list

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def starttls(self, context=None): FakeSMTP.tls = True
    def login(self, user, pw): FakeSMTP.logins.append((user, pw))
    def send_message(self, msg): FakeSMTP.sent.append(msg)


@pytest.fixture()
def smtp_on(monkeypatch):
    from api import config, mailer
    FakeSMTP.sent, FakeSMTP.logins, FakeSMTP.tls = [], [], False
    monkeypatch.setattr(config, "SMTP_PASSWORD", "pw-not-real")
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    from api.routers.public import demo_limiter
    demo_limiter.reset()
    yield FakeSMTP
    demo_limiter.reset()


def test_disabled_without_password(client, monkeypatch):
    from api import config, mailer
    monkeypatch.setattr(config, "SMTP_PASSWORD", "")
    assert mailer.enabled() is False
    assert mailer.send_mail("s", "b") is False


def test_demo_request_emails_info_address(client, smtp_on):
    t = uuid.uuid4().hex[:6]
    r = client.post("/public/demo-requests", json={
        "name": "Dana Q", "email": f"dana-{t}@example.com", "count": 9})
    assert r.status_code == 201
    assert len(smtp_on.sent) == 1
    m = smtp_on.sent[0]
    assert m["To"] == "info@inspectit.app"
    assert "info@inspectit.app" in m["From"]
    assert m["Reply-To"] == f"dana-{t}@example.com"
    assert "Dana Q" in m["Subject"]
    assert "9" in m.get_body(("plain",)).get_content()
    assert smtp_on.logins == [("info@inspectit.app", "pw-not-real")]


def test_honeypot_sends_no_email(client, smtp_on):
    client.post("/public/demo-requests", json={
        "name": "Bot", "email": "bot@example.com", "website": "x"})
    assert smtp_on.sent == []


def test_trial_signup_emails_owner_without_password(client, smtp_on):
    t = uuid.uuid4().hex[:6]
    r = client.post("/auth/trial-signup", json={
        "name": "Trial Tom", "email": f"tom-{t}@example.com",
        "password": "super-secret-pw", "who": "org", "org": "Tom Fleet",
        "track": "vehicles"})
    assert r.status_code == 200
    assert len(smtp_on.sent) == 1
    body = smtp_on.sent[0].get_body(("plain",)).get_content()
    assert "Trial Tom" in body and "Tom Fleet" in body and "vehicles" in body
    assert "super-secret-pw" not in body and "super-secret-pw" not in str(smtp_on.sent[0])


def test_duplicate_trial_sends_nothing(client, smtp_on):
    t = uuid.uuid4().hex[:6]
    body = {"name": "Dup", "email": f"dup-{t}@example.com", "password": "password123"}
    client.post("/auth/trial-signup", json=body)
    smtp_on.sent.clear()
    assert client.post("/auth/trial-signup", json=body).status_code == 409
    assert smtp_on.sent == []


def test_header_injection_is_neutralised(client, smtp_on):
    from api import mailer
    msg = mailer.build_message("Hi\r\nBcc: evil@example.com", "x",
                               reply_to="a@b.co\r\nBcc: evil@example.com")
    assert "\n" not in msg["Subject"] and "\r" not in msg["Subject"]
    assert msg["Bcc"] is None
    assert "Bcc" not in msg.keys()


def test_smtp_failure_never_breaks_the_request(client, smtp_on, monkeypatch):
    from api import mailer

    class Boom(FakeSMTP):
        def login(self, u, p): raise OSError("blocked")
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", Boom)
    t = uuid.uuid4().hex[:6]
    r = client.post("/public/demo-requests", json={"name": "Z", "email": f"z-{t}@example.com"})
    assert r.status_code == 201


def test_port_587_uses_starttls(client, smtp_on, monkeypatch):
    from api import config, mailer
    monkeypatch.setattr(config, "SMTP_PORT", 587)
    assert mailer.send_mail("s", "b") is True
    assert smtp_on.tls is True


# ---------- Resend (HTTPS API) ----------

class _Resp:
    def __init__(self, status=200): self.status = status
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_resend_used_when_key_set(client, monkeypatch):
    import json
    from api import config, mailer
    calls = []
    monkeypatch.setattr(config, "SMTP_PASSWORD", "")
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_not_real")
    monkeypatch.setattr(mailer.urllib.request, "urlopen",
                        lambda req, timeout=None: (calls.append(req), _Resp(200))[1])
    assert mailer.enabled() is True
    assert mailer.send_mail("Hi\r\nBcc: x@y.z", "body", reply_to="a@b.co") is True
    req = calls[0]
    assert req.full_url == "https://api.resend.com/emails"
    assert req.get_header("Authorization") == "Bearer re_not_real"
    assert req.get_header("User-agent") == "inspectit-api/1.0"
    payload = json.loads(req.data)
    assert payload["to"] == ["info@inspectit.app"] and payload["reply_to"] == "a@b.co"
    assert "\n" not in payload["subject"]


def test_resend_failure_is_swallowed(client, monkeypatch):
    from api import config, mailer
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_not_real")

    def boom(req, timeout=None):
        raise OSError("network down")
    monkeypatch.setattr(mailer.urllib.request, "urlopen", boom)
    assert mailer.send_mail("s", "b") is False


def test_html_version_is_branded_and_matches_text():
    from api import config, mailer
    subject, body = mailer.password_reset_email(
        "<Bob & Co>", "https://inspectit.app/reset-password?token=abc&x=1", 60)
    assert isinstance(body, str) and body.button == "Choose a new password"
    out = mailer.render_html(subject, body, body.button)
    assert f'{config.APP_BASE_URL}/assets/email-logo.jpg' in out and 'alt="Inspectit.app"' in out
    assert "#1e64d3" in out and ">Choose a new password</a>" in out
    assert 'href="https://inspectit.app/reset-password?token=abc&amp;x=1"' in out
    assert "&lt;Bob &amp; Co&gt;" in out and "<Bob" not in out            # escaped
    assert "expires in 60 minutes" in out                                  # same words as text
    assert out.count("Inspectit.app</strong>") == 1                         # signature -> footer


def test_inline_links_and_no_button_emails():
    from api import mailer
    subject, body = mailer.password_changed_email("Ann", "https://inspectit.app/web/inspectit-app.html")
    out = mailer.render_html(subject, body, body.button)
    assert '<a href="https://inspectit.app/web/inspectit-app.html"' in out and "padding:14px 28px" not in out
    subject, body = mailer.demo_request_email("Zed", "z@example.com", 3)
    assert body.button is None and "Zed" in mailer.render_html(subject, body)


def test_resend_and_smtp_send_html(client, smtp_on, monkeypatch):
    import json
    from api import config, mailer
    subject, body = mailer.welcome_email("Ann", "Acme", "Viewer", "https://x.test/r?t=1", 60)
    assert mailer.send_mail(subject, body, None, "ann@example.com") is True
    msg = smtp_on.sent[-1]
    assert msg.get_body(("plain",)).get_content().startswith("Hi Ann")
    assert ">Set my password</a>" in msg.get_body(("html",)).get_content()
    calls = []
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_not_real")
    monkeypatch.setattr(mailer.urllib.request, "urlopen",
                        lambda req, timeout=None: (calls.append(req), _Resp(200))[1])
    assert mailer.send_mail(subject, body, None, "ann@example.com") is True
    payload = json.loads(calls[0].data)
    assert payload["text"] == str(body) and ">Set my password</a>" in payload["html"]
