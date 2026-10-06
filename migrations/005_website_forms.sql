-- 005_website_forms.sql
-- Marketing-site forms (2026-10-02):
--   * Free-trial sign-up: companies get a trial end date and the optional
--     answers from the sign-up form (what they track, who it's for, size).
--   * "Schedule a Demo" requests land in demo_requests; platform admins read
--     and triage them under /admin/demo-requests.
ALTER TABLE companies
  ADD COLUMN trial_ends_at timestamptz,
  ADD COLUMN signup_info   jsonb;

CREATE TABLE demo_requests (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name          text NOT NULL,
  email         text NOT NULL,
  asset_count   integer,                       -- vehicles or properties managed
  status        text NOT NULL DEFAULT 'new'
                CHECK (status IN ('new', 'contacted', 'closed')),
  ip            text,
  user_agent    text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  handled_at    timestamptz
);

CREATE INDEX ix_demo_requests_created ON demo_requests (created_at DESC);
CREATE INDEX ix_demo_requests_status  ON demo_requests (status, created_at DESC);
