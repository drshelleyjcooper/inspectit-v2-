# Inspectit — Users, Roles & Permissions

**Version:** 2.4 · **Date:** 2026-09-09 · **Status:** specified, settled,
written, and applied — every step in §8 is committed on `user-roles-v2` with
the full suite green (1,213 tests)
**Supersedes:** v1.1 (2026-08-29), which is shipped in `inspectit-app.html`
**Source of truth for the matrix:** this document. `User_Roles_Chart.pdf` is
now historical — the decisions in §2 go beyond what the chart covers.
**Mirrors:** `api/presets.py`, `roles.permissions`, `roles.grants`

---

## 1. What changed since v1.1

v1.1 closed the frontend gap: people sign in as themselves, the company page
manages real users, and the role decides what you see. That all stands and none
of it is being rewritten.

What changed is the shape of the role set itself. Six decisions, taken
2026-08-30 and 2026-08-31:

1. **Grants are domain-limited.** Vehicle Manager, Property Manager and Project
   Manager are siblings, not a ladder. Each may only create sign-ins inside its
   own domain.
2. **Inspection and maintenance split into separate roles.** An inspector
   inspects; a maintenance person works the scheduler and spend log. Neither
   does the other's job.
3. **Repairs and warranties become manager level.** Nobody below a domain
   manager creates or edits them.
4. **Viewers are domain-scoped.** A domain manager may create a viewer limited
   to its own domain; only a Company Administrator creates a company-wide
   Viewer.
5. **`export` leaves the action list; `admin` replaces it.** Still seven
   actions.
6. **A `company` module joins the matrix.** Twelve modules.

Net effect: 8 presets become 13, and two small schema additions carry the rules
the module × action matrix can't express.

---

## 2. Decisions and their reasoning

### 2.1 Carried forward from v1.1 (unchanged)

| Decision | Resolution |
|---|---|
| Manager `delete` | Removed. Deletion is Company Administrator only. |
| Manager user management | Yes — Managers create sign-ins for roles below their own rank. |
| Property Manager ↔ projects | Full create/edit, not read-only. |
| Viewer `print` | Kept. Viewer reads and prints. |
| Project Manager ↔ properties | Parent property visible read-only; the role is unusable without it. |
| Inspector scope | Company, not assigned. No assignment screen exists, and a single-operator fleet gains little from per-record scoping. |
| ~~Project Manager scope~~ | ~~Assigned.~~ **Superseded 2026-08-31 — company scope; see §2.7.** |
| Multiple roles per member | Yes. Effective permissions are the union. |

### 2.2 New: domain-limited grants

The v1.1 rule was "roles ranked below your own." That works for Manager, where
rank does all the work. It fails for the domain roles, because Vehicle Manager,
Property Manager and Project Manager sit at the same rank. Rank alone would let
a Property Manager create a Vehicle Inspector, crossing the exact line the
domain roles exist to draw.

So the rule is **rank *and* domain**. Each domain manager may issue only the
roles inside its own domain, listed explicitly per preset in §4.2 rather than
computed from a rank number.

### 2.3 New: inspection and maintenance are different jobs

