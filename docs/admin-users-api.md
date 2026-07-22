# Admin: Users, Roles & Account Controls — API Contract

Company-admin surface only. `is_platform_admin` / `require_platform_admin` do not
exist yet (verified in Stage 0 recon — the Platform Admin Console prompt hasn't
built them). This contract is scoped entirely to `/companies/{cid}/...`; a
`/platform/...` mount reusing the same handlers is future work once that
dependency lands, not part of this pass.

## Ground truth vs. the original brief

The brief this was written from assumed some things that don't match the
code. Corrections, so nobody re-derives them later:

- **12 modules × 7 actions**, not 11×7 (`api/presets.py: MODULES`). The 12th is
  `company` — the module that governs member/role management itself.
- **No distinct `members` module and no separate "role assignment" action.**
  Invitations already establish the convention: `company:assign` governs
  "manage membership" (invite, and now suspend/reactivate/role-change/revoke
  sessions). `company:delete` is unused today; this pass gives it a job —
  gating the one truly destructive action (remove-from-company, Stage 4).
- Only the **Company Administrator** preset grants anything on `company` at
  all. Manager and everyone else can *see* the roster (via the existing
  `company:view` OR any-entity-`assign` clause on `GET /members`) but can't
  mutate it. Nothing in this pass changes that visibility rule.
- `memberships.status` already existed (migration 001) with `'suspended'` in
  its `CHECK`, just never set by anything. Stage 1 (already shipped on this
  branch) added `suspended_at` / `suspended_by` / `suspend_reason` and the
  atomicity constraint. This pass wires the endpoints on top of it.

## Additive migration for this stage: `005_refresh_token_context.sql`

`refresh_tokens` has no device/IP/last-seen data — can't build "active
sessions" for the member-detail view without it. Adds, nullable, populated at
mint/rotation time (never backfilt for tokens issued before this migration):

```sql
ALTER TABLE refresh_tokens
  ADD COLUMN ip           text,
  ADD COLUMN user_agent   text,
  ADD COLUMN last_seen_at timestamptz;
```

`last_seen_at` is touched on `/auth/refresh` (each rotation), not on every
authenticated request — the access token is stateless for 30 minutes, so
"last seen" has ~30-minute granularity, same as everywhere else "session"
means something in this system. That's an intentional cost/accuracy
tradeoff, not an oversight — a `last_seen_at` update per API call would mean
a write on every request.

## "Last activity" is two fields, not one

Stage 0 flagged this: assigned-scope users (inspectors) can't use collection
sync, so any "last activity" sourced only from `app_collections` reads empty
for them forever — not because they're inactive, but because the sync
surface literally excludes assigned-scope roles today (see
`collections.py`: `grant_scope(...) != "company"` is rejected). Collapsing
that into one ambiguous "last activity" field would make an admin read
"inspector never logs in" when the truth is "the data model doesn't capture
what they do yet." So the member-detail response carries two honestly
distinct fields:

- **`last_login`** — `MAX(refresh_tokens.created_at)` for the user. Reliable
  for every role; a real login timestamp regardless of scope.
- **`last_data_activity`** — `MAX(audit_log.at)` for `(user_id, company_id)`.
  Note this is *not* empty for every assigned-scope user by default —
  accepting an invitation itself writes an audit row, so most members show
  something here. The caveat is narrower than "assigned-scope users show
  nothing": it's that *collection-sync* activity specifically never
  populates this for them (`collections.py` rejects non-company-scope
  writes outright), so once the invite-accept row ages out of relevance, an
  inspector who's actively doing inspections through a future per-record API
  can still show a stale `last_data_activity` next to a recent `last_login`
  — that gap is what the frontend must not misread as "inactive." Label the
  two fields distinctly ("last seen" vs. "last data change") — see Stage 3
  note.

## Guard codes

Every mutating endpoint below can return `409` with one of:

| code | meaning | applies to |
|---|---|---|
| `last_admin` | would leave zero active memberships holding `company:delete` | suspend, roles-patch (removing the grant), delete (Stage 4) |
| `self_suspend` | an admin can't suspend/delete themselves | suspend, delete (Stage 4) |
| `self_deadmin` | an admin can't remove their own `company:delete`-granting role | roles-patch |

