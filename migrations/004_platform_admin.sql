-- Platform admin portal (first iteration).
--   is_platform_admin : may use /admin/* (cross-tenant: create users, reset
--                       passwords, view usage). Granted via the
--                       PLATFORM_ADMIN_EMAILS env var at startup or by an
--                       existing platform admin.
--   last_login_at     : bumped on every successful /auth/login (usage stats).
--   disabled_at       : set = account may not log in or use its tokens.
ALTER TABLE users
  ADD COLUMN is_platform_admin boolean NOT NULL DEFAULT false,
  ADD COLUMN last_login_at     timestamptz,
  ADD COLUMN disabled_at       timestamptz;

CREATE INDEX ix_users_last_login ON users (last_login_at DESC);
