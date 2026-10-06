-- 007_trial_grace_retention.sql
-- Trial timeline (T = trial_ends_at):
--   T-7d        reminder                        (trial_reminder_sent_at)
--   T           ended; 7 days' grace, full use   (trial_ended_notice_sent_at)
--   T+7d        paused ("suspended")            (suspended_notice_sent_at)
--   T+30d       warning: records deleted in 7d  (deletion_warning_sent_at)
--   T+37d       records deleted (soft) if RETENTION_PURGE_ENABLED
-- Each marker is claimed atomically so each email goes out once.
ALTER TABLE companies
  ADD COLUMN suspended_notice_sent_at timestamptz,
  ADD COLUMN deletion_warning_sent_at timestamptz;
