"""The permission matrix constants and the 13 built-in role presets.
Source: USER-ROLES-SPEC v2.0 §4.1 (permissions) and §4.2 (grants). Where this
file and the spec disagree, the spec is right and this file is a bug.
Presets are seeded with company_id NULL; companies clone/adjust later (custom
roles UI is a later phase — the schema supports it now).

Changed 2026-08-31 (v2.0, was 8 presets):
  - `export` removed from ACTIONS; `admin` on `company` is the data-egress gate
  - inspection and maintenance split into separate roles
  - repairs/warranties raised to manager level
  - grants moved out of rank and onto the role (`grants`, `viewer_grants`)
  - inspectors flipped from 'assigned' to 'company' scope
"""
MODULES = [
    "company",                # user/role/company management + data egress
    "vehicles", "vehicle_inspections", "vehicle_maintenance",
    "vehicle_repairs", "vehicle_warranties",
    "properties", "property_inspections", "property_maintenance",
    "property_repairs", "property_warranties",
    "projects",
]
ACTIONS = ["view", "create", "edit", "delete", "print", "assign", "admin"]
_ALL = ["view", "create", "edit", "delete", "print", "assign"]
_MANAGE = ["view", "create", "edit", "print", "assign"]              # no delete
_WORK = ["view", "create", "edit", "print"]                          # field work
_VIEW_PRINT = ["view", "print"]
_VEHICLE_MODULES = ["vehicles", "vehicle_inspections", "vehicle_maintenance",
                    "vehicle_repairs", "vehicle_warranties"]
_PROPERTY_MODULES = ["properties", "property_inspections", "property_maintenance",
                     "property_repairs", "property_warranties"]
_ENTITY_MODULES = _VEHICLE_MODULES + _PROPERTY_MODULES + ["projects"]

# Role names, referenced by the grants lists below.
ADMIN, MANAGER = "Company Administrator", "Manager"
VEH_MGR, PROP_MGR, PROJ_MGR = "Vehicle Manager", "Property Manager", "Project Manager"
VEH_INSP, PROP_INSP = "Vehicle Inspector", "Property Inspector"
VEH_MAINT, PROP_MAINT = "Vehicle Maintenance", "Property Maintenance"
VEH_VIEW, PROP_VIEW, PROJ_VIEW = "Vehicle Viewer", "Property Viewer", "Project Viewer"
VIEWER = "Viewer"
ALL_ROLES = [ADMIN, MANAGER, VEH_MGR, PROP_MGR, PROJ_MGR, VEH_INSP, PROP_INSP,
             VEH_MAINT, PROP_MAINT, VEH_VIEW, PROP_VIEW, PROJ_VIEW, VIEWER]
             
   # Pairs a DOMAIN manager may not assemble from two narrower grants (§2.3).
# Company Administrator and Manager (company:admin holders) are exempt.
BLOCKED_COMBINATIONS = [
    {VEH_INSP, VEH_MAINT},
    {PROP_INSP, PROP_MAINT},
]          

