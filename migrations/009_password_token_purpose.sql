-- 009_password_token_purpose.sql
-- password_resets also carries "welcome" links (an admin created the account and
-- the person sets their first password). 'welcome' links skip the
-- "your password was changed" notice that a real reset sends.
ALTER TABLE password_resets
  ADD COLUMN purpose text NOT NULL DEFAULT 'reset'
  CHECK (purpose IN ('reset', 'welcome'));
