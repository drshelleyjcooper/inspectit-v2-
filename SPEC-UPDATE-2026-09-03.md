# USER-ROLES-SPEC v2.1 → v2.2 update patch

**Date:** 2026-09-03
**Applies to:** `USER-ROLES-SPEC.md` v2.1 (2026-08-31), 639 lines
**Reason:** steps 1–5 of §8 are applied, tested and committed. The document
still describes them as drafted-but-not-run, and two of its warnings have been
overtaken by code.

Eight edits. Each gives the current text and its replacement. §1–§6 are
untouched and still accurate — the model did not change, only its status.

---

## Edit 1 — header status line

**Find (lines 3–4):**

```
**Version:** 2.1 · **Date:** 2026-08-31 · **Status:** specified, settled, and
written — patches drafted for every step in §8, not yet applied or run
```

**Replace:**

```
**Version:** 2.2 · **Date:** 2026-09-03 · **Status:** specified, settled,
written, and applied — every step in §8 is committed on `user-roles-v2` with
the full suite green (1,208 tests)
```

---

## Edit 2 — §7.4 opening line

Item 3 is struck through, so the count is wrong.

**Find:**

```
Five changes, all on the invitation path:
```

**Replace:**

```
Four changes (item 3 turned out to need no work), all on the invitation path:
```

---

## Edit 3 — §7.5, mark the findings applied and add the fifth

**Find:**

```
Three of these are consequences of giving five roles `company:assign`, which
v1.1 gave to one. They are not optional.
```

**Replace:**

```
Four of these are consequences of giving five roles `company:assign`, which
v1.1 gave to one. They are not optional. **All were applied 2026-09-03.**
```

**Then append to the end of the §7.5 bullet list:**

```
- **`_grants_user_management` was stale in the same way `_other_admins` was.**
  Found 2026-09-03, after the others, and the most serious of the five. The
  guard reads `losing_admin and _other_admins(...) == 0`, and
  `_grants_user_management` computes the first operand from `company:assign` —
  held by five roles under v2.0. Demoting a company's sole Company
  Administrator to Vehicle Manager therefore short-circuited the guard: 200 OK,
  `_other_admins` correctly 0, and the demoted user then refused permission to
  invite a replacement ("You may assign: Vehicle Inspector, Vehicle
  Maintenance"). No self-service recovery — the company would have needed
  direct database access. Re-keyed to `ADMIN = ANY(grants)`, matching
  `_other_admins`. Two regression tests in `test_grants.py`.

  Two lessons. Re-keying one half of a two-part predicate is worse than
  re-keying neither, because the remaining half looks correct in isolation.
  And any predicate using `company:assign` as a proxy for "administrator" is
  suspect under v2.0 — these two were the ones in `members.py`, but a grep
  before deploy is cheap.
```

---

## Edit 4 — §8 build order, record what shipped

**Insert after the numbered list, before "Step 2 has a dependency worth
knowing about":**

```
**Status as of 2026-09-03 — all five steps applied on branch `user-roles-v2`:**

| Step | State | Notes |
|---|---|---|
| 1. Vocabulary | applied earlier | — |
| 2. Migration + `presets.py` | applied earlier | migration 004 runs clean; psycopg3 list→`text[]` adaptation verified on live server start |
| 3. `permissions.py` + members router | applied 2026-09-03 | `30626de`, `26e1f73`, `4eb81a4`, `b2e6ee4` |
| 4. Frontend | applied earlier | `inspectit-app.html`, +1,266 lines |
| 5. Tests | applied earlier | `test_role_matrix.py` (246 lines), `test_grants.py` (43 tests) |

Step 3 landed as four edits: `permissions.py` wholesale,
`BLOCKED_COMBINATIONS` into `presets.py`, eight sites in
`api/routers/members.py`, and the acceptance-time re-check in
`api/routers/auth.py`. The two `auth.py` items §7.4 called for were already
present — merge-on-accept via the existing `ON CONFLICT` upsert, and the
`deleted_at = NULL` fix.
```

---

## Edit 5 — §9 opening, correct the suite description

**Find:**

```
The suite is 34 end-to-end tests against a throwaway database: auth flows, role
permissions, scoped data access, backup import/export, collection sync,
production hardening. The auth, import, sync and hardening groups are unaffected
by v2.0. The permission and scoping groups are written against the eight-role
model and need rewriting — this is real work sitting outside §7, and it's the
part most likely to be underestimated.
```

