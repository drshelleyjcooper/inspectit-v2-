"""Permanent deletion: rows, files, orphaned members, and every safety rail."""
import uuid

import pytest

from test_phase1 import BACKUP          # the app-export fixture used by the import tests

PW = "password123"


def _tag():
    return uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def platform_admin(client):
    from api import config
    from api.db import get_pool
    from api.routers.admin import promote_platform_admins
    t = _tag()
    d = client.post("/auth/signup", json={
        "company_name": f"Purge Ops {t}", "name": "Purge Ops",
        "email": f"purge-ops-{t}@example.com", "password": PW}).json()
    config.PLATFORM_ADMIN_EMAILS.append(f"purge-ops-{t}@example.com")
    with get_pool().connection() as conn:
        promote_platform_admins(conn)
    return {"Authorization": "Bearer " + d["access_token"], "email": f"purge-ops-{t}@example.com",
            "user_id": d["user_id"]}


def _hdr(p):
    return {"Authorization": p["Authorization"]}


def _populated_company(client, who="org"):
    """A company with real data in most tables and 7 real files on disk."""
    t = _tag()
    email = f"owner-{t}@example.com"
    d = client.post("/auth/signup", json={
        "company_name": f"Purge Co {t}", "name": f"Owner {t}", "email": email, "password": PW}).json()
    h = {"Authorization": "Bearer " + d["access_token"]}
    r = client.post(f"/companies/{d['company_id']}/import/backup", headers=h, json=BACKUP)
    assert r.status_code == 200, r.text
    # an invitation and a calendar feed, so those tables hold rows too
    roles = {x["name"]: x["id"] for x in client.get(f"/companies/{d['company_id']}/roles", headers=h).json()}
    client.post(f"/companies/{d['company_id']}/invitations", headers=h,
                json={"email": f"inv-{t}@example.com", "role_ids": [roles["Viewer"]]})
    client.put(f"/companies/{d['company_id']}/calendar", headers=h, json={})
    from api.db import get_pool
    with get_pool().connection() as conn:       # the import may rename the company from its profile
        name = conn.execute("SELECT name FROM companies WHERE id = %s", (d["company_id"],)).fetchone()["name"]
    return {"cid": d["company_id"], "name": name, "email": email, "hdr": h, "tag": t,
            "uid": d["user_id"]}


def _rows_left(cid):
    from api.db import get_pool
    out = {}
    with get_pool().connection() as conn:
        tables = [r["table_name"] for r in conn.execute(
            """SELECT table_name FROM information_schema.columns
               WHERE column_name = 'company_id' AND table_schema = 'public'
                 AND table_name <> 'purge_log'""").fetchall()]
        for tb in tables:
            n = conn.execute(f"SELECT count(*) AS n FROM {tb} WHERE company_id = %s", (cid,)).fetchone()["n"]
            if n:
                out[tb] = n
        n = conn.execute("SELECT count(*) AS n FROM companies WHERE id = %s", (cid,)).fetchone()["n"]
        if n:
            out["companies"] = n
    return out


def _files_on_disk(storage_dir, cid):
    return [p for p in (storage_dir / cid).rglob("*") if p.is_file()] if (storage_dir / cid).is_dir() else []


# ---------- the main promise ----------

