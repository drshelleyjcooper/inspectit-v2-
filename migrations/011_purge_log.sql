-- 011_purge_log.sql
-- Permanent deletion of a company (api/purge.py). The company's own rows are
-- gone afterwards, so this keeps a record that it happened. It deliberately
-- holds no personal data: no company name, no emails, only the company id,
-- why, who asked and how much was removed.
CREATE TABLE purge_log (
  id             bigserial PRIMARY KEY,
  company_id     uuid NOT NULL,
  reason         text NOT NULL CHECK (reason IN ('retention', 'admin')),
  requested_by   uuid,                       -- platform admin id (no FK: may be deleted later)
  files_deleted  integer NOT NULL DEFAULT 0,
  bytes_deleted  bigint  NOT NULL DEFAULT 0,
  rows_deleted   jsonb   NOT NULL DEFAULT '{}'::jsonb,
  users_deleted  integer NOT NULL DEFAULT 0,
  users_kept     integer NOT NULL DEFAULT 0,
  purged_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_purge_log_at ON purge_log (purged_at DESC);
