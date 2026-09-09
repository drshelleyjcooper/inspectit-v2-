"""Delegated granting: who may issue which role, and the combination block.

USER-ROLES-SPEC v2.0 §9.2. None of this was covered before v2.0 because
delegated granting didn't exist -- Company Administrator was the only role that
could invite anyone.

Uses the session-scoped `client` fixture from conftest.py. That client shares
one database across the whole run, so every address here is generated rather
than written out -- a literal would collide the second time a test used it,
and `users.email` is unique across companies even though membership isn't.
Each test builds its own company via /auth/signup for the same reason.

Every grant test hits POST /invitations directly rather than driving the UI. A
greyed-out dropdown option is not a control -- the point is that the server
refuses regardless of what the browser sent.
"""
import uuid

import pytest

PW = "test-password-123"


# --- helpers ----------------------------------------------------------------

def _signup(client, company="Acme Fleet"):
    """A fresh company whose creator is Company Administrator."""
    email = f"admin-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/auth/signup", json={
        "company_name": company, "name": "Admin", "email": email,
        "password": PW})
    assert r.status_code == 200, r.text
    d = r.json()
    return d["company_id"], d["access_token"], email


def _addr(tag):
    """The suite shares one database across the file, so no literal emails."""
    return f"{tag}-{uuid.uuid4().hex[:8]}@example.com"


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _roles(client, company_id, token):
    r = client.get(f"/companies/{company_id}/roles", headers=_auth(token))
    assert r.status_code == 200, r.text
    return {row["name"]: row["id"] for row in r.json()}


def _invite(client, company_id, token, email, role_names, rmap):
    return client.post(
        f"/companies/{company_id}/invitations",
        headers=_auth(token),
        json={"email": email, "role_ids": [rmap[n] for n in role_names]})


def _accept(client, invite_token, name="Sam", password=PW):
    return client.post("/auth/invitations/accept", json={
        "token": invite_token, "name": name, "password": password})


def _add_member(client, company_id, admin_token, rmap, role_names, email=None):
    """Invite as the administrator and accept. Returns (email, access_token)."""
    email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
    r = _invite(client, company_id, admin_token, email, role_names, rmap)
    assert r.status_code == 200, r.text
    a = _accept(client, r.json()["token"])
    assert a.status_code == 200, a.text
    return email, a.json()["access_token"]


def _membership_id(client, company_id, token, email):
    r = client.get(f"/companies/{company_id}/members", headers=_auth(token))
    assert r.status_code == 200, r.text
    for m in r.json():
        if m["email"] == email:
            return m["membership_id"]
    raise AssertionError(f"{email} is not a member")


@pytest.fixture
def company(client):
    cid, token, email = _signup(client)
    return {"id": cid, "token": token, "email": email,
            "roles": _roles(client, cid, token)}


# --- §4.2 grant enforcement -------------------------------------------------

def test_vehicle_manager_may_invite_its_own_inspector(client, company):
    _, vm = _add_member(client, company["id"], company["token"],
                        company["roles"], ["Vehicle Manager"])
    r = _invite(client, company["id"], vm, _addr("insp"),
                ["Vehicle Inspector"], company["roles"])
    assert r.status_code == 200, r.text


def test_vehicle_manager_may_not_cross_the_domain(client, company):
    """The whole reason grants aren't derived from rank: the domain managers
    are siblings, so rank alone would allow this."""
    _, vm = _add_member(client, company["id"], company["token"],
                        company["roles"], ["Vehicle Manager"])
    r = _invite(client, company["id"], vm, _addr("x"),
                ["Property Inspector"], company["roles"])
    assert r.status_code == 403


def test_domain_manager_may_not_invite_a_peer(client, company):
    _, vm = _add_member(client, company["id"], company["token"],
                        company["roles"], ["Vehicle Manager"])
    r = _invite(client, company["id"], vm, _addr("x"),
                ["Vehicle Manager"], company["roles"])
    assert r.status_code == 403