v1.1 gave inspectors the maintenance scheduler ("inspectors can work the
maintenance scheduler but not export", BACKEND-SCHEMA §3.3). **That is
superseded.** Vehicle Inspector reaches `vehicle_inspections` only; the new
Vehicle Maintenance role reaches `vehicle_maintenance` only. Same on the
property side.

**One person may still do both — but a domain manager cannot arrange it.** The
combination is available, and a Company Administrator or Manager may grant it
deliberately. What's blocked is a domain manager assembling it from two
narrower grants, which would let a Vehicle Manager manufacture something close
to a peer out of two roles it *is* allowed to issue.

The block is enforced **at submission**, not after the fact:

- The invite form greys out Vehicle Maintenance for any address that already
  holds Vehicle Inspector, with the reason on hover — *"this combination needs
  administrator approval; ask an admin to grant it."*
- The check runs against **current members and pending invitations**, so it
  catches accumulation across two different managers and two separate invites,
  not just both roles selected in one form.
- The API enforces the same rule at invitation create **and** at acceptance,
  since the membership set can change between the two.

An earlier draft held the grant in a pending-approval queue instead. That was
dropped: on the accumulation path it would have revoked a working inspector's
access the moment they accepted a second invite, and with no email delivery
wired up the request could sit unseen for days. Blocking at submission keeps
the rule visible where the decision is made and nobody loses access they
already had. **No approval queue object is needed** — this was the only thing
that would have required one.

The rule fires **within one domain only**. Vehicle Inspector plus Property
Maintenance is an ordinary cross-domain pair and needs no special handling.

An earlier proposal auto-promoted inspector + maintenance to the domain manager
role. Rejected: permissions are a union of held roles with no place for a rule
watching combinations, and it was an escalation path around the grant limits.

### 2.4 New: repairs and warranties are manager level

`vehicle_repairs`, `vehicle_warranties`, `property_repairs` and
`property_warranties` are created and edited by the domain manager and above.
Inspectors and maintenance personnel get nothing on them.

Viewers still **read and print** them (confirmed 2026-08-31). Viewer is
read-only across everything it can see, and a domain viewer sees its whole
domain. "Manager level only" governs create and edit.

### 2.5 New: domain viewers, and who may grant them

Vehicle Viewer, Property Viewer and Project Viewer are read-and-print inside
one domain. The company-wide Viewer is unchanged and remains **Company
Administrator only** — a domain manager who could issue it would be granting
read access across every domain through the back door.

Whether a given domain manager may issue its domain viewer is **decided per
person by the Company Administrator when the manager role is assigned**, and
changeable later. Two Vehicle Managers can differ.

This is the **first per-membership permission override in the design.**
Everything else derives from roles alone (token → membership → union of roles →
scope). It lives on `memberships`, not on the role, because it varies by
person. BACKEND-SCHEMA §3 should stop saying permissions are roles-only.

### 2.6 New: `export` out, `admin` in; the `company` module

Dropping `export` left the Export/Import backup control ungated — that column
was carrying the Manager-versus-inspector line and a `data-perm` attribute in
the app. `admin` takes its place in the action list, and backup egress gates on
`company:admin`, held by Company Administrator and Manager.

The `company` module also gives user management a home (`company:assign`, which
§7 of v1.1 already wanted for Manager) and company settings a key.

### 2.7 No preset is assigned-scope

Decided 2026-08-31, after the presets were written. Project Manager and Project
Viewer were the last two roles at `scope='assigned'`; both are now company
scope, for the same reason the inspectors were flipped in §2.1.

Assigned scope does not work in this app yet. Sync is collection-level — the
app pulls and pushes whole collections through `/collections/{key}` — and
assigned-scope roles cannot use it at all (BACKEND-ANALYSIS §3). The REST
endpoints do filter by assignment, but the app doesn't read its data through
them. So an assigned-scope member signs in to an empty screen no matter how
many assignments exist.

Two further things would have been needed even with an assignment screen built:

- **The app would have to read projects per-record**, outside collection sync,
  for assigned members only — a change to the sync layer, not a new screen.
- **Scope is a property of the role, not the module.** Project Manager's
  `properties: ["view"]` resolves to assigned scope too, so someone assigned to
  a project still couldn't see its parent property. The preset's "reads the
  parent property for context" intent silently didn't work.

Flipping costs one line per role and is reversible. Shipping two roles that
show an empty screen is not: people build around a role or abandon it.

**Consequence.** The `assignments` table, its endpoints, and the filtering in
`permissions.py` are now dormant. They stay in place and tested. Assigned scope
returns with the per-record API, which is already on the roadmap for
attachments — those still travel as base64 inside collection JSON.

---

### 2.8 Module list: BACKEND-SCHEMA's, plus one

README line 141 listed a different set of eleven modules — generic
`inspections` / `maintenance` / `repairs` / `warranties` shared across domains,
plus `estimates`, `payments`, `option_lists` and `company`. **Not adopted, and
the README line should be corrected.** Collapsing the vehicle/property split
would make Vehicle Inspector and Property Inspector — and Vehicle Manager and
Property Manager — impossible to express, since scope is `company` vs
`assigned`, not vehicles vs properties.

`estimates`, `payments` and `option_lists` need **no keys of their own**.
`project_estimates` and `project_payments` are child tables of projects, and
BACKEND-SCHEMA §3.1 already defines the `projects` module as covering the
dossier's sections. Option lists are all project vocabularies (project types,
contractor types, cost categories, permit types, final-record types, scope
types, payment types — BACKEND-SCHEMA §7), so they belong under `projects` too.

