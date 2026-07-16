# Inspectit v2

Multi-tenant SaaS platform for vehicle and property inspections, maintenance
scheduling, repair tracking, and project management. The frontend is a single
self-contained HTML file; the backend is a Python/FastAPI API backed by
PostgreSQL.

**Deployment target:** DigitalOcean — App Platform (API + static app), Managed
PostgreSQL, Spaces for file storage.

---

## Quick start

```bash
# one-time setup
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# run the API (boots embedded Postgres on first launch)
.venv/bin/python run_dev.py
```

The API starts on **http://127.0.0.1:8100**. First run creates an embedded
PostgreSQL 16 database (via `pgserver`, data in `.pgdata/`), applies all
migrations, and seeds the 8 built-in role presets.

Interactive API docs: http://127.0.0.1:8100/docs

Or double-click **`Run Inspectit API.command`** — it activates the virtualenv,
launches the server, and opens `/docs` in your browser.

## Run the frontend

Open **`web/inspectit-app.html`** in any modern browser. No build step, no
server required for local use. Cloud sync connects to the API when configured
(sign in via the "Cloud sync" button on the Home screen).

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

34 end-to-end tests against a fresh throwaway database (`inspectit_test`):
auth flows, role permissions, scoped data access, backup import/export,
collection sync, and production hardening (rate limiting, body size limits,
CORS, audit logging).

---

## Project layout

```
inspectit-v2/
│
├── web/                              # FRONTEND
│   ├── inspectit-app.html            # The live app (single-file, ~1.9 MB)
│   ├── BACKEND-SCHEMA.md             # Multi-tenant schema design blueprint
│   ├── inspectit-app.md              # App changelog and documentation
│   ├── vehicle-inspection-app.md     # Inspection module changelog
│   ├── READ ME FIRST.txt             # End-user instructions for updating the app
│   └── chat.md                       # Original design conversation transcript
│
├── api/                              # BACKEND
│   ├── main.py                       # FastAPI app entry point; lifespan runs
│   │                                 #   migrations → seed presets → cleanup
│   ├── config.py                     # Env-driven configuration (see table below)
│   ├── db.py                         # Connection pool, migration runner,
│   │                                 #   audit helper, expired-row cleanup
│   ├── security.py                   # bcrypt passwords, JWT access/refresh
│   │                                 #   tokens, URL-safe reset tokens
│   ├── presets.py                    # 8 role presets with full permission matrix
│   ├── permissions.py                # Auth chain: token → membership → role
│   │                                 #   union → scope (assigned vs company)
│   ├── storage.py                    # File storage: local disk (dev) or
│   │                                 #   DigitalOcean Spaces (prod, via boto3)
│   ├── ratelimit.py                  # Per-IP sliding-window rate limiter
│   ├── bodylimit.py                  # 75 MB global request-body cap (ASGI)
│   ├── requestmeta.py                # ContextVar middleware for IP + user-agent
│   ├── accesslog.py                  # JSON-lines access log (skips /health)
│   │
│   └── routers/
│       ├── auth.py                   # signup, login, refresh, forgot/reset,
│       │                             #   invitation accept (all rate-limited)
│       ├── me.py                     # GET /me — identity, memberships, permissions
│       ├── members.py                # roles list, members list, invitations
│       │                             #   (create/revoke/list), audit trail
│       ├── entities.py               # scoped CRUD lists: vehicles, properties,
│       │                             #   projects, inspections, tickets, etc.
│       ├── assignments.py            # assign users to vehicles/properties/projects
│       ├── importer.py               # POST import/backup — ingests the app's
│       │                             #   Export JSON (idempotency guard)
│       └── collections.py            # collection-level sync: GET/PUT per key
│                                     #   with optimistic concurrency (409 on conflict)
│
├── migrations/                       # SCHEMA
│   ├── 001_initial.sql               # 24 tables: companies, users, roles,
│   │                                 #   memberships, vehicles, properties,
│   │                                 #   inspections, tickets, maintenance,
│   │                                 #   warranties, projects, files, audit_log…
│   ├── 002_app_collections.sql       # Collection-level sync table (JSONB per key)
│   └── 003_audit_meta_file_kind.sql  # Audit IP/user-agent columns, 'bin' file kind
│
├── tests/                            # TEST SUITE
│   ├── conftest.py                   # Test DB setup (embedded pgserver)
│   ├── test_phase1.py                # 10 tests: auth → roles → import → scoping
│   ├── test_collections.py           # 6 tests: sync endpoints + conflict handling
│   └── test_zz_hardening.py          # 19 tests: all 13 hardening fixes (F1–F13)
│
├── run_dev.py                        # Local dev server launcher (port 8100)
├── Run Inspectit API.command         # Double-click launcher (macOS)
├── requirements.txt                  # Python dependencies
├── BACKEND-ANALYSIS.md               # Build review (13 findings) + DO runbook
├── CLAUDE.md                         # Conventions for Claude Code sessions
└── .gitignore                        # Excludes .venv, .pgdata, .filestore, etc.
```

---

## Workflows

### Authentication flow

1. **Signup** (`POST /auth/signup`) — creates company + user + admin membership;
   returns access token (30 min) + refresh token (30 days)