def test_admin_purge_deletes_rows_files_and_orphaned_members(client, platform_admin, storage_dir):
    keep = _populated_company(client)                     # a neighbour that must be untouched
    c = _populated_company(client)
    assert len(_files_on_disk(storage_dir, c["cid"])) >= 7
    before = _rows_left(c["cid"])
    assert before.get("vehicles") and before.get("files") and before.get("audit_log") \
        and before.get("invitations") and before.get("calendar_feeds") and before.get("projects")

    r = client.post(f"/admin/companies/{c['cid']}/purge", headers=_hdr(platform_admin),
                    json={"confirm": c["name"]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["files_deleted"] >= 7 and d["bytes_deleted"] > 0 and d["users_deleted"] == 1

    assert _rows_left(c["cid"]) == {}                      # nothing, in ANY company-owned table
    assert _files_on_disk(storage_dir, c["cid"]) == []     # and nothing on disk
    assert not (storage_dir / c["cid"]).exists()
    # the owner's account existed only for this company, so it is gone too
    assert client.post("/auth/login", json={"email": c["email"], "password": PW}).status_code == 401
    # the neighbour is intact: rows and files
    assert _rows_left(keep["cid"]).get("vehicles") and len(_files_on_disk(storage_dir, keep["cid"])) >= 7
    assert client.post("/auth/login", json={"email": keep["email"], "password": PW}).status_code == 200


def test_purge_log_records_it_without_personal_data(client, platform_admin):
    c = _populated_company(client)
    client.post(f"/admin/companies/{c['cid']}/purge", headers=_hdr(platform_admin), json={"confirm": c["name"]})
    log = client.get("/admin/purge-log", headers=_hdr(platform_admin)).json()
    mine = [x for x in log if x["company_id"] == c["cid"]]
    assert len(mine) == 1 and mine[0]["reason"] == "admin" and mine[0]["files_deleted"] >= 7
    assert mine[0]["requested_by"] == platform_admin["user_id"]
    blob = str(mine[0])
    assert c["name"] not in blob and c["email"] not in blob      # only ids and counts
    from api.db import get_pool
    with get_pool().connection() as conn:
        raw = str(conn.execute("SELECT * FROM purge_log WHERE company_id = %s", (c["cid"],)).fetchone())
    assert c["name"] not in raw and c["email"] not in raw


def test_members_with_other_companies_and_platform_admins_are_kept(client, platform_admin):
    c = _populated_company(client)
    other = _populated_company(client)
    from api.db import get_pool
    with get_pool().connection() as conn:       # make c's owner ALSO a member of `other`
        role = conn.execute("SELECT id FROM roles WHERE company_id IS NULL AND name = 'Viewer'").fetchone()["id"]
        m = conn.execute("INSERT INTO memberships (company_id, user_id) VALUES (%s, %s) RETURNING id",
                         (other["cid"], c["uid"])).fetchone()["id"]
        conn.execute("INSERT INTO membership_roles (membership_id, role_id) VALUES (%s, %s)", (m, role))
        # and a platform admin who is a member of c
        pm = conn.execute("INSERT INTO memberships (company_id, user_id) VALUES (%s, %s) RETURNING id",
                          (c["cid"], platform_admin["user_id"])).fetchone()["id"]
        conn.execute("INSERT INTO membership_roles (membership_id, role_id) VALUES (%s, %s)", (pm, role))
    r = client.post(f"/admin/companies/{c['cid']}/purge", headers=_hdr(platform_admin), json={"confirm": c["name"]})
    assert r.status_code == 200 and r.json()["users_deleted"] == 0 and r.json()["users_kept"] == 2
    assert client.post("/auth/login", json={"email": c["email"], "password": PW}).status_code == 200
    assert client.get("/admin/stats", headers=_hdr(platform_admin)).status_code == 200


# ---------- safety rails ----------

def test_wrong_name_or_non_admin_deletes_nothing(client, platform_admin, storage_dir):
    c = _populated_company(client)
    bad = client.post(f"/admin/companies/{c['cid']}/purge", headers=_hdr(platform_admin), json={"confirm": "nope"})
    assert bad.status_code == 422
    assert client.post(f"/admin/companies/{c['cid']}/purge", headers=_hdr(platform_admin),
                       json={"confirm": c["name"].lower()}).status_code == 422     # exact match only
    assert client.post(f"/admin/companies/{c['cid']}/purge", headers=c["hdr"],
                       json={"confirm": c["name"]}).status_code == 403
    assert client.post(f"/admin/companies/{uuid.uuid4()}/purge", headers=_hdr(platform_admin),
                       json={"confirm": "x"}).status_code == 404
    assert _rows_left(c["cid"]).get("vehicles") and len(_files_on_disk(storage_dir, c["cid"])) >= 7


def test_retention_path_refuses_a_company_that_was_not_removed(client):
    from api.purge import PurgeRefused, purge_company
    c = _populated_company(client)
    with pytest.raises(PurgeRefused):
        purge_company(c["cid"], "retention", require_removed=True)
    assert _rows_left(c["cid"]).get("vehicles")


def test_refuses_when_a_new_company_table_is_not_covered(client, platform_admin):
    from api.db import get_pool
    c = _populated_company(client)
    with get_pool().connection() as conn:
        conn.execute("CREATE TABLE zz_future_table (id serial, company_id uuid)")
    try:
        r = client.post(f"/admin/companies/{c['cid']}/purge", headers=_hdr(platform_admin), json={"confirm": c["name"]})
        assert r.status_code == 500 and "zz_future_table" in r.json()["detail"]
        assert _rows_left(c["cid"]).get("vehicles")            # nothing was touched
    finally:
        with get_pool().connection() as conn:
            conn.execute("DROP TABLE zz_future_table")


def test_a_storage_failure_stops_before_any_rows_are_deleted(client, platform_admin, monkeypatch):
    from api import purge
    c = _populated_company(client)

    class Boom:
        def delete_company(self, cid):
            raise RuntimeError("storage unavailable")
    monkeypatch.setattr(purge, "get_storage", lambda: Boom())
    with pytest.raises(RuntimeError):
        purge.purge_company(c["cid"], "admin", confirm_name=c["name"], require_removed=False)
    assert _rows_left(c["cid"]).get("vehicles")                # retry later is possible


# ---------- removal + restore ----------

def _remove(cid, days_ago=0):
    from api.db import get_pool
    with get_pool().connection() as conn:
        conn.execute("UPDATE companies SET deleted_at = now() - make_interval(days => %s) WHERE id = %s",
                     (days_ago, cid))


def test_restore_brings_back_a_removed_company_with_a_fresh_warning(client, platform_admin):
    c = _populated_company(client)
    _remove(c["cid"])
    listed = {x["id"]: x for x in client.get("/admin/companies?include_removed=true",
                                             headers=_hdr(platform_admin)).json()}
    assert listed[c["cid"]]["access"] == "removed" and listed[c["cid"]]["deleted_for_good_at"]
    normal = {x["id"] for x in client.get("/admin/companies", headers=_hdr(platform_admin)).json()}
    assert c["cid"] not in normal
    r = client.post(f"/admin/companies/{c['cid']}/restore", headers=_hdr(platform_admin))
    assert r.status_code == 200
    assert client.get(f"/companies/{c['cid']}/vehicles", headers=c["hdr"]).status_code == 200
    from api.db import get_pool
    with get_pool().connection() as conn:
        w = conn.execute("SELECT deletion_warning_sent_at FROM companies WHERE id = %s", (c["cid"],)).fetchone()
    assert w["deletion_warning_sent_at"] is not None
    assert client.post(f"/admin/companies/{c['cid']}/restore", headers=_hdr(platform_admin)).status_code == 404


# ---------- the scheduled retention step ----------

@pytest.fixture()
def cycle_ready(monkeypatch):
    from api import config, mailer
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_not_real")
    monkeypatch.setattr(config, "RETENTION_PURGE_ENABLED", True)
    monkeypatch.setattr(mailer, "send_mail", lambda *a, **k: True)


def _expired(client, removed_days_ago, warned_days_ago=10, **flags):
    from api.db import get_pool
    c = _populated_company(client)
    with get_pool().connection() as conn:
        conn.execute(
            """UPDATE companies SET trial_ends_at = now() - interval '60 days',
                      trial_ended_notice_sent_at = now() - interval '50 days',
                      suspended_notice_sent_at = now() - interval '40 days',
                      deletion_warning_sent_at = now() - make_interval(days => %s),
                      subscribed_at = CASE WHEN %s THEN now() END,
                      complimentary = %s
               WHERE id = %s""",
            (warned_days_ago, flags.get("subscribed", False), flags.get("complimentary", False), c["cid"]))
    if removed_days_ago is not None:
        _remove(c["cid"], removed_days_ago)
    return c


def test_cycle_deletes_for_good_only_after_the_hold_and_only_eligible_companies(client, cycle_ready, storage_dir):
    from api import trial
    due = _expired(client, removed_days_ago=8)
    holding = _expired(client, removed_days_ago=3)                    # hold period not over
    paid = _expired(client, removed_days_ago=8, subscribed=True)      # subscribed meanwhile
    comped = _expired(client, removed_days_ago=8, complimentary=True)
    unwarned = _expired(client, removed_days_ago=8, warned_days_ago=2)  # warning too recent
    live = _expired(client, removed_days_ago=None)                    # never removed
    res = trial.run_trial_cycle()
    assert res["deleted_permanently"] >= 1
    assert _rows_left(due["cid"]) == {} and _files_on_disk(storage_dir, due["cid"]) == []
    for kept in (holding, paid, comped, unwarned, live):
        assert _rows_left(kept["cid"]).get("vehicles"), kept["name"]
        assert len(_files_on_disk(storage_dir, kept["cid"])) >= 7
    from api.db import get_pool
    with get_pool().connection() as conn:
        reason = conn.execute("SELECT reason FROM purge_log WHERE company_id = %s", (due["cid"],)).fetchone()
    assert reason["reason"] == "retention"


def test_cycle_does_nothing_permanent_while_the_switch_is_off(client, cycle_ready, monkeypatch):
    from api import config, trial
    monkeypatch.setattr(config, "RETENTION_PURGE_ENABLED", False)
    c = _expired(client, removed_days_ago=30)
    trial.run_trial_cycle()
    assert _rows_left(c["cid"]).get("vehicles")


def test_one_failing_company_does_not_stop_the_others(client, cycle_ready, monkeypatch, storage_dir):
    from api import purge, trial
    bad = _expired(client, removed_days_ago=9)
    good = _expired(client, removed_days_ago=9)
    real = purge.get_storage()

    class Picky:
        def delete_company(self, cid):
            if cid == bad["cid"]:
                raise RuntimeError("boom")
            return real.delete_company(cid)
    monkeypatch.setattr(purge, "get_storage", lambda: Picky())
    trial.run_trial_cycle()
    assert _rows_left(bad["cid"]).get("vehicles")           # kept, will retry next hour
    assert _rows_left(good["cid"]) == {}


# ---------- DigitalOcean Spaces (S3) deletion, against a fake client ----------

class FakeS3:
    def __init__(self, objects, fail=False):
        self.objects = dict(objects)           # key -> size
        self.fail = fail
        self.deleted_batches = []

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        page = keys[:2]                                    # tiny pages to force several rounds
        return {"Contents": [{"Key": k, "Size": self.objects[k]} for k in page],
                "IsTruncated": len(keys) > 2}

    def delete_objects(self, Bucket, Delete):
        keys = [o["Key"] for o in Delete["Objects"]]
        self.deleted_batches.append(keys)
        if self.fail:
            return {"Errors": [{"Key": keys[0], "Code": "AccessDenied"}]}
        for k in keys:
            self.objects.pop(k, None)
        return {}


def _spaces(fake):
    from api.storage import SpacesStorage
    s = SpacesStorage.__new__(SpacesStorage)
    s.bucket, s.client = "b", fake
    return s


def test_spaces_deletes_only_this_companys_prefix_across_pages():
    cid, other = str(uuid.uuid4()), str(uuid.uuid4())
    fake = FakeS3({f"{cid}/a.pdf": 10, f"{cid}/b.png": 20, f"{cid}/c.pdf": 30,
                   f"{cid}/d.pdf": 40, f"{cid}/e.pdf": 50, f"{other}/x.pdf": 99})
    n, size = _spaces(fake).delete_company(cid)
    assert (n, size) == (5, 150)
    assert list(fake.objects) == [f"{other}/x.pdf"]        # the neighbour is untouched
    assert len(fake.deleted_batches) == 3                  # 2 + 2 + 1


def test_spaces_failure_raises_so_the_purge_is_retried():
    cid = str(uuid.uuid4())
    with pytest.raises(RuntimeError):
        _spaces(FakeS3({f"{cid}/a.pdf": 1}, fail=True)).delete_company(cid)


def test_spaces_with_nothing_stored_is_fine():
    assert _spaces(FakeS3({})).delete_company(str(uuid.uuid4())) == (0, 0)
