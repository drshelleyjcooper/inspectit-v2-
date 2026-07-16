-- F12: forensic context on audit entries (who = also from where / which client)
ALTER TABLE audit_log ADD COLUMN ip text, ADD COLUMN user_agent text;

-- F11: honest label for files of unknown MIME type ('bin' instead of 'pdf')
ALTER TABLE files DROP CONSTRAINT IF EXISTS files_kind_check;
ALTER TABLE files ADD CONSTRAINT files_kind_check
  CHECK (kind IN ('pdf','image','signature','logo','diagram','bin'));
