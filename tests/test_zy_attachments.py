"""Photos/PDFs leave synced records for file storage; records keep links
(api/attachments.py, routers/files.py, move_attachments_to_spaces.py)."""
import base64
import json

from psycopg.types.json import Jsonb

STATE = {}

JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-bytes" * 50
PDF = b"%PDF-1.4\n" + b"pdf-bytes" * 50


def _durl(mime, data):
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def _pool():
    from api.db import get_pool
    return get_pool()


def _file_count(cid):
    with _pool().connection() as conn:
        return conn.execute("SELECT count(*) AS n FROM files WHERE company_id = %s",
                            (cid,)).fetchone()["n"]


def _path(link):
    return link.split("://", 1)[1].split("/", 1)[1]   # drop scheme + host


def test_setup(client):
    r = client.post("/auth/signup", json={
        "company_name": "PhotoCo", "name": "Pat Admin",
        "email": "pat@photoco.com", "password": "photopass123"})
    assert r.status_code == 200, r.text
    STATE["cid"] = r.json()["company_id"]
    STATE["tok"] = r.json()["access_token"]


def test_put_moves_photos_and_pdfs_out_of_the_record(client):
    cid = STATE["cid"]
    vehicles = [{"id": "v1", "vehicleId": "VAN-1", "photo": _durl("image/jpeg", JPEG)}]
    r = client.put(f"/companies/{cid}/collections/vehicles",
                   headers=_auth(STATE["tok"]), json={"data": vehicles})
    assert r.status_code == 200, r.text
    link = r.json()["data"][0]["photo"]
    assert link.startswith("http") and "/files/" in link
    STATE["photo_link"] = link

    tickets = {"v1": [{"id": "t1", "attachments": [
        {"name": "invoice.pdf", "type": "application/pdf", "size": len(PDF),
         "dataURL": _durl("application/pdf", PDF)}]}]}
    r = client.put(f"/companies/{cid}/collections/tickets",
                   headers=_auth(STATE["tok"]), json={"data": tickets})
    assert r.status_code == 200, r.text
    att = r.json()["data"]["v1"][0]["attachments"][0]
    assert att["name"] == "invoice.pdf" and "/files/" in att["dataURL"]
    STATE["pdf_link"] = att["dataURL"]

    # The database holds links only.
    with _pool().connection() as conn:
        rows = conn.execute("SELECT data FROM app_collections WHERE company_id = %s",
                            (cid,)).fetchall()
        f = conn.execute("""SELECT filename, mime, kind FROM files
                            WHERE company_id = %s AND mime = 'application/pdf'""",
                         (cid,)).fetchone()
    assert "data:" not in json.dumps([x["data"] for x in rows])
    assert f["filename"] == "invoice.pdf" and f["kind"] == "pdf"
    # GET returns the same links
    got = client.get(f"/companies/{cid}/collections/vehicles",
                     headers=_auth(STATE["tok"])).json()["data"]
    assert got[0]["photo"] == link


def test_link_serves_the_file_without_sign_in(client):
    r = client.get(_path(STATE["photo_link"]))
    assert r.status_code == 200
    assert r.content == JPEG and r.headers["content-type"] == "image/jpeg"
    assert r.headers["x-content-type-options"] == "nosniff"
    r = client.get(_path(STATE["pdf_link"]))
    assert r.content == PDF and r.headers["content-type"] == "application/pdf"
    assert "invoice.pdf" in r.headers["content-disposition"]
    assert client.head(_path(STATE["photo_link"])).status_code == 200


def test_link_with_wrong_signature_or_id_is_404(client):
    path = _path(STATE["photo_link"])
    base, sig = path.rsplit("/", 1)
    assert client.get(f"{base}/{'0' * len(sig)}").status_code == 404
    assert client.get("/files/not-a-uuid/abc").status_code == 404
    other = "/files/00000000-0000-0000-0000-000000000000/" + sig
    assert client.get(other).status_code == 404


def test_resending_the_same_file_reuses_it(client):
    cid = STATE["cid"]
    before = _file_count(cid)
    vehicles = [{"id": "v1", "vehicleId": "VAN-1", "photo": _durl("image/jpeg", JPEG)},
                {"id": "v2", "vehicleId": "VAN-2", "photo": _durl("image/jpeg", JPEG)}]
    r = client.put(f"/companies/{cid}/collections/vehicles",
                   headers=_auth(STATE["tok"]), json={"data": vehicles})
    assert r.status_code == 200
    out = r.json()["data"]
    assert out[0]["photo"] == out[1]["photo"] == STATE["photo_link"]
    assert _file_count(cid) == before