> **Note for later.** If vehicles or properties ever get customizable dropdowns
> of their own, they will have no permission key — `option_lists` living under
> `projects` won't cover them. Not a problem today; every customizable list in
> the app is a project list.

Only `company` is genuinely missing, and it stands alone rather than folding
into `projects`: project editors must not reach company settings, or Project
Manager and Property Manager acquire company-level control and the grant
hierarchy collapses.

---

## 3. The model

Two axes, plus two things the axes can't hold.

**Modules (12).** The eleven from BACKEND-SCHEMA §3.1 unchanged, plus
`company`:

`vehicles` · `vehicle_inspections` · `vehicle_maintenance` · `vehicle_repairs` ·
`vehicle_warranties` · `properties` · `property_inspections` ·
`property_maintenance` · `property_repairs` · `property_warranties` ·
`projects` · `company`

**Actions (7).** `view` · `create` · `edit` · `delete` · `print` · `assign` ·
`admin`

- `view` is implied by module access; `edit` implies `create`. Presets grant
  both together, as before.
- `assign` on an entity module = may assign users to records in it.
- `assign` on `company` = may issue sign-ins. Which sign-ins is §4.2's job.
- `admin` on `company` = data egress (Export/Import backup, spend exports).
- `export` no longer exists. Every occurrence in `presets.py`, in `data-perm`
  attributes, and in BACKEND-SCHEMA §3.1 becomes `admin` on `company` or is
  removed.

**What the axes can't hold**, and where each goes instead:

| Rule | Home |
|---|---|
| Which roles this role may issue | `roles.grants text[]` — a new column (§7.2) |
| Which it may issue *only* with the flag set | `roles.viewer_grants text[]` — a new column (§7.2) |
| Whether this member may issue viewers | `memberships.can_grant_viewers` — a new column (§7.2) |
| The inspector + maintenance block | Code, in the members router and the invite form (§2.3) |

`grants` replaces v1.1's `manageUsers: 'all' | 'below' | false`. A rank string
can't express "vehicle roles only," and the domain rule is now the whole point.

---

## 4. The thirteen presets

### 4.1 Permissions

`v` view · `c` create · `e` edit · `d` delete · `p` print · `as` assign ·
`ad` admin. A blank cell is no access at all.

| Role | vehicles | veh_insp | veh_maint | veh_repairs | veh_warr | properties | prop_insp | prop_maint | prop_repairs | prop_warr | projects | company | Scope |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Company Administrator** | vcedp·as | vcedp | vcedp | vcedp | vcedp | vcedp·as | vcedp | vcedp | vcedp | vcedp | vcedp·as | vedp·as·ad | company |
| **Manager** | vcep·as | vcep | vcep | vcep | vcep | vcep·as | vcep | vcep | vcep | vcep | vcep·as | vep·as·ad | company |
| **Vehicle Manager** | vcep·as | vcep | vcep | vcep | vcep | — | — | — | — | — | — | as | company |
| **Property Manager** | — | — | — | — | — | vcep·as | vcep | vcep | vcep | vcep | vcep·as | as | company |
| **Project Manager** | — | — | — | — | — | v | — | — | — | — | vcep·as | as | company |
| **Vehicle Inspector** | v | vcep | — | — | — | — | — | — | — | — | — | — | company |
| **Property Inspector** | — | — | — | — | — | v | vcep | — | — | — | — | — | company |
| **Vehicle Maintenance** | v | — | vcep | — | — | — | — | — | — | — | — | — | company |
| **Property Maintenance** | — | — | — | — | — | v | — | vcep | — | — | — | — | company |
| **Vehicle Viewer** | vp | vp | vp | vp | vp | — | — | — | — | — | — | — | company |
| **Property Viewer** | — | — | — | — | — | vp | vp | vp | vp | vp | — | — | company |
| **Project Viewer** | — | — | — | — | — | v | — | — | — | — | vp | — | company |
| **Viewer** | vp | vp | vp | vp | vp | vp | vp | vp | vp | vp | vp | — | company |

Notes on individual cells:

- **`delete` appears in one row only.** Company Administrator. Inspection
  reports are records; the person who filed one shouldn't erase it. Paired with
  the audit log.
