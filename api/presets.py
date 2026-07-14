"""The permission matrix constants and the 8 built-in role presets.

Source: Brandon's "User Roles" matrix + decisions logged in BACKEND-SCHEMA.md §3.3.
Presets are seeded with company_id NULL; companies clone/adjust later (custom
roles UI is a later phase — the schema supports it now).
"""

MODULES = [
    "company",                # user/role/company management (admin only)
    "vehicles", "vehicle_inspections", "vehicle_maintenance",
    "vehicle_repairs", "vehicle_warranties",
    "properties", "property_inspections", "property_maintenance",
    "property_repairs", "property_warranties",
    "projects",
]

ACTIONS = ["view", "create", "edit", "delete", "print", "export", "assign"]

_ALL = ["view", "create", "edit", "delete", "print", "export", "assign"]
_MANAGE = ["view", "create", "edit", "print", "export", "assign"]   # no delete
_WORK = ["view", "create", "edit", "print"]                          # inspector work
_VIEW_PRINT = ["view", "print"]

_VEHICLE_MODULES = ["vehicles", "vehicle_inspections", "vehicle_maintenance",
                    "vehicle_repairs", "vehicle_warranties"]
_PROPERTY_MODULES = ["properties", "property_inspections", "property_maintenance",
                     "property_repairs", "property_warranties"]
_ENTITY_MODULES = _VEHICLE_MODULES + _PROPERTY_MODULES + ["projects"]

ROLE_PRESETS = [
    {
        "name": "Company Administrator",
        "scope": "company",
        "permissions": {**{m: list(_ALL) for m in _ENTITY_MODULES},
                        "company": list(_ALL)},
    },
    {
        # Everything except user/role/company management.
        "name": "Manager",
        "scope": "company",
        "permissions": {m: list(_ALL) for m in _ENTITY_MODULES},
    },
    {
        # Vehicle domain; assigns inspectors & maintenance; no delete.
        "name": "Vehicle Manager",
        "scope": "company",
        "permissions": {m: list(_MANAGE) for m in _VEHICLE_MODULES},
    },
    {
        # Property domain + read-only visibility into projects (decided 2026-07-14).
        "name": "Property Manager",
        "scope": "company",
        "permissions": {**{m: list(_MANAGE) for m in _PROPERTY_MODULES},
                        "projects": ["view"]},
    },
    {
        # Assigned projects only (decided 2026-07-14).
        "name": "Project Manager",
        "scope": "assigned",
        "permissions": {"projects": ["view", "create", "edit", "print", "export"],
                        "properties": ["view"]},
    },
    {
        "name": "Vehicle Inspector",
        "scope": "assigned",
        "permissions": {"vehicles": ["view"],
                        "vehicle_inspections": list(_WORK),
                        "vehicle_maintenance": ["view", "edit", "print"]},
    },
    {
        "name": "Property Inspector",
        "scope": "assigned",
        "permissions": {"properties": ["view"],
                        "property_inspections": list(_WORK),
                        "property_maintenance": ["view", "edit", "print"]},
    },
    {
        "name": "Viewer",
        "scope": "company",
        "permissions": {m: list(_VIEW_PRINT) for m in _ENTITY_MODULES},
    },
]


def seed_role_presets(conn) -> int:
    """Insert built-in presets that don't exist yet. Idempotent."""
    from psycopg.types.json import Jsonb
    n = 0
    for preset in ROLE_PRESETS:
        row = conn.execute(
            """INSERT INTO roles (company_id, name, scope, permissions, is_preset)
               VALUES (NULL, %s, %s, %s, true)
               ON CONFLICT (name) WHERE company_id IS NULL DO NOTHING
               RETURNING id""",
            (preset["name"], preset["scope"], Jsonb(preset["permissions"])),
        ).fetchone()
        if row:
            n += 1
    return n
