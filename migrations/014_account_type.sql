-- Business accounts (a company with staff) vs Individual accounts (one person:
-- a homeowner, a vehicle owner). Both are a company row underneath, so data,
-- sync, roles, trials and deletion work the same; the type drives labels,
-- onboarding and (later) pricing. Existing companies are businesses.
ALTER TABLE companies ADD COLUMN IF NOT EXISTS account_type text NOT NULL DEFAULT 'business'
  CHECK (account_type IN ('business', 'individual'));
