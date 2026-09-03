"""The §4.1 grid, asserted cell by cell. No database, no fixtures.

Why this file exists: the old per-role permission tests were mostly negative —
"this role is denied that endpoint". Negative assertions survive almost any
change, including ones that break the product. "Vehicle Inspector cannot delete
a vehicle" still passes if the inspector loses every module in the system.

Two v2.0 changes are exactly that shape: Vehicle Inspector loses
vehicle_maintenance, Manager loses delete. Both NARROW a role, so the old tests
go green while telling you nothing.

So: the expected grid is written out below by hand, from USER-ROLES-SPEC v2.0
§4.1, and asserted in BOTH directions. It is deliberately NOT imported from
presets.py — a test that derives its expectation from the code under test
proves only that the code equals itself. When this file and presets.py
disagree, check the spec to see which one is wrong.
"""
import pytest

from api.presets import ACTIONS, MODULES, ROLE_PRESETS

# Shorthand for the table below.
FULL = "view create edit delete print assign".split()
MANAGE = "view create edit print assign".split()
WORK = "view create edit print".split()
READ = ["view", "print"]
SEE = ["view"]

VEH_TOOLS = ["vehicle_inspections", "vehicle_maintenance",
             "vehicle_repairs", "vehicle_warranties"]
PROP_TOOLS = ["property_inspections", "property_maintenance",
              "property_repairs", "property_warranties"]


def _spread(mods, acts):
    return {m: list(acts) for m in mods}


# --- the expected grid, transcribed from the spec ---------------------------

EXPECTED = {
    "Company Administrator": {
        "scope": "company",
        "perms": {"vehicles": FULL, **_spread(VEH_TOOLS, FULL),
                  "properties": FULL, **_spread(PROP_TOOLS, FULL),
                  "projects": FULL,
                  "company": FULL + ["admin"]},
    },
    "Manager": {
        "scope": "company",
        "perms": {"vehicles": MANAGE, **_spread(VEH_TOOLS, MANAGE),
                  "properties": MANAGE, **_spread(PROP_TOOLS, MANAGE),
                  "projects": MANAGE,
                  "company": ["view", "edit", "print", "assign", "admin"]},
    },
    "Vehicle Manager": {
        "scope": "company",
        "perms": {"vehicles": MANAGE, **_spread(VEH_TOOLS, MANAGE),
                  "company": ["assign"]},
    },
    "Property Manager": {
        "scope": "company",
        "perms": {"properties": MANAGE, **_spread(PROP_TOOLS, MANAGE),
                  "projects": MANAGE, "company": ["assign"]},
    },
    "Project Manager": {
        "scope": "company",
        "perms": {"projects": MANAGE, "properties": SEE,
                  "company": ["assign"]},
    },
    "Vehicle Inspector": {
        "scope": "company",
        "perms": {"vehicles": SEE, "vehicle_inspections": WORK},
    },
    "Property Inspector": {
        "scope": "company",
        "perms": {"properties": SEE, "property_inspections": WORK},
    },
    "Vehicle Maintenance": {
        "scope": "company",
        "perms": {"vehicles": SEE, "vehicle_maintenance": WORK},
    },
    "Property Maintenance": {
        "scope": "company",
        "perms": {"properties": SEE, "property_maintenance": WORK},
    },
    "Vehicle Viewer": {
        "scope": "company",
        "perms": {"vehicles": READ, **_spread(VEH_TOOLS, READ)},
    },
    "Property Viewer": {
        "scope": "company",
        "perms": {"properties": READ, **_spread(PROP_TOOLS, READ)},
    },
    "Project Viewer": {
        "scope": "company",
        "perms": {"projects": READ, "properties": SEE},
    },
    "Viewer": {
        "scope": "company",
        "perms": {m: READ for m in MODULES if m != "company"},
    },
}

BY_NAME = {p["name"]: p for p in ROLE_PRESETS}

# Every cell of the grid: 13 roles x 12 modules x 7 actions.
CELLS = [(r, m, a) for r in EXPECTED for m in MODULES for a in ACTIONS]


def test_preset_names_match_the_spec():
    assert set(BY_NAME) == set(EXPECTED)
    assert len(ROLE_PRESETS) == 13


@pytest.mark.parametrize("role,module,action", CELLS,
                         ids=lambda v: str(v).replace(" ", ""))
def test_every_cell(role, module, action):
    """Granted cells reachable, ungranted cells refused — both directions.

    This is what makes narrowing a role fail loudly instead of silently.
    """
    want = action in EXPECTED[role]["perms"].get(module, [])
    got = action in BY_NAME[role]["permissions"].get(module, [])
    assert got is want, (
        f"{role} / {module}:{action} — spec says "
        f"{'granted' if want else 'denied'}, presets.py says "
        f"{'granted' if got else 'denied'}")


