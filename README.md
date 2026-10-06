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

## Seed a test company (one member per role)

For the manual role pass (USER-ROLES-SPEC §9.4). With the API running:

```bash
.venv/bin/python seed_roles.py                  # new company, 13 sign-ins, prints a table
.venv/bin/python seed_roles.py --viewer-grants  # also sets can_grant_viewers on the domain managers
.venv/bin/python seed_roles.py --json           # machine-readable
```

Every run creates a fresh company (name and emails carry a run tag), so it is
safe to repeat. It drives the real signup → invite → accept path over HTTP and
needs `DEV_MODE=1` (the `run_dev.py` default) so invite tokens come back in the
response. `/auth/*` is rate-limited to 10/min per IP by default and the script
makes 13 auth calls, so expect one pause of up to a minute; set
`AUTH_RATE_LIMIT=100` in the environment before launching the API to skip it.

## Turn an existing company into a demo model

`seed_demo_members.py` signs in as a company's administrator on any server —
including the live one — optionally renames the company, then invites and
accepts one dummy member per preset role that has nobody yet. Re-running is
safe: roles that already have a member are skipped.

```bash
.venv/bin/python seed_demo_members.py --api https://inspectit.app \
    --admin you@company.com --rename "Cooper Test" --domain coopertest.example
```

It prompts for the administrator's password and for the password to give every
dummy member; nothing is stored. Dummy addresses are `<role>@<domain>` — no
mail is sent, so the domain never needs to exist. The company name can also be
changed from the app: Cloud sync → Company name → Rename (Company Administrator
and Manager only; it calls `PATCH /companies/{id}`).

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
4. **Password reset** — `POST /auth/forgot` creates a reset token and emails
   the link to the account (`/forgot-password` and `/reset-password` pages; token
   also returned in dev mode); `POST /auth/reset` consumes it and emails a
   'password changed' notice
5. **Invitation** — admin creates invite (`POST /companies/{id}/invitations`);
   the invitee is emailed a link (`/web/inspectit-app.html?invite=<token>`, Reply-To the
   inviter; the token is also returned to the admin); recipient accepts
   (`POST /auth/invitations/accept`) with the token; admin can
   revoke anytime (`DELETE /companies/{id}/invitations/{id}`)

### Admin-created accounts

In the admin portal, **Create user** has "Email them a link to set their own
password" (on by default): the backend sets a random password nobody sees, emails
a welcome with a one-time link (`/reset-password?token=...&welcome=1`, valid 7 days),
and shows no password. If the email can't be sent the one-time link is returned so
the operator can pass it on. **Email reset link** on a user sends a normal reset
link without changing anything until the person uses it. Unticking the box (or
**Set password...**) keeps the old hand-over-a-password flow.

### Free-trial lifecycle

`POST /auth/trial-signup` starts a 30-day trial (`companies.trial_ends_at`, T).
A background check (hourly, exactly-once across workers) walks each company
through this timeline, emailing its administrators and managers at each step:

| When | What happens |
|---|---|
| T - 7 days | Reminder: trial ends soon |
| T | Trial ended. **7 days' grace**: full access continues |
| T + 7 days | Account **paused**: every `/companies/{id}/...` route answers `402`. Records are kept 30 days (`access` = `suspended`) |
| T + 30 days | Final warning: records will be deleted in 7 days |
| T + 37 days | Records removed (soft delete) **only if `RETENTION_PURGE_ENABLED=1`** and the warning went out at least 7 days earlier (`access` = `expired` until then) |

Login and `/me` keep working while paused; `/me` reports `access`
(`none`/`trialing`/`grace`/`subscribed`/`suspended`/`expired`). Platform admins
lift a pause with `PATCH /admin/companies/{id}/subscription {"active": true}` (a
stand-in for a payment webhook until billing exists) or push the date with
`POST .../extend-trial` (re-arms every email). Companies with no trial on record
(everything created before 2026-10) are never paused, and neither are
**complimentary** companies: test/comped accounts a platform admin creates (tick
"Complimentary" when creating a user with a new company) or toggles on the
Companies tab (`PATCH /admin/companies/{id}/complimentary`). They have no trial,
no paywall, no trial emails and are skipped by the removal step. The removal step is OFF by
default and only soft-deletes (`deleted_at`); hard-purging rows and stored files
is not implemented. The app keeps a local copy of its data, so pausing blocks the
cloud, not what is already on a user's device.

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
| `RESEND_API_KEY` | *(unset)* | **Secret.** If set, email goes through Resend's HTTPS API (preferred; no mail ports) and the SMTP vars below are ignored |
| `SMTP_PASSWORD` | *(unset = email off)* | Password for the sending mailbox. **Secret**: env only. Enables demo-request and new-trial notifications |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` | `smtp.office365.com` / 587 / `info@inspectit.app` | Microsoft 365 mailbox (via GoDaddy). 587 = STARTTLS; 465 = SSL (GoDaddy Workspace Email: `smtpout.secureserver.net`) |
| `MAIL_TO` / `MAIL_FROM` / `MAIL_FROM_NAME` | `info@inspectit.app` / same / `Inspectit.app` | Where notifications go, and the From header |
| `DEMO_RATE_LIMIT` / `DEMO_RATE_WINDOW_S` | 5 / 3600 | Per-IP budget for `POST /public/demo-requests` |
| `APP_BASE_URL` | `https://inspectit.app` (prod) | Base of links inside emails (reset links) and calendar subscription links. Never taken from the Host header |
| `CALENDAR_SECRET` | `JWT_SECRET` | **Secret.** Signs private calendar links (`/cal/<token>.ics`). Changing it invalidates every calendar link |
| `CALENDAR_CACHE_S` | `900` | How long a built calendar feed is reused before it's rebuilt from synced data |
| `CALENDAR_RATE_LIMIT` / `CALENDAR_RATE_WINDOW_S` | `60` / `3600` | Max fetches of one calendar link per window |
| `INVITE_EMAIL_LIMIT` / `INVITE_EMAIL_WINDOW_S` | 20 / 3600 | Invitation emails per signed-in user per window; over it the invite is still created, only the email is skipped |
| `TRIAL_REMINDER_DAYS` / `TRIAL_CHECK_INTERVAL_S` | 7 / 3600 | Trial-end reminder lead time; how often the background check runs (0 = off). Needs email configured |
| `ADMIN_LINK_TTL_MIN` | 10080 (7 days) | How long a set-password link emailed from the admin portal stays valid |
| `TRIAL_GRACE_DAYS` / `DATA_RETENTION_DAYS` / `RETENTION_WARNING_DAYS` | 7 / 30 / 7 | Grace after the trial, how long records are kept once paused, and how much notice before deletion |
| `RETENTION_PURGE_ENABLED` | off | `1` lets the check remove (soft-delete) companies whose retention is over. Leave off until you want it |
| `TRIAL_DAYS` | 30 | Length of the trial recorded by `POST /auth/trial-signup` |

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
