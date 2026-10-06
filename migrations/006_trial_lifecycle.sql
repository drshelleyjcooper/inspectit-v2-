-- 006_trial_lifecycle.sql
-- Free-trial lifecycle (2026-10-05):
--   subscribed_at               set when the company has a paid subscription
--                               (platform admin sets it for now; a payment
--                               webhook will later). Lifts any suspension.
--   trial_reminder_sent_at      "trial ends in 7 days" email claimed/sent
--   trial_ended_notice_sent_at  "trial ended, account paused" email claimed/sent
-- A company is SUSPENDED when trial_ends_at has passed and subscribed_at is
-- NULL. Companies with no trial_ends_at (existing/legacy) are never suspended.
ALTER TABLE companies
  ADD COLUMN subscribed_at              timestamptz,
  ADD COLUMN trial_reminder_sent_at     timestamptz,
  ADD COLUMN trial_ended_notice_sent_at timestamptz;

CREATE INDEX ix_companies_trial_ends ON companies (trial_ends_at)
  WHERE trial_ends_at IS NOT NULL AND subscribed_at IS NULL;
