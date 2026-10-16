-- Photos and PDFs move out of synced records (app_collections JSON) into
-- file storage (DigitalOcean Spaces in production); the record keeps a link.
-- The content hash lets a re-sent copy of the same file reuse the stored one
-- instead of uploading it again (an app that hasn't yet received the link
-- version of a record sends the embedded copy once more).
ALTER TABLE files ADD COLUMN sha256 text;
CREATE INDEX ix_files_sha256 ON files (company_id, sha256)
  WHERE deleted_at IS NULL AND sha256 IS NOT NULL;

-- The one-time move of files already in the database
-- (move_attachments_to_spaces.py) rewrites records without changing their
-- updated_at: that timestamp is the app's sync token, and bumping it would
-- make any device with unsaved edits lose them to a 409. The script sets
-- inspectit.keep_updated_at for its own transaction only.
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN
  IF current_setting('inspectit.keep_updated_at', true) = 'on' THEN
    RETURN NEW;
  END IF;
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