def test_domain_manager_may_not_invite_the_company_wide_viewer(client, company):
    """Would grant read across every domain through the back door."""
    _, vm = _add_member(client, company["id"], company["token"],
                        company["roles"], ["Vehicle Manager"])
    r = _invite(client, company["id"], vm, _addr("x"),
                ["Viewer"], company["roles"])
    assert r.status_code == 403


@pytest.mark.parametrize("target", ["Company Administrator", "Manager", "Viewer"])
def test_manager_cannot_issue_admin_peer_or_company_viewer(client, company, target):
    """Viewer is in this list deliberately: §2.5 reserves the company-wide
    viewer to the administrator, and the first draft of §4.2 got it wrong."""
    _, mgr = _add_member(client, company["id"], company["token"],
                         company["roles"], ["Manager"])
    r = _invite(client, company["id"], mgr, _addr("x"), [target],
                company["roles"])
    assert r.status_code == 403


def test_manager_may_issue_the_domain_viewers(client, company):
    _, mgr = _add_member(client, company["id"], company["token"],
                         company["roles"], ["Manager"])
    r = _invite(client, company["id"], mgr, _addr("dv"),
                ["Vehicle Viewer"], company["roles"])
    assert r.status_code == 200, r.text


def test_project_manager_invites_nobody_by_default(client, company):
    """Its domain has no inspector or maintenance role beneath it."""
    _, pm = _add_member(client, company["id"], company["token"],
                        company["roles"], ["Project Manager"])
    for target in ("Project Viewer", "Vehicle Inspector", "Viewer"):
        r = _invite(client, company["id"], pm, _addr("x"), [target],
                    company["roles"])
        assert r.status_code == 403, target


@pytest.mark.parametrize("target", [
    "Company Administrator", "Manager", "Vehicle Manager", "Property Manager",
    "Project Manager", "Vehicle Inspector", "Property Inspector",
    "Vehicle Maintenance", "Property Maintenance", "Vehicle Viewer",
    "Property Viewer", "Project Viewer", "Viewer"])
def test_administrator_may_issue_every_role(client, company, target):
    r = _invite(client, company["id"], company["token"], _addr("t"), [target],
                company["roles"])
    assert r.status_code == 200, r.text


def test_inspector_cannot_invite_at_all(client, company):
    _, insp = _add_member(client, company["id"], company["token"],
                          company["roles"], ["Vehicle Inspector"])
    r = _invite(client, company["id"], insp, _addr("x"),
                ["Vehicle Inspector"], company["roles"])
    assert r.status_code == 403


# --- §2.5 the per-membership viewer flag ------------------------------------

def test_viewer_grant_flag_off_then_on(client, company):
    email, vm = _add_member(client, company["id"], company["token"],
                            company["roles"], ["Vehicle Manager"])
    target = _addr("vv")
    r = _invite(client, company["id"], vm, target,
                ["Vehicle Viewer"], company["roles"])
    assert r.status_code == 403, "default is restrictive"

    mid = _membership_id(client, company["id"], company["token"], email)
    p = client.patch(f"/companies/{company['id']}/members/{mid}",
                     headers=_auth(company["token"]),
                     json={"can_grant_viewers": True})
    assert p.status_code == 200, p.text

    r = _invite(client, company["id"], vm, target,
                ["Vehicle Viewer"], company["roles"])
    assert r.status_code == 200, r.text


def test_manager_cannot_widen_its_own_granting(client, company):
    """Only an administrator sets the flag -- otherwise it isn't a limit."""
    email, vm = _add_member(client, company["id"], company["token"],
                            company["roles"], ["Vehicle Manager"])
    mid = _membership_id(client, company["id"], company["token"], email)
    p = client.patch(f"/companies/{company['id']}/members/{mid}",
                     headers=_auth(vm), json={"can_grant_viewers": True})
    assert p.status_code == 403