2. **Login** (`POST /auth/login`) — verifies credentials, sweeps expired tokens,
   returns token pair
3. **Refresh** (`POST /auth/refresh`) — rotates the refresh token (old one is
   invalidated); returns a new token pair
4. **Password reset** — `POST /auth/forgot` creates a reset token (returned in
   dev mode, emailed in prod); `POST /auth/reset` consumes it
5. **Invitation** — admin creates invite (`POST /companies/{id}/invitations`);
   recipient accepts (`POST /auth/invitations/accept`) with the token; admin can
   revoke anytime (`DELETE /companies/{id}/invitations/{id}`)

### Permission model

- **8 built-in roles:** Company Administrator, Manager, Vehicle Manager, Property
  Manager, Project Manager, Vehicle Inspector, Property Inspector, Viewer
- **11 modules** (vehicles, properties, projects, inspections, repairs,
  maintenance, warranties, estimates, payments, option_lists, company) **× 7
  actions** (view, create, edit, delete, print, assign, admin)
- Users can hold **multiple roles** per company; effective permissions = union
- **Scoping:** company-scope roles see all data; assigned-scope roles see only
  subjects they're assigned to (via the `assignments` table)

### Cloud sync (collection-level)

1. App boots → `cloudSyncNow(isInitial=true)` pulls all collections from server,
   merges into localStorage
2. User edits data → `store.set()` marks the key dirty → debounced 1.2 s flush
   pushes changed collections to `PUT /companies/{id}/collections/{key}`
3. Conflict → server returns **409** with its copy → app accepts server version,
   toasts the user, re-renders
4. Token expired → transparent refresh on 401, then retry
5. Offline → retry timer (30 s) + `online` event listener

### Backup import

`POST /companies/{id}/import/backup` ingests the app's full Export JSON
(vehicles, inspections, tickets, maintenance, properties, projects, templates,
option lists, files with base64 extraction). Idempotency guard prevents
double-import (409 unless `?force=true`).

### Startup sequence

1. Embedded Postgres boots (dev) or connects to managed DB (prod)
2. `run_migrations()` applies any pending `migrations/*.sql` under an advisory
   lock (safe for multiple instances)
3. `seed_role_presets()` upserts the 8 built-in roles (idempotent)
4. `cleanup_expired()` purges dead refresh tokens, reset tokens, and flips
   overdue invitations to `expired`

### Middleware stack (inner → outer)

1. **RequestMetaMiddleware** — populates IP + user-agent ContextVar for audit
2. **BodySizeLimitMiddleware** — rejects POST/PUT/PATCH over 75 MB (413) and
   chunked-without-Content-Length (411)
3. **CORSMiddleware** — wraps body-limit so error responses carry CORS headers
4. **AccessLogMiddleware** — JSON-lines to stdout (skips `/health`)

---

## Configuration (environment variables)

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | *(embedded pgserver)* | Set to the managed Postgres URL in prod |
| `APP_ENV` | `development` | `production` refuses boot without JWT_SECRET, with DEV_MODE, or with wildcard CORS |
| `JWT_SECRET` | dev: auto-generated `.jwt_secret` | **Required in production** |
| `DEV_MODE` | off | `1` returns reset/invite tokens in responses (never in prod) |
| `ALLOWED_ORIGINS` | `*` (dev) | Comma-separated CORS origins; **required** in production |
| `AUTH_RATE_LIMIT` / `AUTH_RATE_WINDOW_S` | 10 / 60 | Per-IP sliding window for `/auth/*` routes |
| `MAX_BODY_MB` | 75 | Global request-body ceiling (413 above it) |
| `POOL_MIN` / `POOL_MAX` | 1 / 10 (5 in prod) | Database connection pool bounds |
| `STORAGE_BACKEND` | `local` | `s3` for DigitalOcean Spaces |
| `STORAGE_DIR` | `./.filestore` | Local backend only |
| `SPACES_REGION` / `BUCKET` / `KEY` / `SECRET` | — | Required when `STORAGE_BACKEND=s3` |

---

## Deploying to DigitalOcean

See `BACKEND-ANALYSIS.md` §6 for the full runbook with app spec YAML. Summary:

1. **Managed PostgreSQL** — create cluster; set `DATABASE_URL`
2. **Spaces** — create bucket; set `STORAGE_BACKEND=s3` + `SPACES_*` vars
3. **App Platform (API)** — run command:
   `uvicorn api.main:app --host 0.0.0.0 --port $PORT --no-access-log`;
   set `APP_ENV=production`, `JWT_SECRET`, `ALLOWED_ORIGINS`
4. **App Platform (static site)** — serve `web/inspectit-app.html`
5. **DNS** — point your domain at App Platform (DO DNS is free, auto-TLS)

Migrations and role seeding run automatically on each deploy.

---

## Not yet built

- Email delivery (invites/resets are returned in dev mode for now)
- Per-record CRUD API (assigned-scope inspectors will use this instead of
  collection sync)
- Signed upload/download URLs for files
- Spend-report endpoints
- Platform admin console
- Billing / subscription management
- Responsive / mobile pass (the app is desktop-shaped today)
- PWA / offline-first wrapper
