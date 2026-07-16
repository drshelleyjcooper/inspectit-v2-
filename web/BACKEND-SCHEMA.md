# Inspectit — Multi-Tenant Backend Schema (v1 Draft)

**Date:** 2026-07-14
**Status:** Design document — no code exists yet. This is the blueprint to build from.
**Scope:** Database schema, tenancy model, roles & permissions, file storage, and the
migration path from today's localStorage app. Vendor-neutral: everything here is plain
PostgreSQL and applies equally to DigitalOcean Managed Postgres (with a custom API) or
Supabase (with its built-in auth/storage/row-level security).

**Deployment target (decided 2026-07-14): DigitalOcean.**
- App Platform **static site** (free tier) → serves the app itself over HTTPS
- App Platform **app** (~$5/mo) → the custom API (owns auth, permissions, signed URLs)
- **Managed PostgreSQL** (~$15/mo) → this schema, with automated backups
- **Spaces** ($5/mo, S3-compatible) → all files/photos/PDFs (§6), signed URLs, CDN
- **DO DNS** (free) → domain DNS + auto TLS certificates; the domain itself is purchased
  at an outside registrar (DO is not a registrar)

---

## 1. Guiding principles

1. **Multi-tenant from the first migration.** Every domain row carries `company_id`.
   A solo user is simply a company of one — there is no "single-user mode" to outgrow.