`cross_tenant` is **not** a code this surface returns. Every lookup below is
scoped `WHERE company_id = %s AND ...`; a `{uid}` belonging to another
company simply isn't found — `404`, not `409`. Producing a `409` here would
mean confirming "this user exists, just not here," which is a worse leak
than a plain 404. (A `409 cross_tenant` — or rather, an audited *allow* with
a privacy-event log entry — is a platform-admin-surface concept; out of
scope until that mount exists.)

`last_admin` is computed as: *does at least one other active
(`status='active'`, `deleted_at IS NULL`) membership in this company still
resolve `company:delete` through its role union, after this change?* This is
permission-based, not name-based — a company that clones "Company
Administrator" into a custom role with the same grants is still protected.

## Idempotency

`suspend` on an already-suspended membership, and `reactivate` on an
already-active one, both return `200` with the current state — not an
error. This is enforced by `db.suspend_membership` / `reactivate_membership`
already (Stage 1), so the endpoints get this for free; it's called out here
because it's a contract guarantee, not an implementation detail.

---

## Endpoints

### `GET /companies/{cid}/members`

Existing endpoint, extended. Unchanged: permission is `company:view` OR any
`assign` grant on `vehicles`/`properties`/`projects` (so managers can see who
they can assign — pre-existing behavior, not touched).

New query params, all optional: `status` (`active`|`suspended`), `role`
(role id), `q` (substring match on name or email, case-insensitive),
`limit` (default 50, max 500, existing default of 200 lowered to match the
paginated-from-the-start requirement — 200 as a default silently invited
"fetch everything"), `offset`.

Adds the `X-Total-Count` response header — the existing endpoint didn't set
one despite the doc'd convention; this brings it in line with `/audit` and
the new endpoints below.

Response, extended (found while building Stage 3's list view — the brief
wanted "last activity" and "invited-vs-joined" as list columns, and neither
existed on this endpoint): `membership_id`, `status`, `joined_at`
(`memberships.created_at` — cheap, always-populated, and a more honest
substitute for "invited-vs-joined" than a binary flag would be, since in
this system almost every member *is* invited except the one founder per
company), `last_login` (correlated subquery on `refresh_tokens`, same field
the detail endpoint exposes), `user_id`, `name`, `email`,
`roles: [{id,name}]`. `last_data_activity` stays detail-only — a per-row
audit-log aggregate for a 50-row list is a heavier query than this endpoint
should carry by default.

### `GET /companies/{cid}/members/{uid}`

New. Requires `company:view` (the same gate as `/audit` — this response
carries suspend reason, IP-bearing session data, and is more sensitive than
the plain roster).

```json
{
  "user_id": "...", "email": "...", "name": "...",
  "status": "active | suspended",
  "suspended_at": "...", "suspended_by": "...", "suspend_reason": "...",
  "roles": [{"id": "...", "name": "...", "scope": "company | assigned"}],
  "permissions": {"vehicles": ["view", "edit"], ...},
  "last_login": "2026-07-20T14:03:00Z",
  "last_data_activity": "2026-07-19T09:11:00Z",
  "sessions": [
    {"jti": "...", "created_at": "...", "expires_at": "...",
     "ip": "...", "user_agent": "...", "last_seen_at": "...",
     "revoked_at": null, "active": true}
  ]
}
```

`revoked_at` is null for a session that's merely expired by time, and set for
one killed by suspend/revoke-sessions/password-reset — the distinction Stage
3's UI needs to tell an admin "we kicked them out" from "their token just
timed out." (Caught this by testing the detail view live: the field was
computed into `active` server-side but never actually returned, so every
inactive session silently read as "Expired" regardless of the real reason.)

`suspended_at`/`suspended_by`/`suspend_reason` are `null` when active.
`sessions` includes revoked/expired ones too (`active: false`) so an admin
can see "yes, we already killed that" rather than the list just shrinking
silently — `404` if `uid` has no membership in this company.

### `PATCH /companies/{cid}/members/{uid}/roles`

