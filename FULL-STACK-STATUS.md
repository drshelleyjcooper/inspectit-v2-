# Inspectit — Full-Stack Build Status

**Last updated:** 2026-07-22

Inspectit is a multi-tenant SaaS platform for vehicle and property inspection
management. It consists of a self-contained single-file frontend app, a
Python/FastAPI backend API, a PostgreSQL database, and a marketing landing page.

---

## Frontend: The App (`web/inspectit-app.html`)

**Single-file HTML application — 5,334 lines / 1.9 MB**

A fully offline-capable inspection management app that runs entirely in one
HTML file. No build step, no framework, no dependencies. All CSS, JavaScript,
and assets (logos, icons) are inlined or embedded as base64 data URIs.

### What's built

- **Vehicle Management** — add, edit, delete, search, and filter vehicles with
  full detail records (VIN, year, make, model, mileage, license, status)
- **Property Management** — property records with address, type, unit count,
  square footage, and status tracking
- **Vehicle Inspections** — multi-section inspection forms with pass/fail/N-A
  checkboxes, photo attachments (camera capture or file upload stored as base64),
  severity ratings, notes, and mileage logging
- **Property Inspections** — room-by-room inspection forms with condition
  ratings, photo documentation, and notes
- **Repair Tickets** — create from failed inspection items, track status
  (open → in-progress → completed), assign to technicians, log costs and parts
- **Maintenance Schedules** — recurring service intervals (mileage-based or
  date-based), upcoming/overdue tracking, spend logging
- **Warranty Tracking** — warranty records with provider, coverage dates, terms,
  and claim history
- **Projects Section** — per-property job/dossier tracker with estimates,
  payments, contractor management, permit tracking, scope of work, and
  milestone timelines (built 2026-06-30)
- **Dashboard** — summary cards with counts, status breakdowns, upcoming
  maintenance, recent activity feed
- **Multi-company Support** — switch between companies from the UI; each
  company's data is namespaced in localStorage
- **User Authentication** — login/signup screens connected to the backend API
  (JWT access + refresh tokens)
- **Data Export/Import** — full JSON backup export of all localStorage data;
  import from backup file
- **Cloud Sync** — bi-directional sync bridge to the backend API; pulls
  collections on boot, pushes changes on save; 409 conflict detection with
  server-wins resolution
- **Print/PDF** — print-optimized stylesheets for inspection reports, repair
  tickets, and vehicle/property detail views
- **Search & Filtering** — global search across all entity types; per-list
  filters for status, date range, assignment
- **Responsive Design** — works on desktop, tablet, and phone; touch-friendly
  controls
- **Offline-first** — all data lives in localStorage; the app works without
  network; sync happens when connectivity is available

### Data storage

- **Primary:** browser `localStorage` keyed by `inspectit.<collection>`
- **Sync target:** backend `/collections` endpoints (pull on boot, push on change)
- **Photo storage:** base64 data URIs in localStorage (large photos are the
  main storage bottleneck; cloud file upload planned)

---

## Frontend: Marketing Landing Page (`index.html`)

**Single-file HTML — 782 lines / 277 KB**

Apple-style marketing page with scroll-linked animations. Self-contained: all
CSS, JS, and logos embedded inline (no external requests except Google Fonts).

### Sections

1. **Hero** (230vh sticky) — parallax phone mockup with scroll-driven opacity
   and translation; tagline and CTA buttons
2. **Statement** — IntersectionObserver reveal animation; value proposition copy
3. **Sweep** (420vh sticky) — animated inspection report with 9 checklist rows
   that transition through OK/attention/defect states; live tally counter;
   verdict badge (Pass → Needs Attention → Deficient); repair ticket slide-in
4. **Features** — 6 cards: Inspections, Repair Tracking, Maintenance,
   Warranties, Multi-Property, Team Management
5. **Partners** — 3 testimonial quotes from fleet/property/contractor personas
6. **FAQ** — 17 expandable items (7 general + 10 contractor-specific)
7. **Footer** — app store links (placeholder), legal links, company info

### Design tokens

- **Colors:** `--blue: #1E64D3`, `--navy: #0B2138`, `--ok: #2E7D32`,
  `--attn: #EF6C00`, `--defect: #C62828`
- **Fonts:** Archivo (headings), Inter Tight (body), IBM Plex Mono (data)
- **Features:** mobile nav drawer (hamburger below 900px), language switcher
  (10 languages, stub handler), `prefers-reduced-motion` support, OG/Twitter
  meta tags, favicon

---

## Backend API (`api/`)

**Python / FastAPI — 2,536 lines across 20 files**

Multi-tenant REST API with JWT authentication, role-based access control,
automatic database migrations, and dual storage backends.

### Core modules

