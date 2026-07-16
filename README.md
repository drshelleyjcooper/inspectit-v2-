# Inspectit Backend

Multi-tenant API for Inspectit.app — companies, users, roles/permissions, and
all vehicle/property/project data, replacing the single-device localStorage
model. Design blueprint: `BACKEND-SCHEMA.md` (kept in the app folder:
`~/Desktop/inspect it /vehicle-inspection-app/`).

**Deployment target: DigitalOcean** — App Platform (this API + the static app),
Managed PostgreSQL, Spaces for file storage.

## Run locally (no setup beyond Python needed)

```bash
.venv/bin/python run_dev.py        # API on http://127.0.0.1:8100
```

First run boots an embedded PostgreSQL 16 (via the `pgserver` package, data in
`./.pgdata`), applies `migrations/*.sql`, and seeds the 8 built-in role
presets. Interactive API docs: http://127.0.0.1:8100/docs

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

End-to-end suite against a fresh throwaway database (`inspectit_test`):
signup → login → roles → backup import (with base64 → file extraction) →
invitations → assigned-only scoping → permission denials → password reset →
refresh-token rotation.

## Layout

```
migrations/           numbered SQL migrations (001 = full schema)
api/
  main.py             FastAPI app; startup = migrate + seed presets
  config.py           env-driven config (see below)
  db.py               connection pool + migration runner + audit helper
  security.py         bcrypt passwords, JWT access/refresh, reset tokens
  presets.py          permission matrix + the 8 role presets
  permissions.py      auth dependencies: user -> membership -> permission -> scope
  storage.py          file binaries: local disk (dev) / DO Spaces (prod)
  routers/
    auth.py           signup, login, refresh, forgot/reset, invitation accept
    me.py             GET /me (memberships + effective permissions)
    members.py        roles list, members list, invitations
    entities.py       scoped list endpoints (vehicles/properties/projects)
    assignments.py    assign users to vehicles/properties/projects
    importer.py       POST import/backup — ingests the app's Export JSON
tests/                pytest end-to-end suite
run_dev.py            local dev server
```

## Configuration (environment variables)

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | *(embedded pgserver)* | Set to the DO Managed Postgres URL in prod |
| `APP_ENV` | `development` | `production` refuses unsafe settings (missing JWT_SECRET, DEV_MODE, wildcard origins) |
| `ALLOWED_ORIGINS` | `*` (dev) | Comma-separated CORS origins; **required** in production |
| `AUTH_RATE_LIMIT` / `AUTH_RATE_WINDOW_S` | 10 / 60 | Per-IP budget for /auth/* routes |
| `MAX_BODY_MB` | 75 | Global request-body ceiling (413 above it) |
| `POOL_MIN` / `POOL_MAX` | 1 / 10 (5 in prod) | Database connection pool bounds |
| `JWT_SECRET` | dev: auto-generated `.jwt_secret` | **Required in prod** |
| `DEV_MODE` | off | `1` returns reset tokens in responses (never in prod) |
| `STORAGE_BACKEND` | `local` | `s3` for DigitalOcean Spaces |
| `STORAGE_DIR` | `./.filestore` | local backend only |
| `SPACES_REGION/BUCKET/KEY/SECRET` | — | s3 backend (requires `boto3`) |

## Deploying to DigitalOcean (phase-1 endpoint)

1. Create a Managed PostgreSQL cluster; set `DATABASE_URL`.
2. Create a Space; set `STORAGE_BACKEND=s3` + `SPACES_*`; add `boto3` to
   requirements.
3. App Platform app from this repo: run command
   `uvicorn api.main:app --host 0.0.0.0 --port $PORT`, set `JWT_SECRET`.
4. Tighten CORS `allow_origins` in `api/main.py` to the app's real domain.
5. Migrations + preset seeding run automatically on startup.

## Not yet built (later phases)

Email delivery (invites/resets are returned to the caller for now), full CRUD
for domain records (phase 2, alongside the app's data-layer swap), signed
upload/download URL endpoints, spend-report endpoints, billing.