def test_the_flag_is_per_person_not_per_role(client, company):
    e1, vm1 = _add_member(client, company["id"], company["token"],
                          company["roles"], ["Vehicle Manager"])
    _e2, vm2 = _add_member(client, company["id"], company["token"],
                           company["roles"], ["Vehicle Manager"])
    mid = _membership_id(client, company["id"], company["token"], e1)
    client.patch(f"/companies/{company['id']}/members/{mid}",
                 headers=_auth(company["token"]),
                 json={"can_grant_viewers": True})
    assert _invite(client, company["id"], vm1, _addr("a"),
                   ["Vehicle Viewer"], company["roles"]).status_code == 200
    assert _invite(client, company["id"], vm2, _addr("b"),
                   ["Vehicle Viewer"], company["roles"]).status_code == 403


# --- §2.3 the inspector + maintenance block ---------------------------------

def test_block_both_roles_in_one_invitation(client, company):
    _, vm = _add_member(client, company["id"], company["token"],
                        company["roles"], ["Vehicle Manager"])
    r = _invite(client, company["id"], vm, _addr("sam"),
                ["Vehicle Inspector", "Vehicle Maintenance"], company["roles"])
    assert r.status_code == 403


def test_block_on_accumulation_against_an_active_member(client, company):
    """Sam is already inspecting; a manager tries to add maintenance."""
    sam, _ = _add_member(client, company["id"], company["token"],
                         company["roles"], ["Vehicle Inspector"])
    _, vm = _add_member(client, company["id"], company["token"],
                        company["roles"], ["Vehicle Manager"])
    r = _invite(client, company["id"], vm, sam, ["Vehicle Maintenance"],
                company["roles"])
    assert r.status_code == 403


def test_block_counts_pending_invitations(client, company):
    """Two invitations sent before either is accepted would otherwise slip
    the combination through."""
    _, vm = _add_member(client, company["id"], company["token"],
                        company["roles"], ["Vehicle Manager"])
    sam = _addr("sam")
    assert _invite(client, company["id"], vm, sam,
                   ["Vehicle Inspector"], company["roles"]).status_code == 200
    r = _invite(client, company["id"], vm, sam,
                ["Vehicle Maintenance"], company["roles"])
    assert r.status_code == 403


def test_block_fires_at_acceptance_when_the_membership_changed(client, company):
    """The invitation was legitimate when issued. Between issue and accept an
    administrator gave Sam the other role, so acceptance is the moment the
    combination would actually land."""
    sam, _ = _add_member(client, company["id"], company["token"],
                         company["roles"], ["Viewer"])
    _, vm = _add_member(client, company["id"], company["token"],
                        company["roles"], ["Vehicle Manager"])
    inv = _invite(client, company["id"], vm, sam, ["Vehicle Maintenance"],
                  company["roles"])
    assert inv.status_code == 200, inv.text

    mid = _membership_id(client, company["id"], company["token"], sam)
    p = client.patch(
        f"/companies/{company['id']}/members/{mid}",
        headers=_auth(company["token"]),
        json={"role_ids": [company["roles"]["Vehicle Inspector"]]})
    assert p.status_code == 200, p.text

    a = _accept(client, inv.json()["token"], password=PW)
    assert a.status_code == 409, a.text


def test_cross_domain_pair_is_allowed(client, company):
    """Vehicle Inspector + Property Maintenance is an ordinary pair."""
    sam, _ = _add_member(client, company["id"], company["token"],
                         company["roles"], ["Vehicle Inspector"])
    _, pm = _add_member(client, company["id"], company["token"],
                        company["roles"], ["Property Manager"])
    r = _invite(client, company["id"], pm, sam, ["Property Maintenance"],
                company["roles"])
    assert r.status_code == 200, r.text


def test_manager_may_issue_the_combination(client, company):
    """Manager holds company:admin, so the block doesn't apply to it."""
    _, mgr = _add_member(client, company["id"], company["token"],
                         company["roles"], ["Manager"])
    r = _invite(client, company["id"], mgr, _addr("both"),
                ["Vehicle Inspector", "Vehicle Maintenance"], company["roles"])
    assert r.status_code == 200, r.text


def test_administrator_may_issue_the_combination(client, company):
    r = _invite(client, company["id"], company["token"], _addr("both2"),
                ["Property Inspector", "Property Maintenance"],
                company["roles"])
    assert r.status_code == 200, r.text


# --- §4.3 one email, many roles ---------------------------------------------