2. **Roles are presets over a permission matrix, not hardcoded.** The 8 roles from the
   User Roles matrix ship as built-in presets; companies can adjust or add roles without
   code changes (same philosophy as the app's built-in-plus-custom templates).
3. **Files live in object storage, never in the database.** The database stores
   references. This removes the 5 MB localStorage ceiling permanently and unlocks
   photos for repair/remodel projects.
4. **Sync-friendly records.** UUID primary keys (client-generatable), `updated_at`
   everywhere, soft deletes, idempotent writes — so offline/mobile sync can be added
   later without reshaping data.
5. **Normalize what gets reported on; keep document-like data as JSON.** Money and
   dates that feed spend reports get real columns/tables. Checklist results and
   project sub-sections that the app edits as documents stay JSONB.

---

## 2. Tenancy & identity

### 2.1 `companies`
The tenant. Also holds the profile info that appears on printed reports today.

```sql
CREATE TABLE companies (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name          text NOT NULL,
  address       text,
  city          text,
  state         text,
  zip           text,
  phone         text,
  email         text,
  logo_file_id  uuid,             -- FK to files, set later
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  deleted_at    timestamptz       -- soft delete
);
```

### 2.2 `users`
Global identity — a person, not a membership. The same person can belong to more than
one company (e.g. a contractor who inspects for two firms).

```sql
CREATE TABLE users (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email          text NOT NULL UNIQUE,
  password_hash  text,            -- argon2/bcrypt; NULL if using an external auth provider
  auth_provider  text,            -- 'local' | 'supabase' | etc.
  name           text NOT NULL,
  phone          text,
  photo_file_id  uuid,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);
```

> If built on Supabase, `users` thins out to a profile table keyed by `auth.users.id`
> and password handling disappears from our scope. On DigitalOcean with a custom API,
> the API owns hashing, sessions/JWTs, and password-reset emails.

### 2.3 `memberships` and `membership_roles`
Connects a user to a company. **A member can hold multiple roles** (decided: yes — small
companies need one person to be both Vehicle Inspector and Property Inspector). Effective
permissions are the **union** of all held roles.

```sql
CREATE TABLE memberships (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id  uuid NOT NULL REFERENCES companies(id),
  user_id     uuid NOT NULL REFERENCES users(id),
  status      text NOT NULL DEFAULT 'active',   -- 'active' | 'suspended'
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  deleted_at  timestamptz,
  UNIQUE (company_id, user_id)
);

CREATE TABLE membership_roles (
  membership_id uuid NOT NULL REFERENCES memberships(id),
  role_id       uuid NOT NULL REFERENCES roles(id),
  PRIMARY KEY (membership_id, role_id)
);
```

### 2.4 `invitations`
Admin invites a user by email; they follow a link to join.

```sql
CREATE TABLE invitations (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id  uuid NOT NULL REFERENCES companies(id),
  email       text NOT NULL,
  role_ids    uuid[] NOT NULL,
  token       text NOT NULL UNIQUE,
  status      text NOT NULL DEFAULT 'pending',  -- 'pending' | 'accepted' | 'expired' | 'revoked'
  invited_by  uuid NOT NULL REFERENCES users(id),
  expires_at  timestamptz NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);
```

---

## 3. Roles & permissions

### 3.1 The model: modules × actions

**Modules** (11) — the entity areas plus each per-entity tool, matching the app's
structure:

| Module key | Covers |
|---|---|
| `vehicles` | The vehicle records themselves (add/edit/delete vehicles) |
| `vehicle_inspections` | Inspection form + history |
| `vehicle_maintenance` | Maintenance scheduler + spend log |
| `vehicle_repairs` | Repair tickets |
| `vehicle_warranties` | Warranties / Records |
| `properties` | The property records themselves |
| `property_inspections` | |
| `property_maintenance` | |
| `property_repairs` | |
| `property_warranties` | |
| `projects` | Project dossiers (all sections) |

**Actions** (7): `view`, `create`, `edit`, `delete`, `print`, `export`, `assign`.
Notes:
- `edit` implies `create` within a module the role can access (decided: keep them
  separate columns anyway — costs nothing, allows "can edit but not create" later).
- `export` is deliberately separate from `print`: export = data egress (spend reports,
  backups). Inspectors get neither export nor delete.
- `assign` = may assign users to entities in this module (the "Assign inspectors &
  maintenance" cell).

### 3.2 `roles`

```sql
CREATE TABLE roles (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id   uuid REFERENCES companies(id),  -- NULL = built-in preset visible to all
  name         text NOT NULL,
  scope        text NOT NULL DEFAULT 'company',  -- 'company' | 'assigned'  (see §4)
  permissions  jsonb NOT NULL,   -- { "<module>": ["view","edit",...], ... }
  is_preset    boolean NOT NULL DEFAULT false,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  deleted_at   timestamptz
);
```

`permissions` example (Vehicle Inspector):

```json
{
  "vehicles":            ["view"],
  "vehicle_inspections": ["view", "create", "edit", "print"],
  "vehicle_maintenance": ["view", "edit", "print"]
}
```

### 3.3 Built-in role presets (seeded from the User Roles matrix)

Decisions applied (defaults — any company can clone a preset and adjust):
- **Mid-tier managers and inspectors do NOT get `delete`.** Deletions escalate to
  Manager/Company Administrator. Inspection reports are records; the person who filed
  one shouldn't be able to erase it. Paired with the audit log (§8).
- **Inspectors can work the maintenance scheduler** (mark done, enter costs) but not
  export.
- "Vewier" in the source PDF is treated as **Viewer**.

| Preset | Scope | Summary |
|---|---|---|
| **Company Administrator** | company | Everything, all modules, including `assign`, `delete`, `export`, and user/role/billing management |
| **Manager** | company | Everything except managing users/roles/billing (no `assign` on users; has `assign` on entities, `delete`, `export`) |
| **Vehicle Manager** | company | Vehicle modules only: view/create/edit/print/export + `assign` (inspectors & maintenance). No delete |
| **Property Manager** | company | Property modules: view/create/edit/print/export + `assign`. **Plus `projects: ["view"]`** (read-only visibility into projects on company properties — decided). No delete |
| **Project Manager** | **assigned** | `projects`: view/create/edit/print/export on **assigned projects only** (decided); `properties: ["view"]` for context. No delete |
| **Vehicle Inspector** | **assigned** | `vehicles: view`; `vehicle_inspections`: view/create/edit/print; `vehicle_maintenance`: view/edit/print. Assigned vehicles only |
| **Property Inspector** | **assigned** | Mirror of Vehicle Inspector for properties |
| **Viewer** | company | `view` + `print` on every module, nothing else |

> User management (invite/remove members, grant roles) is not a module action — it is a
> capability reserved to Company Administrator (full) and expressed through the
> `assign` action for the entity-scoped cases (Vehicle/Property Manager assigning
> inspectors).

---

## 4. Assignments (data scoping)

**Decided: assigned-only visibility** for roles with `scope = 'assigned'`. An inspector
sees only vehicles/properties assigned to them; a Project Manager sees only assigned
projects. Roles with `scope = 'company'` see everything their module permissions allow.

```sql
CREATE TABLE assignments (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id    uuid NOT NULL REFERENCES companies(id),
  user_id       uuid NOT NULL REFERENCES users(id),
  subject_type  text NOT NULL,   -- 'vehicle' | 'property' | 'project'
  subject_id    uuid NOT NULL,
  duty          text NOT NULL,   -- 'inspection' | 'maintenance' | 'manage'
  assigned_by   uuid NOT NULL REFERENCES users(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  deleted_at    timestamptz,
  UNIQUE (company_id, user_id, subject_type, subject_id, duty)
);
CREATE INDEX ON assignments (company_id, user_id, subject_type) WHERE deleted_at IS NULL;
```

**Visibility rule (enforced in the API layer, or as row-level-security policies on
Supabase):**

> A row is visible to a member if **any** held role grants `view` on the row's module,
> **and** (the role's scope is `company`, **or** an active assignment exists linking the
> member to the row's vehicle/property/project).

Special case (decided): a Property Manager's `projects: view` applies to projects whose
`property_id` belongs to the company — read-only, company-wide across properties, since
Property Manager is a company-scope role.

---

## 5. Core domain tables

All tables in this section share the same conventions (shown once here, implied
throughout): `id uuid PK DEFAULT gen_random_uuid()`, `company_id uuid NOT NULL
REFERENCES companies(id)`, `created_at`/`updated_at timestamptz NOT NULL DEFAULT now()`,
`deleted_at timestamptz` (soft delete), and an index on `(company_id)`. `updated_at` is
maintained by a trigger.

### 5.1 `vehicles`

```sql
CREATE TABLE vehicles (
  -- conventions ...
  vehicle_id   text NOT NULL,     -- the human "Vehicle ID" shown on cards/reports
  plate        text,
  make_model   text,
  vtype        text NOT NULL,     -- 'auto' | 'van' | 'comm' (drives inspection template + diagram)
  photo_file_id uuid REFERENCES files(id)
);
```

### 5.2 `properties`

```sql
CREATE TABLE properties (
  property_id  text NOT NULL,     -- "Property ID / Unit #"
  ptype        text NOT NULL,     -- 'residential' | 'commercial' | 'accessible'
  street       text, city text, state text, zip text,
  photo_file_id uuid REFERENCES files(id)
);
```

### 5.3 `inspections` (vehicle + property, one table)

Today: `K.inspections` (object keyed by vehicle id) and `K.propertyInspections`.

```sql
CREATE TABLE inspections (
  kind          text NOT NULL,          -- 'vehicle' | 'property'
  vehicle_id    uuid REFERENCES vehicles(id),
  property_id   uuid REFERENCES properties(id),
  CHECK ((kind='vehicle') = (vehicle_id IS NOT NULL)
     AND (kind='property') = (property_id IS NOT NULL)),

  inspected_at      date NOT NULL,
  inspector_user_id uuid REFERENCES users(id),
  inspector_name    text,               -- denormalized for the printed report
  template_key      text,               -- which checklist template was used
  odometer          integer,            -- vehicle only
  overall_condition text,
  results           jsonb NOT NULL,     -- categories/items/statuses/notes — the checklist payload
  diagram           jsonb,              -- damage-mark codes/positions (vehicle only)
  signature_file_id uuid REFERENCES files(id)   -- inspector signature PNG
);
```

`results` stays JSONB deliberately: templates are user-editable, categories/items vary
per company, and nothing inside the checklist is queried across records — reports render
one record at a time.

### 5.4 `inspection_templates`
Replaces the saved-template localStorage lists. Built-ins (`kind` + the current 3
property / vehicle sets) are seeded with `company_id NULL`.

```sql
CREATE TABLE inspection_templates (
  -- company_id NULL = built-in
  kind        text NOT NULL,      -- 'vehicle' | 'property'
  label       text NOT NULL,
  template    jsonb NOT NULL      -- categories/items
);
```

### 5.5 `repair_tickets`
Today: `K.tickets`, `K.propertyTickets`. Costs feed the R&M spend reports → real columns.

```sql
CREATE TABLE repair_tickets (
  kind         text NOT NULL,     -- 'vehicle' | 'property'
  vehicle_id   uuid REFERENCES vehicles(id),
  property_id  uuid REFERENCES properties(id),
  -- CHECK as in inspections
  ticket_date  date,
  description  text,
  status       text,
  odometer     integer,           -- vehicle only
  cost         numeric(12,2),
  details      jsonb              -- remaining form fields as they exist today
  -- attachments via files.attached_to (§6)
);
```

### 5.6 Maintenance scheduler (3 tables)
Today: `K.vehicleMaintenance` / `K.propertyMaintenance` (per-item last-done state),
`K.*MaintTemplates` (schedules), `K.*MaintSpend` (append-only history).

```sql
CREATE TABLE maintenance_schedules (      -- the editable template ("Standard Property Maintenance", etc.)
  kind      text NOT NULL,                -- 'vehicle' | 'property'; company_id NULL = built-in
  label     text NOT NULL,
  template  jsonb NOT NULL                -- categories → items {name, freq, miles?}
);

CREATE TABLE maintenance_state (          -- per entity, per item: when it was last done
  kind        text NOT NULL,
  vehicle_id  uuid REFERENCES vehicles(id),
  property_id uuid REFERENCES properties(id),
  item_key    text NOT NULL,              -- "Category::Item" (today's pmKey)
  last_done   date,
  odometer_at integer,                    -- vehicle only
  UNIQUE (company_id, kind, coalesce(vehicle_id, property_id), item_key)
);

CREATE TABLE maintenance_spend (          -- append-only cost history → spend reports
  kind        text NOT NULL,
  vehicle_id  uuid REFERENCES vehicles(id),
  property_id uuid REFERENCES properties(id),
  item_key    text NOT NULL,
  spend_date  date NOT NULL,
  cost        numeric(12,2) NOT NULL,
  odometer    integer
);
```

The current-odometer override (`"__odo__"`) becomes a proper column on `vehicles`
(`current_odometer integer`) instead of a reserved key.

### 5.7 `warranties`
Today: `K.vehicleWarranties` / `K.propertyWarranties`.

```sql
CREATE TABLE warranties (
  kind          text NOT NULL,
  vehicle_id    uuid REFERENCES vehicles(id),
  property_id   uuid REFERENCES properties(id),
  item          text NOT NULL,
  vendor        text,
  purchase_date date,
  warranty_exp  date,
  last_serviced date,
  notes         text
  -- PDF docs via files.attached_to
);
```

### 5.8 Projects (1 main table + 2 money tables + JSONB sections)
Today: `K.projects`. Split by the reporting rule (§1.5): **estimates and payments feed
spend reports → normalized tables**; scope/contractors/permits/final-records are
document-like → JSONB.

```sql
CREATE TABLE projects (
  property_id     uuid NOT NULL REFERENCES properties(id),
  name            text NOT NULL,
  project_date    date,
  goals           text,
  project_type    text,
  status          text NOT NULL DEFAULT 'active',  -- 'active' | 'hold' | 'done'
  initial_budget  numeric(12,2),
  revised_budget  numeric(12,2),
  sections        jsonb NOT NULL DEFAULT '{}'
  -- sections = { scope: [{id, stype, desc, file_ids:[]}],
  --              contractors: [{id, ctype, company, contact, phone, cell, bphone, email,
  --                             notes, docs: {proposals:[file_ids], license:[], insurance:[],
  --                                           references:[], contracts:[], other:[]}}],
  --              permits: [{id, ptype, status, file_ids:[]}],
  --              final_records: [{id, rtype, desc, file_ids:[]}] }
);

CREATE TABLE project_estimates (           -- cost line items
  project_id  uuid NOT NULL REFERENCES projects(id),
  category    text,
  description text,
  amount      numeric(12,2)
);

CREATE TABLE project_payments (            -- payment schedule rows
  project_id   uuid NOT NULL REFERENCES projects(id),
  description  text,
  amount       numeric(12,2),
  due_date     date,
  payment_type text,                       -- Cash/Check/... from option list
  paid         boolean NOT NULL DEFAULT false,
  paid_date    date,                       -- NEW: fixes the current "ranged reports filter by due date" limitation
  receipt_file_id uuid REFERENCES files(id)
);
```

> `paid_date` deliberately added: today's spend reports must filter paid payments by due
> date because no paid-date exists. Marking paid should stamp the date.

Attachment references inside `sections` are **file ids only** — the files themselves are
first-class rows in `files` (§6), so quota accounting and access control never depend on
parsing JSONB.

---

## 6. Files & object storage

The single biggest change from today (base64 data-URLs in localStorage). One table
tracks every stored object; binaries live in S3-compatible object storage (DigitalOcean
Spaces, Supabase Storage, or any S3).

```sql
CREATE TABLE files (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id     uuid NOT NULL REFERENCES companies(id),
  uploaded_by    uuid REFERENCES users(id),
  storage_key    text NOT NULL,       -- '<company_id>/<yyyy>/<uuid>.<ext>'
  filename       text NOT NULL,       -- original name, shown in the UI
  mime           text NOT NULL,       -- 'application/pdf' | 'image/jpeg' | 'image/png'
  size_bytes     bigint NOT NULL,
  kind           text NOT NULL,       -- 'pdf' | 'image' | 'signature' | 'logo'
  thumb_key      text,                -- small preview (images) — generated on upload
  attached_to_type text,              -- 'repair_ticket' | 'warranty' | 'project' | 'inspection' | ...
  attached_to_id   uuid,
  created_at     timestamptz NOT NULL DEFAULT now(),
  deleted_at     timestamptz
);
CREATE INDEX ON files (company_id, attached_to_type, attached_to_id) WHERE deleted_at IS NULL;
```

Rules:
- **Access is via short-lived signed URLs only** — no public buckets. Company A must
  never be able to reach company B's objects even with a guessed URL.
- **Uploads go direct to storage** (client asks the API for a signed upload URL), so
  phone photo uploads don't funnel through the API server.
- **Thumbnails generated server-side on image upload** (project galleries on mobile).
- **Images are back on the menu**: the PDF-only rule for repair-ticket attachments was a
  localStorage-quota mitigation, not a product decision. Repair/remodel photos are
  expected heavy use.
- Per-company storage accounting = `SUM(size_bytes)` — this is what plan limits/billing
  meter later.

---

## 7. Option lists (the customizable dropdowns)

Today `K.projectMeta` holds global custom lists (project types, contractor types, cost
categories, permit types, final-record types, scope types, payment types). These become
**company-scoped** rows; built-in defaults are seeded in code (as now) and company rows
extend them.

```sql
CREATE TABLE option_lists (
  company_id  uuid NOT NULL REFERENCES companies(id),
  list_key    text NOT NULL,   -- 'project_types' | 'contractor_types' | 'cost_categories'
                               -- | 'permit_types' | 'final_types' | 'scope_types' | 'payment_types'
  items       text[] NOT NULL DEFAULT '{}',
  UNIQUE (company_id, list_key)
);
```

---

## 8. Audit log

Answers "who deleted that inspection?" — cheap now, impossible to reconstruct later.
Written by the API on every create/update/delete/export/assign.

```sql
CREATE TABLE audit_log (
  id            bigserial PRIMARY KEY,
  company_id    uuid NOT NULL,
  user_id       uuid,
  action        text NOT NULL,        -- 'create' | 'update' | 'delete' | 'export' | 'assign' | 'login'
  subject_type  text,
  subject_id    uuid,
  details       jsonb,                -- e.g. changed fields, export type
  at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON audit_log (company_id, at DESC);
```

Append-only; no update/delete API. This also means soft-deleted domain rows can be
purged on a schedule without losing the historical record.

---

## 9. Sync & multi-device conventions

Baked in now so PWA/offline support later is additive, not a rework:

1. **UUID primary keys, client-generatable** — an offline device can create an
   inspection with its own id and sync it later without collision.
2. **Idempotent writes** — `PUT /inspections/{id}` upserts; a retried sync can't
   duplicate a report.
3. **`updated_at` on every row** (trigger-maintained) — enables "give me everything
   changed since T" delta sync and last-write-wins conflict resolution (sufficient for
   this domain).
4. **Soft deletes** (`deleted_at`) — deletions propagate to other devices as a synced
   state change, and the audit trail stays intact.
5. **Token auth (bearer + refresh), not cookie-only** — works identically for browsers,
   PWAs, and future native apps.

---

## 10. localStorage → backend mapping

| localStorage key (today) | Destination |
|---|---|
| `inspectit.account`, `inspectit.session` | Real auth: `users` + tokens (the current fake login is retired) |
| `inspectit.profile` | `companies` (company/report info) + `users` (personal info) |
| `inspectit.users` | `memberships` + `membership_roles` |
| `inspectit.vehicles` | `vehicles` |
| `inspectit.properties` | `properties` |
| `inspectit.inspections` (object keyed by vehicle id) | `inspections` (kind='vehicle') |
| `inspectit.propertyInspections` | `inspections` (kind='property') |
| `inspectit.tickets` / `inspectit.propertyTickets` | `repair_tickets` |
| `inspectit.vehicleMaintenance` / `propertyMaintenance` | `maintenance_state` |
| `inspectit.vehicleMaintTemplates` / `propertyMaintTemplates` | `maintenance_schedules` |
| `inspectit.vehicleMaintSpend` / `propertyMaintSpend` | `maintenance_spend` |
| `inspectit.vehicleWarranties` / `propertyWarranties` | `warranties` |
| `inspectit.projects` | `projects` + `project_estimates` + `project_payments` |
| `inspectit.projectMeta` | `option_lists` |
| `inspectit.diagram.{auto\|van\|comm}` | `files` (kind='image') referenced from company settings |
| Base64 attachments/photos/signatures embedded anywhere | `files` + object storage |

**Migration path:** the app's existing **Export backup JSON already contains every one
of these keys**. A one-time `POST /import/backup` endpoint ingests that JSON into a
fresh company: creates entities first (keeping a temp map of old ids → new UUIDs),
rewrites cross-references, decodes each base64 data-URL and uploads it to object storage
as a `files` row. Zero data loss, and existing users upgrade with the button they
already have.

---

## 11. API surface (sketch)

Vendor-neutral outline; on Supabase much of this is auto-generated + RLS policies, on
DigitalOcean it's a small custom API (Node/Fastify or Python/FastAPI on App Platform).

```
POST   /auth/login | /auth/refresh | /auth/forgot | /auth/reset
GET    /me                                  → user, memberships, roles, permissions
POST   /companies                           → create company (signup)
POST   /companies/{id}/invitations          → invite member (admin)
POST   /invitations/accept                  → join via token

CRUD   /vehicles /properties /projects
CRUD   /inspections /repair-tickets /warranties
CRUD   /maintenance/schedules /maintenance/state /maintenance/spend
CRUD   /assignments                         → requires 'assign' on the module
GET    /reports/spend?scope=...&from=&to=   → server-computed R&M report data
POST   /files/sign-upload                   → signed URL; client uploads direct to storage
GET    /files/{id}/url                      → short-lived signed download URL
POST   /import/backup                       → one-time localStorage-export ingestion
GET    /audit?subject=...                   → admin/manager only
```

Every endpoint enforces, in order: (1) valid token → user; (2) membership in the
company; (3) union-of-roles permission for module+action; (4) scope check via
`assignments` when every granting role is `scope='assigned'`.

---

## 12. Deliberately deferred (designed-for, not built in v1)

- **Billing/subscriptions** (Stripe): plan limits map to countable things this schema
  already has — members per company, storage bytes, entity counts. Add `plans` +
  `subscriptions` tables when real companies are ready to pay.
- **Push notifications** ("inspection assigned", "warranty expiring"): needs a
  `devices`/`push_subscriptions` table + a notifier worker. The assignment and
  warranty-expiry data it would notify about is already queryable.
- **Offline sync engine** (client side): the server conventions (§9) are in v1; the
  client queue/merge logic comes with the PWA phase.
- **Custom roles UI**: schema supports it day one (company-owned `roles` rows); the
  admin screen to edit the matrix can come later — presets cover launch.

---

## 13. Decisions log

| Decision | Answer | Date |
|---|---|---|
| **Vendor/platform** | **DigitalOcean** (App Platform + Managed Postgres + Spaces + DO DNS; custom API owns auth) | 2026-07-14 |
| **Phase-2 sync architecture** | Collection-level sync (`app_collections`: one versioned JSONB doc per app data key), NOT per-record CRUD — the app keeps localStorage as its synchronous cache and mirrors keys to the API (pull on open, debounced push, 409 → server wins). Per-record CRUD + true assigned-scope access arrive with the responsive UI rewrite. | 2026-07-14 |
| Inspector data scope | Assigned-only (roles carry `scope='assigned'`) | 2026-07-14 |
| Project Manager scope | Assigned projects only | 2026-07-14 |
| Property Manager ↔ projects | Read-only `projects: view` | 2026-07-14 |
| Document flavor | Vendor-neutral Postgres | 2026-07-14 |
| Multiple roles per user | Yes — union of permissions | 2026-07-14 (recommended, standing) |
| Delete for mid-tier managers/inspectors | No — escalate to Manager/Admin (preset default, adjustable) | 2026-07-14 (recommended) |
| Create vs edit | Separate actions; presets grant both together | 2026-07-14 (recommended) |
| Viewer scope | Company-wide read-only + print | 2026-07-14 (recommended) |
| Payments in ranged reports | Add `paid_date`, filter by it | 2026-07-14 (recommended) |

Open items to confirm when convenient (none block building):
1. Company signup flow — self-serve "create a company" vs. you provisioning companies
   manually at first.
2. Whether Vehicle/Property Managers are truly company-wide over their domain, or
   should also be assignable to a subset (e.g. a regional property manager). Schema
   already supports it via `scope='assigned'` + `duty='manage'` if wanted later.
3. Retention: how long soft-deleted rows are kept before purge (suggest 90 days).
