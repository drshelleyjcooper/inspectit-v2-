# CLAUDE.md

## What this is

The multi-tenant backend API for **Inspectit.app** (Brandon's vehicle/property
inspection web app). Python 3.9 + FastAPI + PostgreSQL. Phase 1 (auth,
tenancy, roles/permissions, assignments, backup import) is built and tested.

Authoritative design doc: `BACKEND-SCHEMA.md` in the **app's** folder at
`~/Desktop/inspect it /vehicle-inspection-app/` (note the trailing space in
`inspect it `). Read it before schema changes; log decisions in its §13.
The frontend app is a single-file HTML app in that same folder — still
localStorage-based until phase 2 (data-layer swap).

## Environment constraints (this Mac)

- **System Python 3.9 only** (`/usr/bin/python3`), no Homebrew, no Node, no
  Docker, no system Postgres. Use the project venv: `.venv/bin/python`.
- No `X | None` type syntax (3.9) — use `typing.Optional`.
- Local Postgres = **embedded via `pgserver` pip package** (real PG 16,
  data dir `./.pgdata`). `pgcrypto` extension is NOT available — use built-in
  `gen_random_uuid()`.
- This folder deliberately lives at `~/inspectit-backend`, NOT on Desktop —
  iCloud "Optimize Mac Storage" offloads/deletes Desktop folders.

## Run / test

```bash
.venv/bin/python run_dev.py               # dev API on :8100 (+ /docs)
.venv/bin/python -m pytest tests/ -q      # e2e suite (fresh throwaway DB)
```

Startup auto-runs `migrations/*.sql` (tracked in `schema_migrations`) and
seeds the 8 role presets (idempotent). **Never edit an applied migration —
add a new numbered file.**

## Architecture rules

- Every domain row has `company_id`; every endpoint chain is: token → user →
  membership → union-of-roles permission (module, action) → scope check.
  Use `Depends(require(module, action))`; if `ctx.grant_scope(...) ==
  "assigned"`, the endpoint MUST filter by `ctx.visible_subject_ids(...)`.
- Permission matrix + presets live in `api/presets.py`; module names are the
  single source of truth (also mapped in `permissions.MODULE_SUBJECT` and
  `assignments.SUBJECT_MODULE` — keep in sync).
- Files: binaries never go in the DB. `storage.store_file()` writes bytes
  (local disk dev / DO Spaces prod) + a `files` row.
- Soft deletes (`deleted_at`) everywhere; audit writes via `db.audit()` on
  create/delete/assign/export-ish actions.
- The importer (`routers/importer.py`) must stay lenient — the app's export
  shapes evolved over months; guard every field access, skip+report unknowns.

## Deployment (decided: DigitalOcean)

App Platform (API + static app) + Managed Postgres + Spaces. See README
"Deploying" section. `JWT_SECRET` required in prod; `DEV_MODE` never in prod.

## Roadmap

Phase 2: full CRUD endpoints + swap the app's `get*/set*` localStorage helpers
to async API calls. Phase 3: responsive/touch UI pass. Phase 4: PWA/offline.
Deferred: email delivery (invites/reset tokens are currently returned in API
responses), signed upload/download endpoints, spend reports, billing.