- **`company:assign` on the domain managers** lets them open the invite form.
  It does not say what they may issue — `grants` does.
- **No domain manager holds `company:admin`**, so no domain manager can export
  a company backup.
- **No preset is assigned-scope** (§2.7). Every one of the thirteen is
  company scope. Assigned scope returns when the per-record API lands.
- **Company-wide Viewer has no `company` access** — it reads business data, not
  settings or member lists.
- **`company:edit` excludes billing** (confirmed 2026-08-31). Manager edits
  company settings; billing stays with Company Administrator. When billing
  ships it needs its own gate, not `company:edit`.

### 4.2 Grants

| Role | May issue | Only if `can_grant_viewers` |
|---|---|---|
| Company Administrator | every role, including Company Administrator and Viewer | — |
| Manager | every role except Company Administrator, Manager, and the company-wide Viewer | — |
| Vehicle Manager | Vehicle Inspector, Vehicle Maintenance | Vehicle Viewer |
| Property Manager | Property Inspector, Property Maintenance | Property Viewer |
| Project Manager | *(nothing)* | Project Viewer |
| all others | *(nothing)* | — |

Three consequences worth stating plainly:

- **Project Manager can invite nobody by default.** Its domain has no inspector
  or maintenance role beneath it, so an administrator who wants a Project
  Manager to build a team enables `can_grant_viewers` and gets exactly one
  grantable role.
- **Only Company Administrator issues the company-wide Viewer**, and only
  Company Administrator issues Company Administrator. This was inconsistent in
  the first draft of §4.2, which let a Manager issue the Viewer; §2.5 governs.
  A Manager may still issue the three domain viewers.
- **Manager may issue the inspector + maintenance combination** (confirmed
  2026-08-31). Manager sits above the domain managers, so the §2.3 block does
  not apply to it. The block constrains domain managers specifically.

### 4.3 Dual roles

Already supported, no change needed: `users.email` is unique, `membership_roles`
is many-to-many, and effective permissions are the union of every held role.
`invitations.role_ids` is an array, so **one invitation carries both roles** —
a person who manages vehicles and properties gets a single sign-in with Vehicle
Manager and Property Manager attached, not two accounts.

**One email, always.** This has an API consequence (§7.4): if a Vehicle Manager
invites `sam@co.com` as Vehicle Inspector and a Property Manager later invites
the same address as Property Inspector, accepting the second must *add* a role
to Sam's existing membership. It must not fail on the unique email, and must
not create a second membership.

---

## 5. Where a role comes from

Unchanged from v1.1 and still correct:

`cloudSignIn()` stores `roles`, the server's `permissions` blob, and the user id.
`enterApp()` calls `permsRefresh()` on every open, so a role change lands
without a re-login and the cached copy keeps working offline. A server-sent blob
always beats the built-in preset.

**Signed out, nothing is gated.** The app stays the offline single-user tool it
has been since v0.18.

**The frontend is not the enforcement point.** Hiding a button is a courtesy.
Every write passes the API's own check: token → membership → union of roles →
scope.

Two additions for v2.0:

- `/me` must also return `can_grant_viewers` for the current membership, or the
  invite form can't build its role list.
- `/me` must return each role's `grants`, for the same reason. Hardcoding the
  §4.2 table in the frontend would break the "change a role in the backend,
  the UI follows" property that v1.1 established.

---

## 6. Frontend changes

Building on v1.1's twenty-two edits, still single-file, still no build step.

| Area | Change |
|---|---|
| Presets | replace the 8 built-in preset objects with the 13 from §4.1 |
| `manageUsers` | remove; replace with `grants` read from the server, falling back to the §4.2 table |
| `data-perm` | every `:export` becomes `company:admin` — Export/Import backup is the live one |
| Invite form | role list built from `grants` + `can_grant_viewers`; combination block per §2.3 with the reason on hover |
| Members panel | viewer-grant checkbox on manager rows, admin-only, `PATCH /members/{id}` |
| Repairs / warranties | `data-perm="vehicle_repairs:edit"` and the three siblings on their add/edit controls — currently unmarked |
| Tabs | unchanged mechanism; the new roles resolve correctly through it |
| Role normalisation | extend to the five new names |

The nine-role expansion needs no new gating machinery. `applyPerms()` runs after
every render and reads whatever blob the server sent.

---

## 7. Backend changes

