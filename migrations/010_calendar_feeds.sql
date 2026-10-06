-- 010_calendar_feeds.sql
-- Private calendar subscription links (one-way ICS feed per user per company).
-- The link's secret part is an HMAC over (id, nonce) keyed by a server secret
-- (CALENDAR_SECRET, falling back to JWT_SECRET): the database alone can't
-- rebuild anyone's link. "Reset my calendar link" replaces the nonce, which
-- kills the old link. Turning the calendar off sets revoked_at.
CREATE TABLE calendar_feeds (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id        uuid NOT NULL REFERENCES companies(id),
  user_id           uuid NOT NULL REFERENCES users(id),
  nonce             text NOT NULL,
  prefs             jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  rotated_at        timestamptz,
  last_accessed_at  timestamptz,
  revoked_at        timestamptz
);

-- One live feed per member.
CREATE UNIQUE INDEX calendar_feeds_one_live
  ON calendar_feeds (company_id, user_id) WHERE revoked_at IS NULL;

CREATE TRIGGER calendar_feeds_touch BEFORE UPDATE ON calendar_feeds
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