def test_second_invitation_adds_a_role_to_one_membership(client, company):
    sam, _ = _add_member(client, company["id"], company["token"],
                         company["roles"], ["Vehicle Inspector"])
    _, pm = _add_member(client, company["id"], company["token"],
                        company["roles"], ["Property Manager"])
    inv = _invite(client, company["id"], pm, sam, ["Property Inspector"],
                  company["roles"])
    assert inv.status_code == 200, inv.text
    assert _accept(client, inv.json()["token"]).status_code == 200

    members = client.get(f"/companies/{company['id']}/members",
                         headers=_auth(company["token"])).json()
    rows = [m for m in members if m["email"] == sam]
    assert len(rows) == 1, "one person, one membership"
    names = {r["name"] for r in rows[0]["roles"]}
    assert names == {"Vehicle Inspector", "Property Inspector"}


def test_reinviting_an_identical_role_is_refused(client, company):
    sam, _ = _add_member(client, company["id"], company["token"],
                         company["roles"], ["Vehicle Inspector"])
    r = _invite(client, company["id"], company["token"], sam,
                ["Vehicle Inspector"], company["roles"])
    assert r.status_code == 409


def test_a_new_invitation_supersedes_the_pending_one(client, company):
    """_held_role_names reads pending invitations, so duplicates would make the
    combination check drift."""
    cid, tok, roles = company["id"], company["token"], company["roles"]
    who = _addr("dup")
    first = _invite(client, cid, tok, who, ["Vehicle Viewer"], roles)
    assert first.status_code == 200
    second = _invite(client, cid, tok, who, ["Property Viewer"], roles)
    assert second.status_code == 200

    pending = [i for i in client.get(f"/companies/{cid}/invitations",
                                     headers=_auth(tok)).json()
               if i["email"] == who and i["status"] == "pending"]
    assert len(pending) == 1
    assert _accept(client, first.json()["token"]).status_code == 400


# --- §7.4 the routes the domain managers now reach --------------------------

def test_domain_manager_can_see_its_own_invitations(client, company):
    """It holds company:assign but not company:view -- under a naive v2.0 it
    would create an invitation and then 403 listing it."""
    _, vm = _add_member(client, company["id"], company["token"],
                        company["roles"], ["Vehicle Manager"])
    who = _addr("seen")
    _invite(client, company["id"], vm, who, ["Vehicle Inspector"],
            company["roles"])
    r = client.get(f"/companies/{company['id']}/invitations", headers=_auth(vm))
    assert r.status_code == 200, r.text
    row = next(i for i in r.json() if i["email"] == who)
    assert row["role_names"] == ["Vehicle Inspector"], "the UI showed 'No role'"


def test_domain_manager_cannot_rewrite_the_administrator(client, company):
    """company:assign alone used to be the whole check on this route."""
    _, vm = _add_member(client, company["id"], company["token"],
                        company["roles"], ["Vehicle Manager"])
    admin_mid = _membership_id(client, company["id"], company["token"],
                               company["email"])
    r = client.patch(f"/companies/{company['id']}/members/{admin_mid}",
                     headers=_auth(vm),
                     json={"role_ids": [company["roles"]["Vehicle Inspector"]]})
    assert r.status_code == 403


def test_domain_manager_cannot_remove_the_administrator(client, company):
    _, vm = _add_member(client, company["id"], company["token"],
                        company["roles"], ["Vehicle Manager"])
    admin_mid = _membership_id(client, company["id"], company["token"],
                               company["email"])
    r = client.delete(f"/companies/{company['id']}/members/{admin_mid}",
                      headers=_auth(vm))
    assert r.status_code == 403


def test_a_domain_manager_does_not_satisfy_the_last_admin_guard(client, company):
    """_other_admins counted company:assign, which five roles now hold. If it
    still did, this demotion would strand the company with nobody able to
    restore an administrator."""
    _add_member(client, company["id"], company["token"], company["roles"],
                ["Vehicle Manager"])
    admin_mid = _membership_id(client, company["id"], company["token"],
                               company["email"])
    r = client.patch(f"/companies/{company['id']}/members/{admin_mid}",
                     headers=_auth(company["token"]),
                     json={"role_ids": [company["roles"]["Viewer"]]})
    assert r.status_code == 409