### 7.1 `api/presets.py`

Rewrite against §4.1 and §4.2. The v1.1 patch list is superseded; these are the
deltas from what runs today:

```
ALL PRESETS         "export" -> removed; add "admin" to the action vocabulary
                    add "company" module

Company Admin       add company: view/edit/delete/assign/admin
Manager             remove "delete" everywhere; add company:view/edit/assign/admin
Vehicle Manager     add vehicle_repairs, vehicle_warranties (vcep); add company:assign
                    grants: [Vehicle Inspector, Vehicle Maintenance]
Property Manager    projects ["view"] -> vcep; add company:assign
                    grants: [Property Inspector, Property Maintenance]
Project Manager     add company:assign; scope 'assigned' -> 'company'; grants: []
Vehicle Inspector   scope 'assigned' -> 'company'
                    REMOVE vehicle_maintenance  (superseded, see §2.3)
Property Inspector  scope 'assigned' -> 'company'
                    REMOVE property_maintenance
Viewer              no permission change; remains Company-Administrator-grantable only

NEW  Vehicle Maintenance, Property Maintenance,
     Vehicle Viewer, Property Viewer, Project Viewer
```

**Re-seeding is now a blocker, not a footnote.** The seeder upserts by name, so
existing companies keep their old permission JSON — which for Vehicle Inspector
still includes maintenance. Bump a preset version and force a re-seed on
deploy.

*Closed 2026-09-03, recorded 2026-09-09.* The paragraph above described the
v1.1 seeder. Step 3 fixed it by changing the conflict clause to
`DO UPDATE SET`, not by versioning — see the retraction in §11.

The two inspector presets *lose* a module, so anyone currently working
maintenance as an inspector would lose that access at re-seed. **Confirmed
2026-08-31: no live company is affected** — nothing is deployed yet. If that
changes before this ships, those people need the new maintenance role granted
in the same pass.

### 7.2 Migration

```sql
ALTER TABLE roles       ADD COLUMN grants        text[]  NOT NULL DEFAULT '{}';
ALTER TABLE roles       ADD COLUMN viewer_grants text[]  NOT NULL DEFAULT '{}';
ALTER TABLE memberships ADD COLUMN can_grant_viewers boolean NOT NULL DEFAULT false;
```

All three default to the restrictive value, so an unmigrated or
partially-seeded company grants nothing rather than everything.

`viewer_grants` was added during implementation. `grants` plus the membership
flag can say *whether* a manager may issue a viewer but not *which* one, and
hardcoding "Vehicle Manager → Vehicle Viewer" in `permissions.py` would put the
domain mapping back into code — the thing `grants` exists to avoid.

No index is needed. The seeder's `ON CONFLICT (name) WHERE company_id IS NULL`
already works, so the partial unique index exists in `001_initial.sql`.

### 7.3 `api/permissions.py`

- Recognise `admin`; drop `export`.
- Add a grant check used by the members router: may this membership issue this
  role? Reads the union of `grants` across held roles, plus
  `can_grant_viewers`.

### 7.4 `api/routers/members.py`

Four changes (item 3 turned out to need no work), all on the invitation path:

1. **Enforce grants server-side.** A hidden dropdown option is not a control;
   reject a `role_ids` the caller may not issue.
2. **Enforce the §2.3 block**, at create and again at acceptance, against
   current members *and* pending invitations.
3. ~~**Merge on accept** (§4.3).~~ **Already works.** The accept route's
   `ON CONFLICT (company_id, user_id) DO UPDATE` reuses the membership and the
   role inserts are `ON CONFLICT DO NOTHING`, so a second invitation adds a
   role rather than colliding. Nothing to build.
4. **Return roles on `GET /invitations`.** It currently returns only
   `id, email, status, expires_at, created_at`, so the UI shows "No role" for
   an invitation that has one. v1.1 worked around this client-side, which only
   fixes the browser that sent it. Add `role_ids` or `role_names`, and a name
   field on the invitation model.
5. **Reject or supersede duplicate pending invitations** for one address.
   Necessary now, since the §2.3 block reads pending invitations to decide.

Still worth doing and still unaddressed: nothing surfaces the 14-day
invitation expiry.

### 7.5 Found while implementing

Three of these are consequences of giving five roles `company:assign`, which
v1.1 gave to one. They are not optional.

