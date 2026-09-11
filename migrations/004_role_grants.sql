-- 004_role_grants.sql
-- USER-ROLES-SPEC v2.0 §7.2 — delegated granting
--
-- Adds the two things the module x action matrix cannot express:
--   roles.grants          which roles a role may issue
--   roles.viewer_grants   which it may issue only with the membership flag set
--   memberships.can_grant_viewers   that flag, decided per person by an admin
--
-- DDL only. Preset content is re-seeded at boot by api/presets.py, which
-- updates permissions, scope and grants on every start.
--
-- Idempotent: safe to re-run, and safe alongside the advisory-lock migration
-- runner in db.py.

ALTER TABLE roles
  ADD COLUMN IF NOT EXISTS grants text[] NOT NULL DEFAULT '{}';

ALTER TABLE roles
  ADD COLUMN IF NOT EXISTS viewer_grants text[] NOT NULL DEFAULT '{}';

ALTER TABLE memberships
  ADD COLUMN IF NOT EXISTS can_grant_viewers boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN roles.grants IS
  'Role names this role may issue in an invitation. Empty = may issue none. '
  'Rank cannot express this: the domain managers are siblings, so Property '
  'Manager must not reach Vehicle Inspector. See USER-ROLES-SPEC v2.0 4.2.';

COMMENT ON COLUMN roles.viewer_grants IS
  'Role names this role may issue only when the granting membership has '
  'can_grant_viewers = true. Always the single domain viewer.';

COMMENT ON COLUMN memberships.can_grant_viewers IS
  'The only per-membership permission override in the design. Set by a Company '
  'Administrator when assigning a manager role; two Vehicle Managers in one '
  'company may differ. Default false.';

-- No index needed here. The seeder's `ON CONFLICT (name) WHERE company_id IS
-- NULL` already works in v1.1, so the matching partial unique index exists in
-- 001_initial.sql. Adding another under a new name would create a duplicate
-- (IF NOT EXISTS matches on the index name, not the definition).

-- Every existing preset row keeps grants = '{}' until the seeder runs, which
-- happens in the same boot. Failing closed is deliberate: a half-migrated
-- company grants nothing rather than everything.
