-- When a platform admin last cleared a company's synced records
-- (POST /admin/companies/{id}/clear-records). Browsers compare it with the
-- value they last saw and drop their local copy once, so a stale copy isn't
-- pushed straight back up.
ALTER TABLE companies ADD COLUMN IF NOT EXISTS records_cleared_at timestamptz;