**All five were applied 2026-09-03** — `_assert_may_manage` in both
`update_member` and `remove_member`, `_other_admins` re-keyed to
`ADMIN = ANY(r.grants)`, `require_any` on `GET /invitations`, the
`create_invitation` 409 narrowed to "nothing new to add", and `deleted_at =
NULL` in the accept-route upsert.

- **`update_member` and `remove_member` are gated on `company:assign` alone.**
  That was the whole check when only Company Administrator held it. Under v2.0
  a Vehicle Manager passes the dependency and could rewrite the administrator's
  roles or remove them. Fixed with `_assert_may_manage`: you may not modify or
  remove a member holding roles you couldn't have issued.
- **`_other_admins` stopped meaning what it says.** It counts members with
  `company:assign` as a proxy for "someone who can restore an administrator."
  Five roles hold it now, so a company with one administrator and one Vehicle
  Manager would pass the last-admin guard and let you strand it. Re-keyed to
  `'Company Administrator' = ANY(r.grants)`.
- **The domain managers can't read `company:view`,** so `GET /invitations`
  refused them — they could create an invitation and then not see it. Needs a
  `require_any(("company","view"), ("company","assign"))` dependency, which is
  new in `permissions.py`.
- **`create_invitation`'s 409 blocked the merge path.** It refused any address
  already a member, which is exactly the §4.3 case. Now it refuses only when
  there is nothing new to add, and supersedes any pending invitation for that
  address rather than stacking a second one.
- **Unrelated bug, live in v1.1:** re-inviting a removed member creates a dead
  account. `remove_member` sets `deleted_at`; the accept route's
  `ON CONFLICT DO UPDATE SET status = 'active'` revives the status and leaves
  `deleted_at` set. Every downstream query filters `deleted_at IS NULL`, so the
  person accepts, gets a token, and is told they aren't a member — with nothing
  visible to the administrator. Add `deleted_at = NULL` to that clause.

---

## 8. Build order

1. **Reconcile the vocabulary** — §3's twelve modules and seven actions into
   BACKEND-SCHEMA §3.1, README line 141, and the frontend presets. One
   canonical list before any code moves.
2. **Migration + `presets.py`** — §7.1 and §7.2 together, with the re-seed
   decision made before deploy.
3. **`permissions.py` and the members router** — §7.3 and §7.4. The grant rules
   are worthless until the server enforces them.
4. **Frontend** — §6.
5. **Tests** — §9. The suite asserts against the eight-role model and will not
   catch v2.0 regressions until it's rewritten.

**Status as of 2026-09-03 — all five steps applied on branch `user-roles-v2`:**

| Step | State | Commits |
|---|---|---|
| 1. Vocabulary | applied earlier | — |
| 2. Migration + `presets.py` | applied earlier | migration 004 runs clean; psycopg3 list→`text[]` adaptation verified on live start |
| 3. `permissions.py` + members router | applied 2026-09-03 | `30626de`, `26e1f73`, `4eb81a4`, `b2e6ee4` |
| 4. Frontend | applied earlier | `inspectit-app.html`, +1,266 lines |
| 5. Tests | applied earlier | `test_role_matrix.py` (246 lines), `test_grants.py` (44 tests) |

Step 3 landed as four edits: `permissions.py` wholesale,
`BLOCKED_COMBINATIONS` into `presets.py`, eight sites in
`api/routers/members.py`, and the acceptance-time re-check in
`api/routers/auth.py`. The two `auth.py` items §7.4 called for were already
present — merge-on-accept via the existing `ON CONFLICT` upsert, and the
`deleted_at = NULL` fix.

Step 2 has a dependency worth knowing about. BACKEND-ANALYSIS §3 records that
assigned-scope roles can't use collection sync at all; they're waiting on the
per-record API. Flipping the inspectors to company scope is what makes them
able to sync in the first place. **Today an inspector who signs in gets
nothing.** That puts step 2 ahead of the cosmetic invitation fixes.

---

## 9. Testing

**The rewrite described below is done.** The suite is now 1,209 tests against a
throwaway database, up from 34: the original groups plus `test_role_matrix.py`
(the §4.1 grid, table-driven, 246 lines) and `test_grants.py` (44 tests
covering §9.2). All green as of 2026-09-03.

What follows is kept as the record of what was built and why, not as work
outstanding.