ROLE_PRESETS = [
    {
        "name": ADMIN,
        "scope": "company",
        "permissions": {**{m: list(_ALL) for m in _ENTITY_MODULES},
                        "company": _ALL + ["admin"]},
        # The only role that may issue ADMIN or the company-wide VIEWER.
        "grants": list(ALL_ROLES),
        "viewer_grants": [],
    },
    {
        # Everything except delete; manages users but not billing.
        "name": MANAGER,
        "scope": "company",
        "permissions": {**{m: list(_MANAGE) for m in _ENTITY_MODULES},
                        "company": ["view", "edit", "print", "assign", "admin"]},
        # Outranks the domain managers, so the §2.3 block doesn't apply: a
        # Manager may deliberately issue inspector + maintenance together.
        # Not VIEWER either: the company-wide viewer is Company Administrator's
        # to issue alone (§2.5). Manager may issue the three domain viewers.
        "grants": [r for r in ALL_ROLES if r not in (ADMIN, MANAGER, VIEWER)],
        "viewer_grants": [],
    },
    {
        # Vehicle domain; assigns inspectors & maintenance; no delete.
        "name": VEH_MGR,
        "scope": "company",
        "permissions": {**{m: list(_MANAGE) for m in _VEHICLE_MODULES},
                        "company": ["assign"]},      # may open the invite form
        "grants": [VEH_INSP, VEH_MAINT],             # domain-limited (§2.2)
        "viewer_grants": [VEH_VIEW],
    },
    {
        # Property domain + full project access (decided 2026-08-29).
        "name": PROP_MGR,
        "scope": "company",
        "permissions": {**{m: list(_MANAGE) for m in _PROPERTY_MODULES},
                        "projects": list(_MANAGE),
                        "company": ["assign"]},
        "grants": [PROP_INSP, PROP_MAINT],
        "viewer_grants": [PROP_VIEW],
    },
    {
        # Assigned projects only (decided 2026-07-14).
        "name": PROJ_MGR,
        # Company scope since 2026-08-31, same reason as the inspectors: the
        # app syncs whole collections, and assigned-scope roles can't use sync
        # at all (BACKEND-ANALYSIS §3). Assigned scope meant an empty screen.
        "scope": "company",
        "permissions": {"projects": list(_MANAGE),
                        "properties": ["view"],
                        "company": ["assign"]},
        # No inspector or maintenance role exists in the projects domain, so
        # this role invites nobody unless an admin sets can_grant_viewers.
        "grants": [],
        "viewer_grants": [PROJ_VIEW],
    },
    {
        # Company scope, not assigned: no assignment screen exists, and
        # assigned-scope roles can't use collection sync at all
        # (BACKEND-ANALYSIS §3) — an assigned inspector signs in to nothing.
        "name": VEH_INSP,
        "scope": "company",
        "permissions": {"vehicles": ["view"],
                        "vehicle_inspections": list(_WORK)},
        # vehicle_maintenance removed 2026-08-31 — separate role now (§2.3).
        "grants": [],
        "viewer_grants": [],
    },
    {
        "name": PROP_INSP,
        "scope": "company",
        "permissions": {"properties": ["view"],
                        "property_inspections": list(_WORK)},
        "grants": [],
        "viewer_grants": [],
    },
    {
        # Maintenance scheduler + spend log. New 2026-08-31.
        "name": VEH_MAINT,
        "scope": "company",
        "permissions": {"vehicles": ["view"],
                        "vehicle_maintenance": list(_WORK)},
        "grants": [],
        "viewer_grants": [],
    },
    {
        "name": PROP_MAINT,
        "scope": "company",
        "permissions": {"properties": ["view"],
                        "property_maintenance": list(_WORK)},
        "grants": [],
        "viewer_grants": [],
    },
    {
        # Reads its whole domain, repairs and warranties included: "manager
        # level only" governs create and edit (§2.4). New 2026-08-31.
        "name": VEH_VIEW,
        "scope": "company",
        "permissions": {m: list(_VIEW_PRINT) for m in _VEHICLE_MODULES},
        "grants": [],
        "viewer_grants": [],
    },
    {
        "name": PROP_VIEW,
        "scope": "company",
        "permissions": {m: list(_VIEW_PRINT) for m in _PROPERTY_MODULES},
        "grants": [],
        "viewer_grants": [],
    },
    {
        # Mirrors Project Manager's scope.
        "name": PROJ_VIEW,
        "scope": "company",
        "permissions": {"projects": list(_VIEW_PRINT),
                        "properties": ["view"]},
        "grants": [],
        "viewer_grants": [],
    },
    {
        # Business data only — no `company`, so no settings or member list.
        "name": VIEWER,
        "scope": "company",
        "permissions": {m: list(_VIEW_PRINT) for m in _ENTITY_MODULES},
        "grants": [],
        "viewer_grants": [],
    },
]

# Bump on any change above. Logged at boot; the tests assert against it.
PRESET_VERSION = 2


def seed_role_presets(conn) -> int:
    """Insert or refresh the built-in presets. Idempotent; runs every boot.

    Was DO NOTHING through v1.1, which meant a company seeded before v2.0 kept
    its old permission blobs forever — including a Vehicle Inspector that still
    held vehicle_maintenance. Now DO UPDATE, so a preset edit lands on deploy
    without a manual re-seed.

    Rows are updated, never deleted: membership_roles references role ids, so
    dropping a retired preset would orphan live memberships. Retire a preset by
    emptying its permissions, not by removing it here.
    """
    from psycopg.types.json import Jsonb
    n = 0
    for preset in ROLE_PRESETS:
        row = conn.execute(
            """INSERT INTO roles (company_id, name, scope, permissions,
                                  grants, viewer_grants, is_preset)
               VALUES (NULL, %s, %s, %s, %s, %s, true)
               ON CONFLICT (name) WHERE company_id IS NULL DO UPDATE SET
                   scope         = EXCLUDED.scope,
                   permissions   = EXCLUDED.permissions,
                   grants        = EXCLUDED.grants,
                   viewer_grants = EXCLUDED.viewer_grants,
                   updated_at    = now()
               RETURNING id""",
            (preset["name"], preset["scope"], Jsonb(preset["permissions"]),
             preset["grants"], preset["viewer_grants"]),
        ).fetchone()
        if row:
            n += 1
    return n
