# Inspectit Backend — Build Analysis & DigitalOcean Integration Plan

**Date:** 2026-07-16 · **Analyzed at commit:** `35462d7` (phase 2)
**Verdict:** Solid foundation, correct multi-tenant/permission core, all 16 tests
passing. **Not yet production-ready** — 7 items must change before real users
touch a deployed instance (all small; listed in §4 and folded into the
deployment steps in §6).

---

## 1. What exists today (inventory)

| Area | State |
|---|---|
| Code size | ~2,100 lines (Python API + SQL migrations) |
| Endpoints | 17 paths / 22 operations (auth, me, roles, members, invitations, entities, assignments, import, collections sync, health) |
| Database | PostgreSQL 16; 2 migrations; 24 tables incl. full domain schema, files, audit_log, app_collections |
| Auth | bcrypt passwords; JWT access (30 min) + rotating refresh (30 d, server-tracked, revocable); single-use hashed reset tokens; invitations |
| Permissions | 8 role presets over an 11-module × 7-action matrix; union across roles; assigned-scope via assignments table |
| Sync | Collection-level (one versioned JSONB doc per app data key), optimistic concurrency (409 → server copy) |
| Tests | 16 end-to-end (signup→import→invite→scoping→denials→reset→rotation→sync→conflicts) — all passing |
| Local dev | Embedded PostgreSQL via `pgserver` (no installs); `run_dev.py`; Desktop launcher |
| Deployment | **None yet** — runs only on this Mac |

## 2. Strengths (worth keeping as-is)

1. **Tenancy is structural, not bolted on** — every table carries `company_id`;
   every request passes token → membership → permission → scope. The
   cross-company denial is tested.
2. **Roles are data, not code** — the User Roles matrix lives in seeded preset
   rows; per-company custom roles need zero code changes later.
3. **Auth hygiene is above-average for a v1**: refresh rotation with server-side
   revocation, hashed single-use reset tokens, no account-existence oracle on
   /forgot, password change revokes all sessions.
4. **Migrations + preset seeding are self-running and idempotent** — a fresh
   database (local or DO) initializes itself on first boot.
5. **The importer matches reality** — it was tested against the app's actual
   Export format (JSON-string values), lenient by design, all-or-nothing
   transactional.
6. **Sync conflicts are handled, not ignored** — base-timestamp check, 409
   returns the server copy, verified in a real browser including the
   wiped-device restore.

## 3. Architecture notes (deliberate trade-offs, documented)

- **Collection-level sync** (not per-record CRUD): right call for keeping the
  existing app intact, with two accepted consequences: (a) assigned-scope roles
  (inspectors) cannot use sync — they wait for the per-record API; (b) last
  write wins per collection — fine for 1–3 company-scope users, revisit before
  large teams hammer the same collection.
- **Attachments currently travel inside collection JSON** (base64), same as
  localStorage today. The `files` table + Spaces storage get real traffic only
  when per-record endpoints land. Practical ceiling: collections cap at 25 MB
  each; the app's own 5 MB localStorage ceiling binds first, so this is safe
  but unchanged from today — the storage win arrives in the next phase.
- **Embedded form gap (pre-existing):** saved inspection templates and diagram
  graphics are written by the embedded iframe directly to localStorage,
  bypassing the sync chokepoint — not synced, and never in Export backups
  either. Fix belongs in an app-side pass, not the backend.

## 4. Findings

Severity: **[H]** fix before deploy · **[M]** fix soon after · **[L]** note for later.