| File | Lines | Purpose |
|------|-------|---------|
| `config.py` | 92 | Environment-driven settings; production safety gate refuses boot without JWT_SECRET or with wildcard CORS |
| `db.py` | 111 | Connection pool (psycopg-pool), SQL migration runner with advisory-lock serialization, expired-token cleanup |
| `main.py` | 69 | FastAPI app with lifespan (migrations + seed + cleanup), middleware stack (CORS, body limit, access log, request meta) |
| `security.py` | 88 | JWT encode/decode (access + refresh tokens), bcrypt password hashing |
| `permissions.py` | 108 | Auth dependency chain: token → user → membership → role union → scope check; assignment-based filtering for scoped roles |
| `presets.py` | 99 | 8 built-in role presets with permission matrices; idempotent seeder |
| `storage.py` | 104 | File binary storage — local disk (dev) or DigitalOcean Spaces/S3 (prod); data-URL parser for base64 uploads |
| `ratelimit.py` | 62 | Per-IP rate limiting on auth routes (configurable window + max) |
| `bodylimit.py` | 43 | Request body size cap (75 MB default) |
| `accesslog.py` | 45 | Structured access logging middleware (method, path, status, duration) |
| `requestmeta.py` | 33 | Context-var middleware to thread client IP and user-agent into audit logging |

### API endpoints (7 routers, 22 endpoints)

**Auth** (`/auth`) — rate-limited per IP
| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/signup` | Create company + admin user; returns JWT pair |
| POST | `/auth/login` | Email/password login; returns access + refresh tokens |
| POST | `/auth/refresh` | Exchange refresh token for new access token |
| POST | `/auth/forgot` | Request password reset (token returned in dev mode) |
| POST | `/auth/reset` | Consume reset token + set new password |
| POST | `/auth/invitations/accept` | Accept invite; create user + membership |

**Me** (`/me`)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/me` | Current user profile + list of company memberships |

**Members** (`/companies/{id}/...`)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/roles` | List roles (presets + company custom) |
| GET | `/members` | List company members with roles |
| POST | `/invitations` | Invite user by email with role assignment |
| DELETE | `/invitations/{id}` | Cancel pending invitation |
| GET | `/invitations` | List pending invitations |
| GET | `/audit` | Paginated audit log (filterable by action, user, date) |

**Entities** (`/companies/{id}/...`)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/vehicles` | List vehicles (with assignment-scope filtering) |
| GET | `/properties` | List properties (with assignment-scope filtering) |
| GET | `/projects` | List projects (with assignment-scope filtering) |

**Assignments** (`/companies/{id}/...`)
| Method | Path | Description |
|--------|------|-------------|
| POST | `/assignments` | Assign user to vehicle/property/project |
| GET | `/assignments` | List assignments (filterable by user or subject) |
| DELETE | `/assignments/{id}` | Remove assignment |

**Collections** (`/companies/{id}/...`) — cloud sync for the app
| Method | Path | Description |
|--------|------|-------------|
| GET | `/collections` | List all synced collection keys with timestamps |
| GET | `/collections/{key}` | Get single collection blob |
| PUT | `/collections/{key}` | Upsert collection; 409 on timestamp conflict |

**Import** (`/companies/{id}/...`)
| Method | Path | Description |
|--------|------|-------------|
| POST | `/import/backup` | One-shot ingestion of the app's localStorage JSON export into normalized tables; runs in single transaction |

### Role-based access control

8 built-in role presets across 11 permission modules:

| Role | Scope | Access |
|------|-------|--------|
| Company Administrator | company | Full CRUD + user/role management on everything |
| Manager | company | Full CRUD on all entities; no company admin |
| Vehicle Manager | company | Full management of vehicle domain (no delete) |
| Property Manager | company | Full management of property domain + read-only projects |
| Project Manager | assigned | View/create/edit/print/export on assigned projects only |
| Vehicle Inspector | assigned | Inspections + view maintenance on assigned vehicles |
| Property Inspector | assigned | Inspections + view maintenance on assigned properties |
| Viewer | company | Read-only + print across all entities |

**Scope model:** `company` = sees everything in the company. `assigned` = sees
only entities they're assigned to (enforced via the assignments table).

### Middleware stack (outermost → innermost)

1. **AccessLogMiddleware** — logs every request with method, path, status, ms
2. **CORSMiddleware** — origin allowlist (wildcard blocked in production)
3. **BodySizeLimitMiddleware** — rejects bodies over 75 MB (configurable)
4. **RequestMetaMiddleware** — captures IP + user-agent for audit trail

---

## Database (PostgreSQL)

**3 migration files / 417 lines of SQL / 25 tables**

### Tables

**Tenancy & Identity (6)**
- `companies` — tenant root: name, address, phone, email, logo
- `users` — email + bcrypt hash, auth provider, profile photo
- `roles` — permission matrix (JSONB), scope (company|assigned), preset flag
- `memberships` — user ↔ company link with status (active/suspended/removed)
- `membership_roles` — many-to-many: membership ↔ role
- `invitations` — email invites with token, expiry, status lifecycle

**Auth & Security (2)**
- `refresh_tokens` — JWT refresh token tracking with family-based rotation
- `password_resets` — time-limited reset tokens

**Files (1)**
- `files` — metadata (company, uploader, mime, size, kind) + storage key pointing to local disk or S3

