-- 008_complimentary.sql
-- Complimentary (test / comped) companies: never on a trial clock, never behind
-- the paywall, never emailed about trials, never swept by the retention step.
-- Set by a platform admin (create-user form or the Companies tab).
ALTER TABLE companies
  ADD COLUMN complimentary boolean NOT NULL DEFAULT false;