Requires `company:assign`. Body: `{"add": ["role_id", ...], "remove": [...]}`
— either may be empty/omitted. Unknown role ids → `422` (same validation
`POST /invitations` already does: must exist and be `company_id IS NULL` or
`= cid`). Applying: `remove` first, then `add`, both idempotent
(`DELETE` affecting 0 rows / `INSERT ... ON CONFLICT DO NOTHING` are both
fine to call redundantly).

Guards: `self_deadmin` if `uid` is the caller's own user and the resulting
permission set no longer includes `company:delete`. `last_admin` if the
*target* (self or someone else) is the last `company:delete` holder and this
change would remove it.

Audit: `action="role_change"`, `subject_type="membership"`,
`subject_id=<membership_id>`, `details={"added": [...], "removed": [...],
"roles_before": [...], "roles_after": [...]}`.

Response: the updated `roles` + resolved `permissions`, same shape as the
detail endpoint's fields.

### `POST /companies/{cid}/members/{uid}/suspend`

Requires `company:assign`. Body: `{"reason": "..."}` — `reason` required,
`422` if blank (matches existing validation style, e.g. empty
`role_ids` on invitations).

Guards: `self_suspend`, `last_admin`.

Calls `db.suspend_membership` (Stage 1) — status flip + full refresh-token
revocation, one transaction. Audit: `action="suspend"`,
`subject_type="membership"`, `details={"reason": "..."}`. Idempotent per
above.

### `POST /companies/{cid}/members/{uid}/reactivate`

Requires `company:assign`. No body. No guards — reactivating never reduces
admin coverage. Calls `db.reactivate_membership`. Audit:
`action="reactivate"`, `subject_type="membership"`. Idempotent.

### `POST /companies/{cid}/members/{uid}/revoke-sessions`

Requires `company:assign`. No body, no guards (not destructive — the user
just has to log back in; self-revoke is allowed, e.g. an admin force-logging
out their own other devices). Calls `security.revoke_all_refresh_tokens`.
Audit: `action="revoke_sessions"`, `subject_type="membership"`.

### `DELETE /companies/{cid}/members/{uid}` — **not built in this pass**

Contract reserved, not implemented. Stage 4, after suspend/reactivate/roles
are live and reviewed: requires `company:delete` (the one preset action this
pass otherwise leaves unused), guards `self_suspend`-equivalent (`self`) +
`last_admin`, removes the membership row only (not the user, not their audit
trail — see the Stage-4 section of the original brief). Documented here so
the full surface is visible in one place; do not wire a route for this yet.

### `GET /companies/{cid}/audit` — extended, not replaced

Existing endpoint. The brief's contract names (`actor`, `target`, `type`,
`from`, `to`) don't all match the shipped schema — I'm extending with real
column names instead of introducing aliases for the same data:

New optional params: `actor` (filters `user_id`), `action` (exact match on
the `action` column — this is what the brief called `type`; `action` is the
actual column name and "follow existing conventions exactly" cuts toward not
inventing a second name for the same thing), `from`/`to` (ISO timestamps,
range on `at`). **Kept as-is:** `subject_type`/`subject_id` — already
shipped, already tested (`test_zz_hardening.py::test_pagination_and_audit`),
functionally identical to what the brief called `target`; renaming would
break existing consumers for a cosmetic reason.

### `POST /companies/{cid}/invitations`, `DELETE .../invitations/{iid}`

Unchanged — already live, already match this contract. Stage 3 just needs
to surface them in the UI (pending list + copy-link + revoke), per the
Known-traps note that the token is a live credential until email delivery
exists.

---

## What Stage 2 implementation touches

- `migrations/005_refresh_token_context.sql` (new)
- `api/security.py` — `make_refresh_token`/`rotate_refresh_token` start
  accepting/recording `ip`/`user_agent`; `last_seen_at` touched on rotation
- `api/routers/members.py` — extend `list_members`; add the five new routes
- `api/permissions.py` or a small new `api/guards.py` — the guard-check
  helpers (`last_admin` computation, `self_suspend`, `self_deadmin`)
- `tests/test_admin_users_api.py` (new)

No changes planned to `api/db.py`'s `suspend_membership`/
`reactivate_membership` (Stage 1) — the endpoint layer calls them as-is.