**Core Entities (3)**
- `vehicles` — VIN, year, make, model, mileage, license, status, photos
- `properties` — address, type, units, sqft, year built, status, photos
- `assignments` — links users to vehicles/properties/projects (drives scope filtering)

**Inspections (2)**
- `inspections` — header (entity, type, date, status, summary) + sections as JSONB
- `inspection_templates` — reusable inspection form definitions

**Maintenance & Repairs (5)**
- `repair_tickets` — status workflow, assignee, cost tracking, linked to inspections
- `maintenance_schedules` — interval-based (miles or days) recurring services
- `maintenance_state` — current state per entity per schedule (last done, next due)
- `maintenance_spend` — individual spend records with date, cost, vendor, notes
- `warranties` — provider, coverage period, terms, claim history

**Projects (3)**
- `projects` — per-property jobs: scope, status, dates, contractor, notes
- `project_estimates` — line-item estimates with amounts and status
- `project_payments` — payment records with method, reference, amount

**Configuration & Audit (2)**
- `option_lists` — company-customizable dropdowns (project types, contractor types, cost categories, etc.)
- `audit_log` — every write action with user, IP, user-agent, timestamp, details JSONB

**Cloud Sync (1)**
- `app_collections` — key-value store for the app's localStorage collections; used by the sync bridge

---

## Tests

**825 lines across 4 files**

| File | Lines | Coverage |
|------|-------|----------|
| `conftest.py` | 50 | Shared fixtures: test client, embedded PG, auth helpers |
| `test_phase1.py` | 299 | Auth flow (signup, login, refresh, reset), member management, invitation lifecycle, RBAC enforcement |
| `test_collections.py` | 118 | Collection sync CRUD, 409 conflict detection, permission checks |
| `test_zz_hardening.py` | 358 | Rate limiting, body size limits, UUID validation, SQL injection guards, JWT edge cases |

---

## Infrastructure & Deployment

### DigitalOcean App Platform

| Component | Type | Spec |
|-----------|------|------|
| `api` | Web Service | Python, basic-xxs ($5/mo), 2 uvicorn workers |
| `web` | Static Site | Serves `index.html` (landing) at root |
| `inspectit-db` | Managed PostgreSQL | Production, auto-bound as DATABASE_URL |

### Key files

| File | Purpose |
|------|---------|
| `.do/app.yaml` | App Platform spec (services, static sites, database, env vars) |
| `Procfile` | Start command for the Python buildpack |
| `requirements.txt` | Runtime dependencies (FastAPI, uvicorn, psycopg, bcrypt, PyJWT, boto3) |
| `requirements-dev.txt` | Dev additions (pgserver, pytest, httpx) |
| `.env.digitalocean` | Environment variable template for DO console import (not committed) |
| `run_dev.py` | Local dev launcher (boots embedded PG, runs uvicorn) |
| `Run Inspectit API.command` | macOS double-click launcher |

### Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `APP_ENV` | Yes (prod) | `production` enables safety gates |
| `JWT_SECRET` | Yes (prod) | Signing key for access/refresh tokens |
| `DATABASE_URL` | Yes (prod) | PostgreSQL connection string (auto-bound from managed DB) |
| `ALLOWED_ORIGINS` | Yes (prod) | Comma-separated CORS origins |
| `POOL_MAX` | No | Connection pool ceiling (default 5 in prod) |
| `STORAGE_BACKEND` | No | `local` (default) or `s3` |
| `SPACES_REGION` | If s3 | DO Spaces region (e.g. `nyc3`) |
| `SPACES_BUCKET` | If s3 | Bucket name |
| `SPACES_KEY` | If s3 | Spaces access key |
| `SPACES_SECRET` | If s3 | Spaces secret key |
| `DEV_MODE` | No | `1` returns reset tokens in API responses (never in prod) |

---

## What's Not Built Yet

These features are designed in the schema but don't have API endpoints or UI yet:

- **Per-record CRUD endpoints** — individual vehicle/property/inspection/ticket
  create/read/update/delete (currently the app syncs entire collections)
- **File upload API** — backend storage module is built (`storage.py`), but no
  upload/download endpoints exist; photos are still base64 in localStorage
- **Inspection templates API** — table exists, no routes
- **Custom roles UI** — schema supports company-specific roles; no management
  screen in the app
- **Email integration** — password reset tokens are returned in the API response
  (dev mode); no email provider wired up
- **Platform admin console** — super-admin dashboard for managing tenants,
  monitoring usage, viewing cross-company analytics
- **Notification system** — no push notifications or in-app alerts
- **Reporting & analytics** — no aggregated dashboards beyond the app's local
  summary cards
- **App store deployment** — landing page has placeholder store links; no native
  app wrapper (PWA or Capacitor) built yet

---

## Line counts summary

| Layer | Files | Lines |
|-------|-------|-------|
| Backend API | 20 | 2,536 |
| Database migrations | 3 | 417 |
| Tests | 4 | 825 |
| Frontend app | 1 | 5,334 |
| Landing page | 1 | 782 |
| **Total** | **29** | **9,894** |
