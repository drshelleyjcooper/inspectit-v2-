#!/usr/bin/env python3
"""One-time move: photos/PDFs stored inside synced records -> file storage.

Before this change the app's records (app_collections) held every photo and
PDF as base64 text. New saves are converted by the API itself
(api/attachments.py); this script converts what is already there, using the
same code, so the result is identical: each file goes to storage (Spaces in
production), a `files` row is added, and the record keeps a link.

    .venv/bin/python move_attachments_to_spaces.py                 # dry run (default): report only
    .venv/bin/python move_attachments_to_spaces.py --apply --i-have-a-backup
    .venv/bin/python move_attachments_to_spaces.py --company <uuid> ...   # one company only

Run it with the SAME settings as the deployed API — DATABASE_URL,
STORAGE_BACKEND=s3, SPACES_*, APP_BASE_URL, and FILE_LINK_SECRET (or
JWT_SECRET if FILE_LINK_SECRET isn't set). The links are signed with that
secret and built from APP_BASE_URL; with different values they won't open.

Deploy the API first (it adds migration 012, and from then on new saves are
converted), then back up the database, then run this. Safe to re-run: a
record with nothing embedded left is skipped, and a file already stored is
reused (matched by content hash), never uploaded twice.

Each record is its own transaction: uploads are read back from storage and
checked against their hash before the record is rewritten; any failure rolls
that record back and the script carries on with the next one. updated_at is
left as it was (see migration 012), so no device sees a sync conflict.
"""
import argparse
import hashlib
import sys

from psycopg.types.json import Jsonb

from api import config
from api.attachments import count_inline, externalize
from api.db import audit, get_pool
from api.storage import get_storage


def _mb(n):
    return f"{n / 1048576:.1f} MB"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true",
                    help="actually move files (default is a dry run)")
    ap.add_argument("--i-have-a-backup", action="store_true",
                    help="required with --apply")
    ap.add_argument("--company", help="only this company id")
    ap.add_argument("--allow-local-storage", action="store_true",
                    help="allow --apply with STORAGE_BACKEND=local (testing)")
    args = ap.parse_args(argv)

    if args.apply and not args.i_have_a_backup:
        sys.exit("Back up the database first, then add --i-have-a-backup.")
    if args.apply and config.STORAGE_BACKEND != "s3" and not args.allow_local_storage:
        sys.exit("STORAGE_BACKEND is not 's3' — files would go to local disk, "
                 "not Spaces. Set the Spaces env vars (or pass "
                 "--allow-local-storage for a local test).")

    pool = get_pool()
    with pool.connection() as conn:
        has_col = conn.execute(
            """SELECT 1 FROM information_schema.columns
               WHERE table_name = 'files' AND column_name = 'sha256'""").fetchone()
        if not has_col:
            sys.exit("Migration 012 isn't applied — deploy the API first.")
        sql = """SELECT ac.id, ac.company_id, ac.key, ac.updated_by, c.name
                 FROM app_collections ac JOIN companies c ON c.id = ac.company_id
                 WHERE c.deleted_at IS NULL"""
        params = []
        if args.company:
            sql += " AND ac.company_id = %s"
            params.append(args.company)
        rows = conn.execute(sql + " ORDER BY c.name, ac.key", params).fetchall()

    mode = "APPLY" if args.apply else "DRY RUN (nothing changed)"
    print(f"{mode} — storage: {config.STORAGE_BACKEND}, links: "
          f"{config.APP_BASE_URL}/files/…, {len(rows)} record(s) to check\n")

    totals = {"records": 0, "embedded": 0, "text_bytes": 0, "moved": 0,
              "new_files": 0, "new_bytes": 0, "left": 0, "failed": 0}
    storage = get_storage()
    for r in rows:
        label = f"{r['name']} / {r['key']}"
        try:
            with pool.connection() as conn:
                conn.execute("SET LOCAL inspectit.keep_updated_at = 'on'")
                cur = conn.execute(
                    "SELECT data FROM app_collections WHERE id = %s FOR UPDATE",
                    (r["id"],)).fetchone()
                n, size = count_inline(cur["data"])
                if not n:
                    continue
                totals["records"] += 1
                totals["embedded"] += n
                totals["text_bytes"] += size
                if not args.apply:
                    print(f"  {label}: {n} embedded file(s), {_mb(size)} of text")
                    continue
                data, st = externalize(conn, r["company_id"], r["updated_by"],
                                       cur["data"])
                for fid in st["new_files"]:
                    f = conn.execute(
                        "SELECT storage_key, sha256 FROM files WHERE id = %s",
                        (fid,)).fetchone()
                    got = hashlib.sha256(storage.read(f["storage_key"])).hexdigest()
                    if got != f["sha256"]:
                        raise RuntimeError(f"read-back mismatch for file {fid}")
                conn.execute("UPDATE app_collections SET data = %s WHERE id = %s",
                             (Jsonb(data), r["id"]))
                audit(conn, r["company_id"], None, "update", "collection", None,
                      {"key": r["key"], "files_moved": st["moved"],
                       "by": "move_attachments_to_spaces"})
                left = count_inline(data)[0]
                totals["moved"] += st["moved"]
                totals["new_files"] += len(st["new_files"])
                totals["new_bytes"] += st["new_bytes"]
                totals["left"] += left
                note = f", {left} left as-is (not a valid photo/PDF)" if left else ""
                print(f"  {label}: moved {st['moved']}, uploaded "
                      f"{len(st['new_files'])} ({_mb(st['new_bytes'])}){note}")
        except Exception as e:  # this record rolled back; keep going
            totals["failed"] += 1
            print(f"  {label}: FAILED, left unchanged — {e}")

    print()
    print(f"Records with embedded files: {totals['records']} "
          f"({totals['embedded']} files, {_mb(totals['text_bytes'])} of text)")
    if args.apply:
        print(f"Replaced with links: {totals['moved']}; uploaded "
              f"{totals['new_files']} new file(s), {_mb(totals['new_bytes'])}")
        if totals["left"]:
            print(f"Left embedded (not a valid photo/PDF): {totals['left']}")
    if totals["failed"]:
        print(f"FAILED records (unchanged; safe to re-run): {totals['failed']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