def test_links_and_other_values_pass_through_untouched(client):
    cid = STATE["cid"]
    svg = _durl("image/svg+xml", b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    fake_png = "data:image/png;base64,aGVsbG8="          # not really a PNG
    vehicles = [{"id": "v1", "photo": STATE["photo_link"]},
                {"id": "v3", "photo": svg}, {"id": "v4", "photo": fake_png}]
    before = _file_count(cid)
    r = client.put(f"/companies/{cid}/collections/vehicles",
                   headers=_auth(STATE["tok"]), json={"data": vehicles})
    assert r.status_code == 200
    assert "data" not in r.json()          # nothing moved -> no copy sent back
    got = client.get(f"/companies/{cid}/collections/vehicles",
                     headers=_auth(STATE["tok"])).json()["data"]
    assert got == vehicles
    assert _file_count(cid) == before


def test_conflict_stores_nothing(client):
    cid = STATE["cid"]
    before = _file_count(cid)
    new_jpeg = JPEG + b"different"
    r = client.put(f"/companies/{cid}/collections/vehicles",
                   headers=_auth(STATE["tok"]),
                   json={"data": [{"id": "v1", "photo": _durl("image/jpeg", new_jpeg)}],
                         "base_updated_at": "2000-01-01T00:00:00+00:00"})
    assert r.status_code == 409
    assert _file_count(cid) == before


def test_removed_company_links_stop_working(client):
    r = client.post("/auth/signup", json={
        "company_name": "GoneCo", "name": "Gil", "email": "gil@goneco.com",
        "password": "gonepass123"})
    cid, tok = r.json()["company_id"], r.json()["access_token"]
    other = JPEG + b"goneco"
    r = client.put(f"/companies/{cid}/collections/vehicles", headers=_auth(tok),
                   json={"data": [{"id": "g1", "photo": _durl("image/jpeg", other)}]})
    path = _path(r.json()["data"][0]["photo"])
    assert client.get(path).status_code == 200
    with _pool().connection() as conn:
        conn.execute("UPDATE companies SET deleted_at = now() WHERE id = %s", (cid,))
    assert client.get(path).status_code == 404


# --- the one-time move of files already in the database --------------------

def _seed_old_style(cid):
    """A record written before this change: embedded files, straight in the DB."""
    data = {"pm1": [{"id": "s1", "atts": [
        {"name": "quote.pdf", "type": "application/pdf",
         "dataURL": _durl("application/pdf", PDF + b"old")}],
        "photo": _durl("image/jpeg", JPEG + b"old")}]}
    with _pool().connection() as conn:
        conn.execute(
            """INSERT INTO app_collections (company_id, key, data)
               VALUES (%s, 'propertyMaintSpend', %s)
               ON CONFLICT (company_id, key) DO UPDATE SET data = EXCLUDED.data""",
            (cid, Jsonb(data)))
        return conn.execute(
            """SELECT updated_at FROM app_collections
               WHERE company_id = %s AND key = 'propertyMaintSpend'""",
            (cid,)).fetchone()["updated_at"]


def _stored(cid):
    with _pool().connection() as conn:
        return conn.execute(
            """SELECT data, updated_at FROM app_collections
               WHERE company_id = %s AND key = 'propertyMaintSpend'""",
            (cid,)).fetchone()


def test_script_refuses_without_backup_flag():
    import pytest

    from move_attachments_to_spaces import main
    with pytest.raises(SystemExit):
        main(["--apply", "--allow-local-storage"])
    with pytest.raises(SystemExit):          # local storage needs explicit OK
        main(["--apply", "--i-have-a-backup"])


def test_script_dry_run_changes_nothing():
    from move_attachments_to_spaces import main
    cid = STATE["cid"]
    _seed_old_style(cid)
    before, files = _stored(cid), _file_count(cid)
    assert main(["--company", cid]) == 0
    assert _stored(cid) == before and _file_count(cid) == files


def test_script_moves_existing_files_and_keeps_timestamp(client):
    from move_attachments_to_spaces import main
    cid = STATE["cid"]
    ts = _seed_old_style(cid)
    files = _file_count(cid)
    assert main(["--apply", "--i-have-a-backup", "--allow-local-storage",
                 "--company", cid]) == 0
    row = _stored(cid)
    assert row["updated_at"] == ts           # no sync conflict for devices
    assert "data:" not in json.dumps(row["data"])
    rec = row["data"]["pm1"][0]
    assert client.get(_path(rec["photo"])).content == JPEG + b"old"
    assert client.get(_path(rec["atts"][0]["dataURL"])).content == PDF + b"old"
    assert _file_count(cid) == files + 2
    # re-running finds nothing to do
    assert main(["--apply", "--i-have-a-backup", "--allow-local-storage",
                 "--company", cid]) == 0
    assert _file_count(cid) == files + 2


def test_normal_writes_still_touch_updated_at(client):
    cid = STATE["cid"]
    old = _stored(cid)["updated_at"]
    r = client.put(f"/companies/{cid}/collections/propertyMaintSpend",
                   headers=_auth(STATE["tok"]), json={"data": {"pm1": []}})
    assert r.status_code == 200
    assert _stored(cid)["updated_at"] > old