### 9.1 The rewrite risk: passing vacuously

Existing permission tests are mostly negative — *this role is denied that
endpoint*. Negative assertions survive almost any change, including changes
that break the product. A test asserting "Vehicle Inspector cannot delete a
vehicle" still passes if the inspector loses every module in the system.

Two of v2.0's changes are exactly of that shape. Vehicle Inspector loses
`vehicle_maintenance`; Manager loses `delete`. Both narrow a role, so the
current tests will go green while telling you nothing.

**Replace the per-role tests with one table-driven test over the §4.1 grid.**
Thirteen roles × twelve modules × seven actions, each cell asserted in both
directions — granted cells reachable, ungranted cells refused. The table lives
next to the test, not imported from `presets.py`, so drift between the spec and
the seeder fails the build instead of propagating.

### 9.2 New cases the grant rules introduce

None of this is covered today, because delegated granting didn't exist.

**Grant enforcement** (§4.2), each hitting `POST /companies/{id}/invitations`
directly rather than through the UI, since a greyed-out dropdown option is not
a control:

- Vehicle Manager → Vehicle Inspector: **200**
- Vehicle Manager → Property Inspector: **403** (crosses the domain)
- Vehicle Manager → Vehicle Manager: **403** (peer)
- Domain manager → company-wide Viewer: **403**
- Manager → Manager, and Manager → Company Administrator: **403**
- Company Administrator → any of the thirteen: **200**
- Project Manager → anything, viewer flag off: **403**

**The viewer flag** (§2.5):

- `can_grant_viewers = false`, Vehicle Manager → Vehicle Viewer: **403**
- Admin flips it via `PATCH /members/{id}`, same request: **200**
- Non-admin attempting the flip: **403**
- Two Vehicle Managers in one company with different flag values behave
  differently — the override is per-membership, not per-role

**The inspector + maintenance block** (§2.3). Seven cases, and the middle three
are where a naive implementation fails:

| Case | Expected |
|---|---|
| Domain manager, both roles in one invitation | 403 |
| Target already an active Vehicle Inspector, invited as Vehicle Maintenance | 403 at create |
| Target has a *pending* inspector invitation, invited as maintenance | 403 at create — the check reads pending invitations, not just members |
| Both invitations created legitimately, membership changes before acceptance | 403 at **accept** |
| Vehicle Inspector + Property Maintenance | allowed — different domains |
| Manager issues the combination | allowed |
| Company Administrator issues the combination | allowed |

**Merge on accept** (§4.3). Sam holds Vehicle Inspector; a Property Manager
invites the same address as Property Inspector; Sam accepts. Assert the
membership count is still 1, the role count is 2, no second `users` row was
created, and no unique-constraint error surfaced.

### 9.3 Regressions worth pinning

- **`company:admin` gates backup.** Manager reaches Export/Import; Vehicle
  Manager and Viewer get 403. This is the replacement for the deleted `export`
  action and nothing currently tests it.
- **`export` is gone.** A request naming it is an unknown action and is denied,
  not silently allowed.
- **Inspectors can sync.** They're company-scoped now, so collection sync works
  for them — it did not before, per BACKEND-ANALYSIS §3. Worth an explicit test
  because it's the change with the most user-visible consequence.
- **No preset is assigned-scope.** `test_role_matrix.py` asserts it across all
  thirteen. The assignments table and its filtering stay in place and tested,
  but nothing routes through them until the per-record API lands — so a preset
  that sets `scope='assigned'` is a bug until then.
- **Domain viewers read repairs and warranties but cannot edit them** (§2.4).
- **Re-seeding.** Drift a seeded preset row, re-seed, and assert it is
  updated rather than skipped by the upsert-by-name; that a second run is a
  no-op; that every column the `DO UPDATE SET` names is refreshed; and that a
  company-owned role sharing a preset's name is untouched. `PRESET_VERSION`
  exists but is inert; re-seeding does not key on it — see §11.
  `test_presets.py`.

### 9.4 Frontend

There's no automated harness — the app is a single file with no build step, and
adding one isn't worth it for this. A manual pass instead: sign in as each of
the thirteen roles and check tabs, route guards, the invite form's role list,
and the greyed controls with their hover reasons. The three that actually need
looking at are Project Manager with the viewer flag both ways, a domain manager
hitting the §2.3 block, and a dual Vehicle + Property Manager seeing the union.

