#!/usr/bin/env python3
"""Turn an EXISTING company into a demonstration model: one dummy member per
role, invited and accepted through the real HTTP API, optionally renaming the
company first.

Unlike seed_roles.py (which creates a throwaway company on the dev server),
this signs in as the company's administrator on any server — including the
live one — so it can populate the account you actually demo from.

    .venv/bin/python seed_demo_members.py --api https://inspectit.app \
        --admin brandon.m.cooper@att.net --rename "Cooper Test" \
        --domain coopertest.example

You are prompted for the administrator's password and for the password to
give every dummy member (or pass --password). Nothing is echoed or stored.

Dummy addresses are <role>@<domain>, e.g. vehicle-manager@coopertest.example.
No mail is sent (there is no mailer), so the domain never needs to exist;
`.example` is reserved for exactly this. Roles the company already has a
member for are skipped, so re-running is safe. /auth/* is rate-limited per
IP (10/min); the script sleeps on 429 and carries on.
"""
import argparse
import getpass
import json
import sys
import time
import urllib.error
import urllib.request

ADMIN_ROLE = "Company Administrator"

try:
    sys.path.insert(0, __file__.rsplit("/", 1)[0] or ".")
    from api.presets import ALL_ROLES as ROLE_ORDER
except Exception:            # pragma: no cover - script convenience only
    ROLE_ORDER = None


class ApiError(Exception):
    def __init__(self, code, detail):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


def call(api, method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    while True:
        req = urllib.request.Request(api + path, data=data, method=method,
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("Retry-After", "10") or 10)
                print(f"  rate-limited on {path}; waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise ApiError(e.code, e.read().decode(errors="replace"))
        except urllib.error.URLError as e:
            sys.exit(f"Cannot reach {api}: {e.reason}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--api", default="https://inspectit.app")
    ap.add_argument("--admin", required=True, help="administrator's sign-in email")
    ap.add_argument("--company", help="company id; defaults to the admin's first membership")
    ap.add_argument("--rename", metavar="NAME", help="rename the company first")
    ap.add_argument("--domain", default="coopertest.example",
                    help="domain for the dummy addresses")
    ap.add_argument("--password", help="password for every dummy member (prompted if omitted)")
    ap.add_argument("--json", action="store_true", help="print JSON, not a table")
    a = ap.parse_args()
    api = a.api.rstrip("/")

    admin_pw = getpass.getpass(f"Password for {a.admin} on {api}: ")
    member_pw = a.password or getpass.getpass("Password to give every dummy member: ")
    if len(member_pw) < 8:
        sys.exit("The member password must be at least 8 characters.")

    # 1. Sign in as the administrator and pick the company.
    try:
        tok = call(api, "POST", "/auth/login", {"email": a.admin, "password": admin_pw})
    except ApiError as e:
        sys.exit(f"Sign-in failed ({e.detail})")
    admin_tok = tok["access_token"]
    me = call(api, "GET", "/me", token=admin_tok)
    ms = me.get("memberships") or []
    if not ms:
        sys.exit("That account isn't a member of any company.")
    m = next((x for x in ms if x["company_id"] == a.company), None) if a.company else ms[0]
    if not m:
        sys.exit(f"{a.admin} isn't a member of company {a.company}")
    cid, cname = m["company_id"], m["company_name"]
    if ADMIN_ROLE not in [r["name"] for r in m.get("roles", [])]:
        sys.exit(f"{a.admin} isn't a {ADMIN_ROLE} of {cname}; only that role may issue every preset.")
    print(f"company {cname} [{cid}]", file=sys.stderr)

    # 2. Optional rename.
    if a.rename and a.rename.strip() != cname:
        r = call(api, "PATCH", f"/companies/{cid}", {"name": a.rename}, token=admin_tok)
        cname = r["name"]
        print(f"  renamed to {cname}", file=sys.stderr)

    # 3. Presets and who is already here.
    roles = call(api, "GET", f"/companies/{cid}/roles", token=admin_tok)
    by_name = {r["name"]: r["id"] for r in roles if r.get("is_preset")}
    order = [r for r in (ROLE_ORDER or list(by_name)) if r in by_name]
    order += sorted(set(by_name) - set(order))
    existing = call(api, "GET", f"/companies/{cid}/members?limit=500", token=admin_tok)
    if isinstance(existing, dict) and "items" in existing:
        existing = existing["items"]
    held = set()
    for mem in existing:
        for rn in mem.get("role_names") or [r.get("name") for r in mem.get("roles") or []]:
            if rn:
                held.add(rn)

    def addr(role):
        return role.lower().replace(" ", "-") + "@" + a.domain

    # 4. One invite + accept per role that has nobody yet.
    members = [{"role": ADMIN_ROLE, "email": a.admin, "status": "existing"}]
    for role in order:
        if role == ADMIN_ROLE:
            continue
        if role in held:
            members.append({"role": role, "email": "(already has a member)", "status": "skipped"})
            print(f"  {role:<22} already has a member — skipped", file=sys.stderr)
            continue
        email = addr(role)
        try:
            inv = call(api, "POST", f"/companies/{cid}/invitations",
                       {"email": email, "role_ids": [by_name[role]]}, token=admin_tok)
            call(api, "POST", "/auth/invitations/accept",
                 {"token": inv["token"], "name": role, "password": member_pw})
        except ApiError as e:
            members.append({"role": role, "email": email, "status": f"failed {e.code}"})
            print(f"  {role:<22} {email}  FAILED {e}", file=sys.stderr)
            continue
        members.append({"role": role, "email": email, "status": "created"})
        print(f"  {role:<22} {email}", file=sys.stderr)

    out = {"api": api, "company_id": cid, "company_name": cname, "members": members}
    if a.json:
        print(json.dumps(out, indent=2))
        return
    print()
    print(f"Company: {cname}   id {cid}")
    print(f"App: {api}/web/inspectit-app.html")
    print("Password for every dummy member: the one you entered")
    print()
    w = max(len(x["role"]) for x in members)
    print(f"{'Role':<{w}}  {'Email':<44}  Status")
    for x in members:
        print(f"{x['role']:<{w}}  {x['email']:<44}  {x['status']}")


if __name__ == "__main__":
    main()
