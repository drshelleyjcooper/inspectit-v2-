-- Session context for the admin member-detail view. Nullable and never
-- backfilled — tokens minted before this migration just show no context,
-- which is honest (we never captured it for them).
ALTER TABLE refresh_tokens
  ADD COLUMN ip           text,
  ADD COLUMN user_agent   text,
  ADD COLUMN last_seen_at timestamptz;
