"""§9.3: the seeder must update existing preset rows, not skip them.

USER-ROLES-SPEC §7.1 called re-seeding a deploy blocker on the premise that
`seed_role_presets` upserts by name and leaves existing rows alone, so a
company seeded under v1.1 would keep `vehicle_maintenance` on Vehicle
Inspector and quietly defeat the §2.3 split. That premise was wrong: the
ON CONFLICT clause is DO UPDATE SET and covers every mutable column. These
tests pin that, since the spec has been wrong about it once already.

The only uniqueness on `roles` is the partial index `uq_roles_preset_name`
(migration 001), which covers `name` WHERE `company_id IS NULL`. A
company-owned role may therefore share a preset's name, which is what the
third test relies on.
"""
import uuid

import pytest

from api.db import get_pool
from api.presets import seed_role_presets


@pytest.fixture
def company(client):
    """A fresh company. Only the id is needed here, so this does not reuse
    the richer fixture of the same name in test_grants.py (the suite has no
    shared fixture module beyond conftest.py)."""
    r = client.post("/auth/signup", json={
        "company_name": "Seed Co", "name": "Admin",
        "email": f"seed-{uuid.uuid4().hex[:8]}@example.com",
        "password": "test-password-123"})
    assert r.status_code == 200, r.text
    return {"id": r.json()["company_id"]}


def _vehicle_inspector(conn):
    return conn.execute(
        """SELECT permissions, scope, grants FROM roles
           WHERE company_id IS NULL AND name = 'Vehicle Inspector'
             AND deleted_at IS NULL"""
    ).fetchone()


def test_reseeding_repairs_a_drifted_preset(client):
    """Corrupt Vehicle Inspector the way a v1.1 row would look, re-seed, and
    assert the row is back. Seeding twice from a clean state would pass
    whether the conflict clause updated or skipped — the drift is what makes
    the assertion mean anything."""
    with get_pool().connection() as conn:
        before = _vehicle_inspector(conn)
        assert "vehicle_maintenance" not in before["permissions"]

        conn.execute(
            """UPDATE roles
               SET permissions = permissions || '{"vehicle_maintenance":
                   ["view","create","edit","print"]}'::jsonb
               WHERE company_id IS NULL AND name = 'Vehicle Inspector'""")
        conn.commit()
        assert "vehicle_maintenance" in _vehicle_inspector(conn)["permissions"]

        seed_role_presets(conn)
        conn.commit()

        after = _vehicle_inspector(conn)
        assert "vehicle_maintenance" not in after["permissions"], (
            "seeder skipped an existing preset row instead of updating it")
        assert after["permissions"] == before["permissions"]


def test_reseeding_a_clean_database_changes_nothing(client):
    """Second run is a no-op."""
    with get_pool().connection() as conn:
        before = _vehicle_inspector(conn)
        seed_role_presets(conn)
        conn.commit()
        assert _vehicle_inspector(conn) == before


def test_reseeding_leaves_company_scoped_roles_alone(client, company):
    """The conflict target is (name) WHERE company_id IS NULL, so a custom
    role sharing a preset's name should be immune. Nothing tests this and
    there are no custom roles in dev to have revealed a problem."""
    with get_pool().connection() as conn:
        conn.execute(
            """INSERT INTO roles (company_id, name, scope, permissions,
                                  grants, viewer_grants, is_preset)
               VALUES (%s, 'Vehicle Inspector', 'company', '{}'::jsonb,
                       '{}', '{}', false)""",
            (company["id"],))
        conn.commit()

        seed_role_presets(conn)
        conn.commit()

        custom = conn.execute(
            """SELECT permissions, is_preset FROM roles
               WHERE company_id = %s AND name = 'Vehicle Inspector'""",
            (company["id"],)).fetchone()
        assert custom["permissions"] == {}
        assert custom["is_preset"] is False
