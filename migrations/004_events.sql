-- Product-analytics event stream (Analytics Dashboard, Stage 1).
-- Append-only. The dashboard (Stage 2+) reads from rollups built on top of
-- this table, never scans raw rows for a live series — see
-- docs/analytics-api.md once Stage 2 lands. No rollup table yet: this is
-- the foundation stage only, and the rollup schema should be shaped by the
-- first real metric query rather than guessed at now. Planned retention:
-- raw rows pruned after config.EVENTS_RAW_RETENTION_DAYS once a daily
-- rollup exists to age them out into; not implemented yet for the same
-- reason.

CREATE TABLE events (
  id           bigserial PRIMARY KEY,
  company_id   uuid NOT NULL REFERENCES companies(id),
  user_id      uuid NOT NULL REFERENCES users(id),
  event_name   text NOT NULL,
  event_props  jsonb NOT NULL DEFAULT '{}',
  session_id   text NOT NULL,
  occurred_at  timestamptz NOT NULL,
  received_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ix_events_company_time ON events (company_id, occurred_at);
CREATE INDEX ix_events_name_time    ON events (event_name, occurred_at);
CREATE INDEX ix_events_user_time    ON events (user_id, occurred_at);
