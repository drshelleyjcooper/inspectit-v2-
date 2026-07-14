-- Inspectit backend — initial schema (from BACKEND-SCHEMA.md, 2026-07-14)
-- Requires PostgreSQL 13+ (gen_random_uuid built in).

-- ---------- shared trigger: keep updated_at current ----------
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ==================== tenancy & identity ====================

CREATE TABLE companies (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name          text NOT NULL,
  address       text,
  city          text,
  state         text,
  zip           text,
  phone         text,
  email         text,
  logo_file_id  uuid,   -- FK added after files exists
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  deleted_at    timestamptz
);

CREATE TABLE users (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email          text NOT NULL UNIQUE,
  password_hash  text,
  auth_provider  text NOT NULL DEFAULT 'local',
  name           text NOT NULL,
  phone          text,
  photo_file_id  uuid,  -- FK added after files exists
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE roles (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id   uuid REFERENCES companies(id),   -- NULL = built-in preset
  name         text NOT NULL,
  scope        text NOT NULL DEFAULT 'company' CHECK (scope IN ('company','assigned')),
  permissions  jsonb NOT NULL,
  is_preset    boolean NOT NULL DEFAULT false,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  deleted_at   timestamptz
);
CREATE UNIQUE INDEX uq_roles_preset_name ON roles (name) WHERE company_id IS NULL;

CREATE TABLE memberships (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id  uuid NOT NULL REFERENCES companies(id),
  user_id     uuid NOT NULL REFERENCES users(id),
  status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended')),
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  deleted_at  timestamptz,
  UNIQUE (company_id, user_id)
);
CREATE INDEX ix_memberships_user ON memberships (user_id) WHERE deleted_at IS NULL;

CREATE TABLE membership_roles (
  membership_id uuid NOT NULL REFERENCES memberships(id) ON DELETE CASCADE,
  role_id       uuid NOT NULL REFERENCES roles(id),
  PRIMARY KEY (membership_id, role_id)
);

CREATE TABLE invitations (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id  uuid NOT NULL REFERENCES companies(id),
  email       text NOT NULL,
  role_ids    uuid[] NOT NULL,
  token       text NOT NULL UNIQUE,
  status      text NOT NULL DEFAULT 'pending'
              CHECK (status IN ('pending','accepted','expired','revoked')),
  invited_by  uuid NOT NULL REFERENCES users(id),
  expires_at  timestamptz NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_invitations_company ON invitations (company_id);

-- auth plumbing (custom API owns auth on DigitalOcean)
CREATE TABLE refresh_tokens (
  jti         uuid PRIMARY KEY,
  user_id     uuid NOT NULL REFERENCES users(id),
  expires_at  timestamptz NOT NULL,
  revoked_at  timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_refresh_user ON refresh_tokens (user_id);

CREATE TABLE password_resets (
  token_hash  text PRIMARY KEY,
  user_id     uuid NOT NULL REFERENCES users(id),
  expires_at  timestamptz NOT NULL,
  used_at     timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- ==================== files (object storage references) ====================

CREATE TABLE files (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id       uuid NOT NULL REFERENCES companies(id),
  uploaded_by      uuid REFERENCES users(id),
  storage_key      text NOT NULL,
  filename         text NOT NULL,
  mime             text NOT NULL,
  size_bytes       bigint NOT NULL,
  kind             text NOT NULL CHECK (kind IN ('pdf','image','signature','logo','diagram')),
  thumb_key        text,
  attached_to_type text,
  attached_to_id   uuid,
  created_at       timestamptz NOT NULL DEFAULT now(),
  deleted_at       timestamptz
);
CREATE INDEX ix_files_attached ON files (company_id, attached_to_type, attached_to_id)
  WHERE deleted_at IS NULL;

ALTER TABLE companies ADD CONSTRAINT fk_companies_logo
  FOREIGN KEY (logo_file_id) REFERENCES files(id);
ALTER TABLE users ADD CONSTRAINT fk_users_photo
  FOREIGN KEY (photo_file_id) REFERENCES files(id);

-- ==================== assignments (data scoping) ====================

CREATE TABLE assignments (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id    uuid NOT NULL REFERENCES companies(id),
  user_id       uuid NOT NULL REFERENCES users(id),
  subject_type  text NOT NULL CHECK (subject_type IN ('vehicle','property','project')),
  subject_id    uuid NOT NULL,
  duty          text NOT NULL CHECK (duty IN ('inspection','maintenance','manage')),
  assigned_by   uuid NOT NULL REFERENCES users(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  deleted_at    timestamptz
);
CREATE UNIQUE INDEX uq_assignments ON assignments
  (company_id, user_id, subject_type, subject_id, duty) WHERE deleted_at IS NULL;
CREATE INDEX ix_assignments_lookup ON assignments
  (company_id, user_id, subject_type) WHERE deleted_at IS NULL;

-- ==================== core domain ====================

CREATE TABLE vehicles (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id       uuid NOT NULL REFERENCES companies(id),
  vehicle_id       text NOT NULL,
  plate            text,
  make_model       text,
  vtype            text NOT NULL DEFAULT 'auto',
  current_odometer integer,
  photo_file_id    uuid REFERENCES files(id),
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  deleted_at       timestamptz
);
CREATE INDEX ix_vehicles_company ON vehicles (company_id) WHERE deleted_at IS NULL;

CREATE TABLE properties (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id     uuid NOT NULL REFERENCES companies(id),
  property_id    text NOT NULL,
  ptype          text NOT NULL DEFAULT 'residential',
  street         text,
  city           text,
  state          text,
  zip            text,
  photo_file_id  uuid REFERENCES files(id),
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  deleted_at     timestamptz
);
CREATE INDEX ix_properties_company ON properties (company_id) WHERE deleted_at IS NULL;

CREATE TABLE inspections (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id        uuid NOT NULL REFERENCES companies(id),
  kind              text NOT NULL CHECK (kind IN ('vehicle','property')),
  vehicle_id        uuid REFERENCES vehicles(id),
  property_id       uuid REFERENCES properties(id),
  inspected_at      date NOT NULL,
  inspector_user_id uuid REFERENCES users(id),
  inspector_name    text,
  template_key      text,
  odometer          integer,
  overall_condition text,
  results           jsonb NOT NULL DEFAULT '{}',
  diagram           jsonb,
  signature_file_id uuid REFERENCES files(id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  deleted_at        timestamptz,
  CHECK ((kind = 'vehicle') = (vehicle_id IS NOT NULL)),
  CHECK ((kind = 'property') = (property_id IS NOT NULL))
);
CREATE INDEX ix_inspections_vehicle ON inspections (company_id, vehicle_id) WHERE deleted_at IS NULL;
CREATE INDEX ix_inspections_property ON inspections (company_id, property_id) WHERE deleted_at IS NULL;

CREATE TABLE inspection_templates (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id  uuid REFERENCES companies(id),   -- NULL = built-in
  kind        text NOT NULL CHECK (kind IN ('vehicle','property')),
  label       text NOT NULL,
  template    jsonb NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  deleted_at  timestamptz
);

CREATE TABLE repair_tickets (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id   uuid NOT NULL REFERENCES companies(id),
  kind         text NOT NULL CHECK (kind IN ('vehicle','property')),
  vehicle_id   uuid REFERENCES vehicles(id),
  property_id  uuid REFERENCES properties(id),
  ticket_date  date,
  description  text,
  status       text,
  odometer     integer,
  cost         numeric(12,2),
  details      jsonb NOT NULL DEFAULT '{}',
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  deleted_at   timestamptz,
  CHECK ((kind = 'vehicle') = (vehicle_id IS NOT NULL)),
  CHECK ((kind = 'property') = (property_id IS NOT NULL))
);
CREATE INDEX ix_tickets_vehicle ON repair_tickets (company_id, vehicle_id) WHERE deleted_at IS NULL;
CREATE INDEX ix_tickets_property ON repair_tickets (company_id, property_id) WHERE deleted_at IS NULL;

CREATE TABLE maintenance_schedules (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id  uuid REFERENCES companies(id),   -- NULL = built-in
  kind        text NOT NULL CHECK (kind IN ('vehicle','property')),
  label       text NOT NULL,
  template    jsonb NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  deleted_at  timestamptz
);

CREATE TABLE maintenance_state (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id   uuid NOT NULL REFERENCES companies(id),
  kind         text NOT NULL CHECK (kind IN ('vehicle','property')),
  vehicle_id   uuid REFERENCES vehicles(id),
  property_id  uuid REFERENCES properties(id),
  item_key     text NOT NULL,
  last_done    date,
  odometer_at  integer,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  deleted_at   timestamptz,
  CHECK ((kind = 'vehicle') = (vehicle_id IS NOT NULL)),
  CHECK ((kind = 'property') = (property_id IS NOT NULL))
);
CREATE UNIQUE INDEX uq_maint_state ON maintenance_state
  (company_id, kind, COALESCE(vehicle_id, property_id), item_key) WHERE deleted_at IS NULL;

CREATE TABLE maintenance_spend (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id   uuid NOT NULL REFERENCES companies(id),
  kind         text NOT NULL CHECK (kind IN ('vehicle','property')),
  vehicle_id   uuid REFERENCES vehicles(id),
  property_id  uuid REFERENCES properties(id),
  item_key     text NOT NULL,
  spend_date   date NOT NULL,
  cost         numeric(12,2) NOT NULL,
  odometer     integer,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  deleted_at   timestamptz,
  CHECK ((kind = 'vehicle') = (vehicle_id IS NOT NULL)),
  CHECK ((kind = 'property') = (property_id IS NOT NULL))
);
CREATE INDEX ix_spend_vehicle ON maintenance_spend (company_id, vehicle_id, spend_date) WHERE deleted_at IS NULL;
CREATE INDEX ix_spend_property ON maintenance_spend (company_id, property_id, spend_date) WHERE deleted_at IS NULL;

CREATE TABLE warranties (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id     uuid NOT NULL REFERENCES companies(id),
  kind           text NOT NULL CHECK (kind IN ('vehicle','property')),
  vehicle_id     uuid REFERENCES vehicles(id),
  property_id    uuid REFERENCES properties(id),
  item           text NOT NULL,
  vendor         text,
  purchase_date  date,
  warranty_exp   date,
  last_serviced  date,
  notes          text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  deleted_at     timestamptz,
  CHECK ((kind = 'vehicle') = (vehicle_id IS NOT NULL)),
  CHECK ((kind = 'property') = (property_id IS NOT NULL))
);
CREATE INDEX ix_warranties_vehicle ON warranties (company_id, vehicle_id) WHERE deleted_at IS NULL;
CREATE INDEX ix_warranties_property ON warranties (company_id, property_id) WHERE deleted_at IS NULL;

CREATE TABLE projects (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id      uuid NOT NULL REFERENCES companies(id),
  property_id     uuid NOT NULL REFERENCES properties(id),
  name            text NOT NULL,
  project_date    date,
  goals           text,
  project_type    text,
  status          text NOT NULL DEFAULT 'active' CHECK (status IN ('active','hold','done')),
  initial_budget  numeric(12,2),
  revised_budget  numeric(12,2),
  sections        jsonb NOT NULL DEFAULT '{}',
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  deleted_at      timestamptz
);
CREATE INDEX ix_projects_company ON projects (company_id) WHERE deleted_at IS NULL;
CREATE INDEX ix_projects_property ON projects (company_id, property_id) WHERE deleted_at IS NULL;

CREATE TABLE project_estimates (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id  uuid NOT NULL REFERENCES companies(id),
  project_id  uuid NOT NULL REFERENCES projects(id),
  category    text,
  description text,
  amount      numeric(12,2),
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  deleted_at  timestamptz
);
CREATE INDEX ix_estimates_project ON project_estimates (project_id) WHERE deleted_at IS NULL;

CREATE TABLE project_payments (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id      uuid NOT NULL REFERENCES companies(id),
  project_id      uuid NOT NULL REFERENCES projects(id),
  description     text,
  amount          numeric(12,2),
  due_date        date,
  payment_type    text,
  paid            boolean NOT NULL DEFAULT false,
  paid_date       date,
  receipt_file_id uuid REFERENCES files(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  deleted_at      timestamptz
);
CREATE INDEX ix_payments_project ON project_payments (project_id) WHERE deleted_at IS NULL;

CREATE TABLE option_lists (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id  uuid NOT NULL REFERENCES companies(id),
  list_key    text NOT NULL,
  items       text[] NOT NULL DEFAULT '{}',
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (company_id, list_key)
);

-- ==================== audit ====================

CREATE TABLE audit_log (
  id            bigserial PRIMARY KEY,
  company_id    uuid NOT NULL,
  user_id       uuid,
  action        text NOT NULL,
  subject_type  text,
  subject_id    uuid,
  details       jsonb,
  at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_audit_company_at ON audit_log (company_id, at DESC);

-- ==================== updated_at triggers ====================
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'companies','users','roles','memberships','vehicles','properties',
    'inspections','inspection_templates','repair_tickets','maintenance_schedules',
    'maintenance_state','maintenance_spend','warranties','projects',
    'project_estimates','project_payments','option_lists'
  ] LOOP
    EXECUTE format(
      'CREATE TRIGGER trg_%I_touch BEFORE UPDATE ON %I
       FOR EACH ROW EXECUTE FUNCTION touch_updated_at()', t, t);
  END LOOP;
END $$;
