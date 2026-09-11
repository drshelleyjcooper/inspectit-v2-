#!/usr/bin/env python3
"""Seed a throwaway company with one member per role, for the §9.4 manual pass.

Drives the real HTTP API — signup, invite, accept — so the grant and §2.3
paths run exactly as they would for a person. Needs the API up with
DEV_MODE=1 (the default for run_dev.py), because invite tokens come back in
the response instead of an email.

    .venv/bin/python seed_roles.py                      # against 127.0.0.1:8100
    .venv/bin/python seed_roles.py --viewer-grants      # also flip can_grant_viewers
                                                        # on the three domain managers
    .venv/bin/python seed_roles.py --json > seed.json   # machine-readable

Every run makes a NEW company (name and emails carry a run tag), so it is
safe to repeat. /auth/* is rate-limited per IP (10/min by default) and this
makes thirteen auth calls; on a 429 the script sleeps for Retry-After and
carries on, so expect one pause of up to a minute.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

PASSWORD = "seedpass123"
ADMIN_ROLE = "Company Administrator"

# Display order. Falls back to the server's order if presets.py isn't importable.
try:
    sys.path.insert(0, __file__.rsplit("/", 1)[0] or ".")
    from api.presets import ALL_ROLES as ROLE_ORDER
except Exception:            # pragma: no cover - script convenience only
    ROLE_ORDER = None


def call(api, method, path, body=None, token=None):
    """One request. Retries on 429 (sleeping Retry-After); raises on 4xx/5xx."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    while True:
        req = urllib.request.Request(api + path, data=data, method=method,
                                     headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("Retry-After", "5"))
                print(f"  rate-limited on {path}; waiting {wait}s",
                      file=sys.stderr)
                time.sleep(wait)
                continue
            detail = e.read().decode(errors="replace")
            sys.exit(f"{method} {path} -> {e.code}: {detail}")
        except urllib.error.URLError as e:
            sys.exit(f"Cannot reach {api}: {e.reason}. Is the API running?")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--api", default="http://127.0.0.1:8100")
    ap.add_argument("--password", default=PASSWORD)
    ap.add_argument("--tag", default=time.strftime("%m%d%H%M"),
                    help="run tag baked into the company name and emails")
    ap.add_argument("--viewer-grants", action="store_true",
                    help="set can_grant_viewers on Vehicle/Property/Project Manager")
    ap.add_argument("--json", action="store_true", help="print JSON, not a table")
    a = ap.parse_args()

    def addr(role):
        return role.lower().replace(" ", "-") + f".{a.tag}@seed.test"

    # 1. The company and its administrator.
    admin_email = addr(ADMIN_ROLE)
    su = call(a.api, "POST", "/auth/signup", {
        "company_name": f"Seed Co {a.tag}", "name": "Seed Admin",
        "email": admin_email, "password": a.password})
    cid, admin_tok = su["company_id"], su["access_token"]
    print(f"company {cid}  ({admin_email})", file=sys.stderr)

    # 2. Presets, in spec order.
    roles = call(a.api, "GET", f"/companies/{cid}/roles", token=admin_tok)
    by_name = {r["name"]: r["id"] for r in roles if r["is_preset"]}
    order = [r for r in (ROLE_ORDER or list(by_name)) if r in by_name]
    missing = set(by_name) - set(order)
    if missing:
        order += sorted(missing)

    # 3. One invite + accept per remaining role, all issued by the admin.
    members = [{"role": ADMIN_ROLE, "email": admin_email, "token": admin_tok}]
    for role in order:
        if role == ADMIN_ROLE:
            continue
        email = addr(role)
        inv = call(a.api, "POST", f"/companies/{cid}/invitations",
                   {"email": email, "role_ids": [by_name[role]]}, token=admin_tok)
        acc = call(a.api, "POST", "/auth/invitations/accept", {
            "token": inv["token"], "name": role, "password": a.password})
        members.append({"role": role, "email": email,
                        "token": acc["access_token"]})
        print(f"  {role:<22} {email}", file=sys.stderr)

    # 4. Membership ids (needed for the viewer-grants flag and handy for /docs).
    listed = call(a.api, "GET", f"/companies/{cid}/members?limit=100",
                  token=admin_tok)
    mid = {m["email"]: m["membership_id"] for m in listed}
    for m in members:
        m["membership_id"] = mid.get(m["email"])

    if a.viewer_grants:
        for role in ("Vehicle Manager", "Property Manager", "Project Manager"):
            m = next(x for x in members if x["role"] == role)
            call(a.api, "PATCH", f"/companies/{cid}/members/{m['membership_id']}",
                 {"can_grant_viewers": True}, token=admin_tok)
            m["can_grant_viewers"] = True
        print("  can_grant_viewers set on the three domain managers",
              file=sys.stderr)

    out = {"api": a.api, "company_id": cid, "password": a.password,
           "members": members}
    if a.json:
        print(json.dumps(out, indent=2))
        return

    print()
    print(f"Company: Seed Co {a.tag}   id {cid}")
    print(f"Password for every account: {a.password}")
    print(f"App: {a.api}/web/inspectit-app.html   Swagger: {a.api}/docs")
    print()
    w = max(len(m["role"]) for m in members)
    print(f"{'Role':<{w}}  {'Email':<44}  Membership id")
    for m in members:
        flag = "  (can_grant_viewers)" if m.get("can_grant_viewers") else ""
        print(f"{m['role']:<{w}}  {m['email']:<44}  {m['membership_id']}{flag}")
    print()
    print("Bearer tokens for Swagger's Authorize box (valid until they expire;")
    print("log in again with the email/password to get a fresh one):")
    for m in members:
        print(f"  {m['role']:<{w}}  {m['token']}")


if __name__ == "__main__":
    main()