The server is the enforcement point (§5), so a frontend gap is a cosmetic bug.
A server gap is a security bug. Weight the effort accordingly.

---

## 10. Not in this pass

- **Assignment UI.** No longer a launch blocker (§2.7) — no preset is
  assigned-scope, so nothing needs assigning. The endpoints
  (`POST`/`GET`/`DELETE /assignments`) and the filtering in `permissions.py`
  stay in place and tested, waiting on the per-record API.
- **Email delivery.** Invitations create a token; no mail provider is wired up,
  so the link is passed along manually. The §2.3 block was designed around this
  absence.
- **Custom roles UI.** The schema supports company-owned roles; 13 presets
  cover launch.
- **Audit surfacing.** `audit_log` captures writes; no screen reads it. Worth
  revisiting now that grants are delegated to five roles rather than one.
- **Billing.** Untouched. Company Administrator only, and it needs its own gate
  rather than riding on `company:edit` (§4.1).

---

## 11. Open items

Everything in §4 is decided. One item remains, and nothing here waits on it.

**Retracted 2026-09-08 — `_grants_user_management`.** v2.2 recorded this as an
open item: the predicate was said to still test `company:assign` while its
sibling `_other_admins` had been re-keyed, leaving the last-admin guard
skippable by demoting a sole Company Administrator to Vehicle Manager. **Wrong
on every point.** Both predicates were re-keyed to `ADMIN = ANY(grants)` in the
same 2026-09-03 pass, and the comment on the function records that fix citing
§7.5 — it was misread as a warning that the fix was missing rather than a note
that it had landed. The case was also already covered:
`test_demoting_the_sole_admin_to_a_domain_manager_is_refused` and
`test_the_sole_admin_is_still_left_administrable_after_the_refusal` have been
green since 2026-09-03, which is also why `test_grants.py` was 43 tests rather
than the 41 first recorded above.

One real gap came out of it. Manager also holds `company:assign` and §4.2
refuses `Manager → Company Administrator`, so the same stranding applies to
that role, and nothing tested it.
`test_demoting_the_sole_admin_to_a_manager_is_refused` closes it — commit
`11c578a`, suite now 1,209.

Kept rather than deleted so the next reader doesn't re-derive the same
suspicion from the same comment.

**Retracted 2026-09-09 — the §7.1 re-seed blocker.** §7.1 says the seeder
"upserts by name, so existing companies keep their old permission JSON" and
asks for a preset version bump plus a forced re-seed on deploy. That described
the v1.1 seeder accurately: its clause was `ON CONFLICT ... DO NOTHING`. The
same 2026-09-03 pass that shipped step 3 (`61a007a`) closed it by changing the
clause to `DO UPDATE SET` over scope, permissions, grants, viewer_grants and
updated_at, and the migration header in `004_role_grants.sql` and the
docstring on `seed_role_presets` both record that — but §7.1 was never
updated, and the §9.3 bullet kept asking for a version bump that nothing
implements. The blocker is closed. A `PRESET_VERSION = 2` constant does
exist in `presets.py` (added in the same commit), but nothing reads it: it
is not logged at boot despite its comment and `SPEC-UPDATE-2026-09-03.md`
saying so, no test asserts it, and the seeder does not key on it — the
`DO UPDATE` runs unconditionally every boot. Correction 2026-09-09 to the
first draft of this entry, which said no such constant existed.

Two framing slips corrected while pinning it. Presets are global rows
(`company_id IS NULL`), so drift would hit every company at once rather than
"existing companies" one by one. And the partial index
`uq_roles_preset_name` is the whole conflict target, so a company-owned role
may share a preset's name and is untouched by re-seeding — true, but nothing
had tested it. `test_presets.py` pins all four behaviours (drift is
repaired, a clean run is a no-op, every mutable column refreshes, custom
roles are left alone); the drift test was verified to fail against
`DO NOTHING`. Commits `6cd280a` and `543ec0e`; suite now 1,213.

**Vehicle/Property Manager sub-scoping.** BACKEND-SCHEMA §13 left open whether a
regional property manager should be assignable to a subset of properties rather
than the whole company. The schema already supports it via `scope='assigned'`,
so it stays available without a migration. Revisit if properties turn out to be
residential, where "every address" is a different proposition than "every
truck."