# --- regression, not part of v2.0 -------------------------------------------

def test_reinviting_a_removed_member_produces_a_working_account(client, company):
    """remove_member sets deleted_at; the accept route's ON CONFLICT revived
    `status` but left deleted_at set, so the person got a token and then
    'Not a member of this company' on every request. Reachable on v1.1."""
    sam, _ = _add_member(client, company["id"], company["token"],
                         company["roles"], ["Vehicle Inspector"])
    mid = _membership_id(client, company["id"], company["token"], sam)
    assert client.delete(f"/companies/{company['id']}/members/{mid}",
                         headers=_auth(company["token"])).status_code == 200

    inv = _invite(client, company["id"], company["token"], sam,
                  ["Vehicle Inspector"], company["roles"])
    assert inv.status_code == 200, inv.text
    a = _accept(client, inv.json()["token"])
    assert a.status_code == 200, a.text

    me = client.get("/me", headers=_auth(a.json()["access_token"]))
    assert me.status_code == 200
    assert any(m["company_id"] == company["id"]
               for m in me.json().get("memberships", [])), \
        "accepted the invite but isn't a member"
# Append to tests/test_grants.py
#
# Settles the open item in the spec patch (Edit 7): is _grants_user_management
# stale in the same way _other_admins was?
#
# The company fixture's Company Administrator is the only member. Demoting them
# to Vehicle Manager should strand the company — nobody left who can grant the
# administrator role. _other_admins now knows that (re-keyed to
# ADMIN = ANY(r.grants)), but _grants_user_management still tests company:assign,
# which Vehicle Manager holds. If that matters, losing_admin computes False and
# the guard never runs.
#
# 409 => the guard fired; my reading was wrong; drop Edit 7 from the patch.
# 200 => the gap is real; the company is now unadministrable.


def test_demoting_the_sole_admin_to_a_domain_manager_is_refused(client, company):
    """The last-admin guard should fire on any change that leaves nobody able
    to restore an administrator — not only on changes that drop company:assign.

    Vehicle Manager holds company:assign but cannot grant Company
    Administrator, so a company whose only member holds it is stranded: no
    self-service route back, and no other member to ask.
    """
    mid = _membership_id(client, company["id"], company["token"],
                         company["email"])
    r = client.patch(
        f"/companies/{company['id']}/members/{mid}",
        headers=_auth(company["token"]),
        json={"role_ids": [company["roles"]["Vehicle Manager"]]})
    assert r.status_code == 409, r.text


def test_the_sole_admin_is_still_left_administrable_after_the_refusal(client, company):
    """Belt and braces on the case above: whatever the guard decides, the
    administrator must still be able to act afterwards. If the PATCH went
    through, this is the request that proves the damage is real rather than
    theoretical — a stranded company cannot invite anyone.
    """
    mid = _membership_id(client, company["id"], company["token"],
                         company["email"])
    client.patch(
        f"/companies/{company['id']}/members/{mid}",
        headers=_auth(company["token"]),
        json={"role_ids": [company["roles"]["Vehicle Manager"]]})

    # Company Administrator is the only role that may grant Company
    # Administrator, so this is the recovery path. It must still work.
    r = _invite(client, company["id"], company["token"], _addr("rescue"),
                ["Company Administrator"], company["roles"])
    assert r.status_code == 200, r.text


def test_demoting_the_sole_admin_to_a_manager_is_refused(client, company):
    """Manager holds company:assign but §4.2 refuses Manager -> Company
    Administrator, so the same stranding applies. Sibling of the two Vehicle
    Manager cases; the role §11 does not name."""
    mid = _membership_id(client, company["id"], company["token"],
                         company["email"])
    r = client.patch(
        f"/companies/{company['id']}/members/{mid}",
        headers=_auth(company["token"]),
        json={"role_ids": [company["roles"]["Manager"]]})
    assert r.status_code == 409, r.text