| # | Sev | Finding | Detail / consequence | Fix |
|---|---|---|---|---|
| F1 | ~~H~~ **FIXED 2026-07-16** | CORS is wide open (`allow_origins=["*"]`) | Origins now come from `ALLOWED_ORIGINS` env (dev default `*`); production refuses to boot with a wildcard | — |
| F2 | ~~H~~ **FIXED 2026-07-16** | JWT secret silently self-generates if unset | `APP_ENV=production` gate (`check_production_config`): boot fails without explicit `JWT_SECRET`, with `DEV_MODE=1`, or with wildcard/empty origins | — |
| F3 | ~~H~~ **FIXED 2026-07-16** | No rate limiting on `/auth/*` | Per-client-IP sliding-window limiter on every auth route (default 10/min, `AUTH_RATE_LIMIT`/`AUTH_RATE_WINDOW_S`); honors X-Forwarded-For behind the DO proxy; 429 + Retry-After | — |
| F4 | **H** | No request-body size limit | Uvicorn accepts unbounded bodies; a huge POST could exhaust memory/disk (import endpoint especially) | Content-Length middleware, reject > 50 MB |
| F5 | **H** | Startup migrations can race | Two App Platform instances booting simultaneously both run migrations | Wrap `run_migrations()` in `pg_advisory_lock` |
| F6 | **M** | Invitation token returned in the API response | Deliberate (no mailer), but any `company:assign` holder sees join tokens; combined with F-email gap it's the weakest auth link | Acceptable at launch (admin-only in practice); resolved when email delivery lands |
| F7 | **M** | Connection pool (max 10) vs DO basic Postgres (~22 conn limit) | Two API instances would flirt with the cap | Set pool max via env (default 5 in prod) and/or use DO's built-in connection pool (PgBouncer) |
| F8 | **M** | No pagination on list endpoints / audit query | Fine at current scale; unbounded responses at fleet scale | Add `limit/offset` when per-record API lands |
| F9 | **M** | Expired refresh-token / reset rows accumulate | Table growth, slow leak | Nightly delete job or opportunistic cleanup on login |
| F10 | **L** | Import endpoint is not idempotent | Importing the same backup twice duplicates records | Documented as one-time; per-company "already imported" guard is easy if needed |
| F11 | **L** | `files.kind` falls back to `pdf` for unknown MIME types | Cosmetic mislabeling only | Map unknowns to a `bin` kind later |
| F12 | **L** | Audit rows lack IP / user-agent | Less forensic value | Add columns when there's a real user base |
| F13 | **L** | No structured logging/metrics | App Platform captures stdout; enough for now | Add request logging middleware later |

**Bottom line:** F1–F5 are the pre-deploy gate. **F1–F3 are fixed and tested
(21-test suite green).** F4 (body-size limit) and F5 (migration lock) remain
for the pre-deploy session — Step 4 of the integration plan below.

---

## 5. What deployment does NOT require

- **No data migration from this Mac.** Your real data lives in the app's
  localStorage. After deploy, you sign in to Cloud sync pointed at the
  production URL and the initial sync pushes everything up. (Belt-and-braces:
  click Export backup first, as always.) The Mac's dev database holds only
  test data and stays local.
- **No changes to the schema or tests.** The same migrations self-apply to
  DO Managed Postgres on first boot.

---

## 6. DigitalOcean integration — detailed steps

Target architecture (decided 2026-07-14):

```
you / users
   │  https://app.<domain>            https://api.<domain>
   ▼                                       ▼
App Platform STATIC SITE  ──fetch──▶  App Platform SERVICE (this API)
(inspectit-app.html)                       │
                                           ├──▶ Managed PostgreSQL (private)
                                           └──▶ Spaces bucket (files, later phase)
```

### Step 0 — What Brandon must provide (blockers, ~30 min of clicking)

| Item | Where | Notes |
|---|---|---|
| Domain name | Registrar (Porkbun/Namecheap/Cloudflare) | ~$15/yr; `.app` requires HTTPS (automatic). Check availability; have a backup name |
| DO **API token** | DO console → API → Generate New Token (write scope) | Lets the deploy be scripted with `doctl`; alternative is clicking every step in the DO console |
| GitHub account + empty private repo | github.com | App Platform deploys from GitHub; `inspectit-backend` gets pushed there |
| Go/no-go on ~$25/mo | — | API $5 + Postgres $15 + Spaces $5; static site free |

### Step 1 — Push the repo to GitHub

```bash
cd ~/inspectit-backend
git remote add origin git@github.com:<USER>/inspectit-backend.git
git push -u origin main
```
(App static site can live in the same repo under `web/` — see Step 6.)

### Step 2 — Create the managed database

Console: *Databases → Create → PostgreSQL 16, Basic 1 GB ($15), region NYC3.*
Or scripted:
```bash
doctl databases create inspectit-db --engine pg --version 16 \
  --size db-s-1vcpu-1gb --region nyc3
```
Then: create a database user/db for the app (defaults fine), copy the
**connection string** (the `sslmode=require` one), and later restrict inbound
sources to the App Platform app ("Trusted Sources").

### Step 3 — Create the Spaces bucket (files phase-ready)

Console: *Spaces → Create bucket* `inspectit-files`, region NYC3, **private**.
Generate a Spaces access key pair (API → Spaces Keys). Not load-bearing until
the per-record attachments phase, but cheap to provision now.

### Step 4 — Pre-deploy code changes (the F1–F5 fixes)

To be implemented in one session before first deploy:

1. `APP_ENV=production` mode in `config.py`: hard-fail if `JWT_SECRET` unset
   or `DEV_MODE=1`; read `ALLOWED_ORIGINS` env (comma-separated) → CORS.
2. Body-size middleware (reject Content-Length > 50 MB with 413).
3. Per-IP rate limiter on `/auth/*` (simple in-process token bucket).
4. `pg_advisory_lock(42)` around `run_migrations()`.
5. `POOL_MAX` env (default 5 in prod); add `boto3` to requirements;
   `Dockerfile`-free run command (below) verified.
