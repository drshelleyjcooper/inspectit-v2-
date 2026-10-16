# Moving photos and PDFs from the database to Spaces

Until this change, every photo and PDF added in the app was saved inside its
record as text (a base64 "data URL"), so it lived in the Postgres database.
Now the API stores the file in DigitalOcean Spaces and the record keeps only
a link (`https://inspectit.app/files/<id>/<signature>`). The app's photo
resizing (longest side 1200 px, JPEG 82%) is unchanged.

## How it works

- **New files:** the app still saves as it always has. When a record syncs,
  the API takes any photo/PDF out of it, uploads it to Spaces, and saves the
  record with a link instead (`api/attachments.py`). It sends the linked
  version back and the app swaps its own copy for it, so the browser stops
  holding the file as text too. Offline or not signed in: nothing changes
  until the next sync.
- **Links** work in `<img>` tags and "Download" links without sign-in because
  each one is signed (`FILE_LINK_SECRET`). The bucket stays private: the API
  reads the file from Spaces and serves it (`api/routers/files.py`). A link
  stops working when the company is removed.
- **Same file twice** (two vehicles with the same photo, or a device
  re-sending a file it hasn't got the link for yet) is stored once, matched
  by content hash.
- **Old files:** `move_attachments_to_spaces.py` converts what's already in
  the database, using the same code. It doesn't change each record's
  `updated_at`, so no device gets a sync conflict or loses unsaved edits.

Only JPEG, PNG, GIF, WebP and PDF are moved, and only if the bytes really
are that type. Anything else stays in the record as it was.

Known gap: removing a photo from a record doesn't delete the file from
Spaces. It's removed when the company is permanently deleted.

## Production steps (in order)

### 1. Spaces bucket and settings

1. DigitalOcean → Spaces → create a bucket (e.g. `inspectit-files`) in the
   same region as the app, **File Listing: Restricted** (private). No CDN.
2. Spaces → Access Keys → create a key limited to that bucket (read/write).
3. App → Settings → App-Level Environment Variables (mark the secrets
   **Encrypt**):
   - `STORAGE_BACKEND` = `s3`
   - `SPACES_REGION` = the bucket's region, e.g. `nyc3`
   - `SPACES_BUCKET` = the bucket name
   - `SPACES_KEY`, `SPACES_SECRET` = the access key pair (Encrypt)
   - `FILE_LINK_SECRET` = a long random value (Encrypt). Make one with
     `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`.
     Never change it afterwards: every saved link depends on it.

Check first whether `STORAGE_BACKEND` is already set: if production has
been running with the default (`local`), anything stored that way is on the
container's temporary disk and is lost on each deploy.

### 2. Deploy

Merge the `spaces-attachments` PR. On startup the API applies migration
012. From then on, new photos/PDFs go to Spaces.

Quick check: add a photo to a test vehicle, wait a few seconds, reload. It
should still show, and the bucket should have a new object under the
company's id.

### 3. Back up the database

- DigitalOcean → Databases → the cluster → **Backups**: confirm last night's
  automatic backup exists (they're kept 7 days).
- Also take your own copy: on the cluster's Overview, copy the connection
  string, then on a computer with Postgres tools:
  `pg_dump "<connection string>" -Fc -f inspectit-before-spaces.dump`

### 4. Test on a copy

1. Databases → the cluster → **Fork Database Cluster** (from now). This makes
   a separate copy; the real database isn't touched.
2. Create a second, throwaway bucket (e.g. `inspectit-files-test`).
3. App → the API component → **Console**, then run (fork's connection string,
   test bucket):

   ```
   DATABASE_URL='<fork connection string>' SPACES_BUCKET=inspectit-files-test python move_attachments_to_spaces.py
   DATABASE_URL='<fork connection string>' SPACES_BUCKET=inspectit-files-test python move_attachments_to_spaces.py --apply --i-have-a-backup
   ```

   The first is a dry run: it lists each record with embedded files. The
   second moves them, reading each upload back to check it before rewriting
   the record. Run it once more: it should report nothing left to move.
4. Destroy the fork and the test bucket afterwards (the fork is billed while
   it exists).

### 5. Run it for real

In the same Console (no overrides, so it uses the app's own settings):

```
python move_attachments_to_spaces.py
python move_attachments_to_spaces.py --apply --i-have-a-backup
```

If any record says FAILED, it was left unchanged; fix the cause and run it
again. Then open the app and check some older inspections, repair tickets
and project attachments: photos show and PDFs open.

### Undo

Restore the backup from step 3. Files already uploaded to Spaces do no harm
and can be deleted from the bucket.

## Local test (done 2026-10-16)

Copy of the local dev database (`inspectit_copy`): 11 records held 27
embedded files (5.6 MB of text). After the script, 26 were links (18 unique
files, 3.6 MB); the 27th was a fake test value (`data:image/png;base64,xx`)
and was correctly left alone. Records went from 5.8 MB to 51 kB. Every link
opened. Re-running found nothing to move. The original database was
unchanged. In the app, a 3000×2000 photo was resized to 1200×800, synced,
and the browser's copy replaced with the link.
