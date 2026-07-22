-- Account suspension: reversible off-switch, distinct from delete.
-- memberships.status already existed with a 'suspended' value in the CHECK
-- constraint (001_initial.sql), but nothing ever set it. These columns make
-- suspension a real, auditable state instead of dead schema.

ALTER TABLE memberships
  ADD COLUMN suspended_at    timestamptz,
  ADD COLUMN suspended_by    uuid REFERENCES users(id),
  ADD COLUMN suspend_reason  text;

-- Invariant: suspended_at is set if and only if status = 'suspended'. Guards
-- against a half-suspended row surviving a bug in the application layer.
ALTER TABLE memberships ADD CONSTRAINT chk_membership_suspend_fields
  CHECK ((status = 'suspended') = (suspended_at IS NOT NULL));

CREATE INDEX ix_memberships_status ON memberships (company_id, status)
  WHERE deleted_at IS NULL;
