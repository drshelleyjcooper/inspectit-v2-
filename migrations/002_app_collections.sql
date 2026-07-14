-- Phase 2: collection-level sync for the existing app.
-- Each row = one of the app's data keys (vehicles, tickets, projects, ...)
-- stored as a versioned JSONB document per company. The app pulls on boot and
-- pushes on change; updated_at doubles as the concurrency token.

CREATE TABLE app_collections (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id  uuid NOT NULL REFERENCES companies(id),
  key         text NOT NULL,
  data        jsonb NOT NULL,
  updated_by  uuid REFERENCES users(id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (company_id, key)
);

CREATE TRIGGER trg_app_collections_touch BEFORE UPDATE ON app_collections
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