6. App-side: change `CLOUD_DEFAULT_API` in `inspectit-app.html` to the
   production API URL (the server-address field still allows overrides).

### Step 5 — Create the App Platform app (API service)

App spec (checked into the repo as `.do/app.yaml`):

```yaml
name: inspectit
region: nyc
services:
  - name: api
    github:
      repo: <USER>/inspectit-backend
      branch: main
      deploy_on_push: true
    environment_slug: python
    run_command: uvicorn api.main:app --host 0.0.0.0 --port $PORT --workers 2
    instance_size_slug: basic-xxs        # $5/mo
    instance_count: 1
    http_port: 8080
    health_check:
      http_path: /health
    envs:
      - key: APP_ENV
        value: production
      - key: JWT_SECRET
        type: SECRET                      # set once in the console
      - key: DATABASE_URL
        value: ${inspectit-db.DATABASE_URL}   # auto-injected binding
      - key: ALLOWED_ORIGINS
        value: https://app.<domain>
      - key: POOL_MAX
        value: "5"
      - key: STORAGE_BACKEND
        value: s3
      - key: SPACES_REGION
        value: nyc3
      - key: SPACES_BUCKET
        value: inspectit-files
      - key: SPACES_KEY
        type: SECRET
      - key: SPACES_SECRET
        type: SECRET
databases:
  - name: inspectit-db
    cluster_name: inspectit-db
    engine: PG
    production: true
```

Create with `doctl apps create --spec .do/app.yaml` (or console → Apps →
Create App → from GitHub). First boot runs migrations + seeds presets
automatically; watch the deploy log for `schema_migrations` output, then hit
`https://<default-app-url>/health`.

### Step 6 — Deploy the app itself (static site)

Add to the same spec:

```yaml
static_sites:
  - name: web
    github: { repo: <USER>/inspectit-backend, branch: main }
    source_dir: web            # contains index.html (the single-file app)
    index_document: index.html
```

`web/index.html` = the current `inspectit-app.html` with `CLOUD_DEFAULT_API`
pointed at the API URL. The Desktop copy keeps working unchanged — the hosted
copy simply becomes the one phones/other computers open. (From this point the
GitHub repo is the master copy of the app; the "replace the live file"
workflow gains a `git push` step — worth adopting deliberately.)

### Step 7 — Domain + DNS + TLS

1. At the registrar: point nameservers to DO (`ns1–3.digitalocean.com`), or
   add individual CNAMEs if you prefer keeping DNS at the registrar.
2. DO console → the app → Settings → Domains: add `api.<domain>` (attach to
   the api service) and `app.<domain>` (attach to web). Certificates are
   issued automatically; `.app` domains enforce HTTPS by design.
3. Update `ALLOWED_ORIGINS` to the final `https://app.<domain>` and redeploy.

### Step 8 — First-run smoke test (scripted, ~5 min)

1. `GET /health` → `{"ok": true}`.
2. Create the real company account via the app's Cloud sync modal (production
   URL prefilled) — **this signup is the real one**; dev-machine test accounts
   don't exist in prod.
3. Watch initial sync push all collections; verify counts via `GET
   /companies/{id}/collections`.
4. Sign in from a phone browser at `https://app.<domain>` → data appears.
5. Confirm `DEV_MODE` behavior is off: `/auth/forgot` must NOT return a token.

### Step 9 — Post-deploy hygiene

- DB: enable Trusted Sources (App Platform only); confirm daily backups on.
- Alerts: App Platform → enable deploy-failure + health-check email alerts.
- Set a calendar reminder to rotate the DO API token & Spaces keys yearly.
- Keep `run_dev.py` local workflow for development; production deploys happen
  via `git push` (auto-deploy on push is in the spec).

### Cost recap

| Piece | Monthly |
|---|---|
| API service (basic-xxs) | $5 |
| Managed PostgreSQL (1 GB) | $15 |
| Spaces (250 GB incl. CDN) | $5 |
| Static site | $0 |
| **Total** | **~$25/mo** + ~$15/yr domain |

---

## 7. After deployment (next phases, unchanged)

1. **Responsive/touch UI pass** — the app on phones (works day one via the
   hosted URL; the pass makes it *pleasant*).
2. **Per-record API + attachments to Spaces** — unlocks inspector
   (assigned-scope) logins, kills the 5 MB ceiling for real, starts real
   `files`/Spaces traffic.
3. **Email delivery** (Resend/Postmark) — invitations + password resets stop
   returning links in responses; resolves F6.
4. **PWA/offline, then billing** — per the roadmap and pricing-hypothesis
   discussion (2026-07-16: define pricing structure hypothesis anytime;
   commit to numbers only with real usage data).