@pytest.mark.parametrize("role", sorted(EXPECTED))
def test_scope(role):
    assert BY_NAME[role]["scope"] == EXPECTED[role]["scope"]


def test_export_action_is_gone():
    """Removed 2026-08-31; company:admin replaced it."""
    assert "export" not in ACTIONS
    for p in ROLE_PRESETS:
        for acts in p["permissions"].values():
            assert "export" not in acts, p["name"]


def test_delete_is_administrator_only():
    for p in ROLE_PRESETS:
        has_delete = any("delete" in a for a in p["permissions"].values())
        assert has_delete == (p["name"] == "Company Administrator"), p["name"]


def test_company_admin_action_gates_backup():
    """Only Company Administrator and Manager reach data egress."""
    holders = {p["name"] for p in ROLE_PRESETS
               if "admin" in p["permissions"].get("company", [])}
    assert holders == {"Company Administrator", "Manager"}


def test_inspectors_have_no_maintenance():
    """Superseded 2026-08-31 — separate roles now. The regression this file
    exists to catch runs the other way, but pin it explicitly too."""
    assert "vehicle_maintenance" not in BY_NAME["Vehicle Inspector"]["permissions"]
    assert "property_maintenance" not in BY_NAME["Property Inspector"]["permissions"]


def test_no_preset_is_assigned_scope():
    """Assigned-scope roles can't use collection sync at all
    (BACKEND-ANALYSIS §3), so they sign in to an empty screen. Every preset is
    company-scoped as of 2026-08-31 -- the inspectors first, then the two
    project roles. Assigned scope returns when the per-record API lands; until
    then a preset that sets it is a bug, not a feature."""
    for p in ROLE_PRESETS:
        assert p["scope"] == "company", p["name"]


def test_repairs_and_warranties_are_manager_level():
    """Nobody below a domain manager creates or edits them; viewers still read."""
    for mod in ("vehicle_repairs", "vehicle_warranties",
                "property_repairs", "property_warranties"):
        for p in ROLE_PRESETS:
            acts = set(p["permissions"].get(mod, []))
            if {"create", "edit"} & acts:
                assert p["name"] in ("Company Administrator", "Manager",
                                     "Vehicle Manager", "Property Manager"), \
                    f"{p['name']} should not write {mod}"


# --- grants (§4.2) ----------------------------------------------------------

EXPECTED_GRANTS = {
    "Company Administrator": ("all", []),
    "Manager": ("all_but_admin_manager_viewer", []),
    "Vehicle Manager": (["Vehicle Inspector", "Vehicle Maintenance"],
                        ["Vehicle Viewer"]),
    "Property Manager": (["Property Inspector", "Property Maintenance"],
                         ["Property Viewer"]),
    "Project Manager": ([], ["Project Viewer"]),
    "Vehicle Inspector": ([], []),
    "Property Inspector": ([], []),
    "Vehicle Maintenance": ([], []),
    "Property Maintenance": ([], []),
    "Vehicle Viewer": ([], []),
    "Property Viewer": ([], []),
    "Project Viewer": ([], []),
    "Viewer": ([], []),
}


@pytest.mark.parametrize("role", sorted(EXPECTED_GRANTS))
def test_grants(role):
    want, want_viewer = EXPECTED_GRANTS[role]
    got = set(BY_NAME[role]["grants"])
    if want == "all":
        assert got == set(EXPECTED)
    elif want == "all_but_admin_manager_viewer":
        assert got == set(EXPECTED) - {"Company Administrator", "Manager",
                                       "Viewer"}
    else:
        assert got == set(want)
    assert set(BY_NAME[role]["viewer_grants"]) == set(want_viewer)


def test_only_the_administrator_issues_the_company_viewer():
    """A domain manager issuing the company-wide Viewer would grant read across
    every domain through the back door."""
    for p in ROLE_PRESETS:
        if "Viewer" in p["grants"]:
            assert p["name"] == "Company Administrator", p["name"]


def test_domain_managers_cannot_cross_domains():
    assert "Property Inspector" not in BY_NAME["Vehicle Manager"]["grants"]
    assert "Vehicle Inspector" not in BY_NAME["Property Manager"]["grants"]


def test_nobody_can_issue_their_own_role():
    """A role that grants itself is a peer-creation path around the rank rule.
    Company Administrator is the deliberate exception."""
    for p in ROLE_PRESETS:
        if p["name"] != "Company Administrator":
            assert p["name"] not in p["grants"], p["name"]


def test_grant_targets_all_exist():
    names = set(EXPECTED)
    for p in ROLE_PRESETS:
        assert set(p["grants"]) <= names, p["name"]
        assert set(p["viewer_grants"]) <= names, p["name"]