**Replace:**

```
**The rewrite described below is done.** The suite is now 1,208 tests against a
throwaway database, up from 34: the original groups plus `test_role_matrix.py`
(the §4.1 grid, table-driven, 246 lines) and `test_grants.py` (43 tests
covering §9.2 plus the last-admin regressions). All green as of 2026-09-03.

What follows is kept as the record of what was built and why, not as work
outstanding.
```

**Append to §9.1, after "Both narrow a role, so the current tests will go green
while telling you nothing":**

```
A live instance of this hazard turned up in the new tests themselves.
`test_block_counts_pending_invitations` called the address helper twice for
what was meant to be one person, so the two invitations went to different
addresses and the accumulation check was never exercised. It failed loudly and
was caught. The same defect with a permissive expected status would have passed
silently and proved nothing. Worth re-reading any test whose setup builds
identity from a randomised helper.
```

---

## Edit 6 — §9.2, the status-code discrepancy

§9.2 specifies 201 for a successful invitation. The route returns **200**, and
`test_grants.py` asserts 200. Tests and code agree; the spec doesn't.

**Find (in the grant-enforcement list):**

```
- Vehicle Manager → Vehicle Inspector: **201**
```

**Replace:**

```
- Vehicle Manager → Vehicle Inspector: **200**
```

Same substitution for the other two `201`s in §9.2 — "Company Administrator →
any of the thirteen" and the viewer-flag "Admin flips it via
`PATCH /members/{id}`, same request".

**This is a decision, not a correction.** 201 is the more correct HTTP for a
created resource, and the spec may simply be right where the code is older. The
patch changes the spec because that's the lower-risk direction late in a pass;
changing the route instead is one line plus three test assertions. Decide
deliberately rather than by default.

---

## Edit 7 — §11, no change

Earlier in this session `_grants_user_management` was going to be added here as
an unverified open item. It was then tested, reproduced and fixed, so it
belongs in §7.5 as a finding (Edit 3) rather than in §11 as outstanding work.

§11 stands exactly as written in v2.1 — the Vehicle/Property Manager
sub-scoping question is again the only thing open.

---

## Edit 8 — §7.1, the re-seeding blocker is resolved

**Find:**

```
**Re-seeding is now a blocker, not a footnote.** The seeder upserts by name, so
existing companies keep their old permission JSON — which for Vehicle Inspector
still includes maintenance. Bump a preset version and force a re-seed on
deploy.
```

**Replace:**

```
**Re-seeding is handled.** `seed_role_presets` runs on every boot from
`api/main.py` and upserts `ON CONFLICT (name) WHERE company_id IS NULL DO
UPDATE`, refreshing scope, permissions, grants and viewer_grants. A company
seeded before v2.0 therefore has its Vehicle Inspector row corrected on
deploy — the stale-permission-blob problem this section warned about was fixed
when the seeder moved from DO NOTHING to DO UPDATE. `PRESET_VERSION` is 2 and
logged at boot.

Rows are updated, never deleted: `membership_roles` references role ids, so
dropping a retired preset would orphan live memberships. **Retire a preset by
emptying its permissions, not by removing it from `ROLE_PRESETS`.**
```

The paragraph that follows in §7.1 — inspectors losing maintenance access at
re-seed, confirmed harmless because nothing is deployed — is unchanged and
still stands.

---

## Not changed, and why

- **§7.4 item 3** is already struck through with "Already works." Correct as
  written — the accept route needed nothing.
- **§10.** Accurate. The assignment UI is still out, `test_role_matrix.py`
  asserts no preset is assigned-scope, and email delivery is still absent —
  which is what the §2.3 design depends on.

---

## After applying

The branch has no known deploy blockers. Remaining bookkeeping:

1. Decide 200 vs 201 (Edit 6).
2. Replace the v1.1 copy of this spec in project knowledge with v2.2 — the
   stale copy describes eight roles, `export` instead of `admin`, and Project
   Manager as assigned-scope.
3. `git rm --cached api/routers/members.py.bak api/routers/members.py.bak2`.
4. Merge `user-roles-v2` into `main`. `main` is already two commits ahead of
   `origin/main`.
