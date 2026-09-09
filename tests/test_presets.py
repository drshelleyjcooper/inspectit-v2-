"""§9.3: the seeder updates existing preset rows rather than skipping them.

USER-ROLES-SPEC §7.1 called re-seeding a deploy blocker because the seeder
upserted by name and left existing rows alone — the v1.1 clause was
`ON CONFLICT ... DO NOTHING`, so a preset edit would never reach a database
that had already been seeded, and Vehicle Inspector would keep
`vehicle_maintenance` and quietly defeat the §2.3 split.

That was true when §7.1 was written. The step-3 commit (61a007a, 2026-09-03)
closed it by flipping the clause to DO UPDATE SET over scope, permissions,
grants, viewer_grants and updated_at (api/presets.py:202) instead of the
"bump a preset version and force a re-seed" the spec proposed, and §7.1 was
not updated to say so — see the retraction in §11. These tests pin the
behaviour because the failure §7.1 describes would be silent.

Two things the spec's framing gets subtly wrong, kept straight here:

- Presets are global rows (`company_id IS NULL`). There is one set, not one
  per company, so drift would affect every company at once.
- The only uniqueness on `roles` is the partial index `uq_roles_preset_name`
  (migration 001) on `name` WHERE `company_id IS NULL`. A company-owned role
  may share a preset's name, which is what the last test relies on.
"""
import uuid

import pytest

from api.db import get_pool
from api.presets import seed_role_presets

_PRESET_ROW = """SELECT scope, permissions, grants, viewer_grants
                 FROM roles
                 WHERE company_id IS NULL AND name = %s
                   AND deleted_at IS NULL"""


def _preset(conn, name="Vehicle Inspector"):
    return conn.execute(_PRESET_ROW, (name,)).fetchone()


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


def test_reseeding_repairs_a_drifted_preset(client):
    """Corrupt Vehicle Inspector the way a v1.1 row looked, re-seed, assert
    the row is back.

    The drift step is what makes this meaningful. Seeding twice from a clean
    state passes whether the conflict clause updates or skips, because fresh
    inserts were never the problem — the same trap as testing a new company
    instead of an existing one. Verified to fail against DO NOTHING.
    """
    with get_pool().connection() as conn:
        before = _preset(conn)
        assert "vehicle_maintenance" not in before["permissions"], (
            "fixture assumption broken: Vehicle Inspector should not hold "
            "maintenance under v2.0 (§2.3)")

        conn.execute(
            """UPDATE roles
               SET permissions = permissions || '{"vehicle_maintenance":
                   ["view","create","edit","print"]}'::jsonb
               WHERE company_id IS NULL AND name = 'Vehicle Inspector'""")
        conn.commit()
        assert "vehicle_maintenance" in _preset(conn)["permissions"], (
            "the drift step did not take; the rest of this test is vacuous")

        seed_role_presets(conn)
        conn.commit()

        after = _preset(conn)
        assert "vehicle_maintenance" not in after["permissions"], (
            "seeder skipped an existing preset row instead of updating it — "
            "§7.1's blocker would be real")
        assert after["permissions"] == before["permissions"]
        assert after["scope"] == before["scope"]
        assert after["grants"] == before["grants"]


def test_reseeding_a_clean_database_is_a_no_op(client):
    """Second run changes nothing."""
    with get_pool().connection() as conn:
        before = _preset(conn)
        seed_role_presets(conn)
        conn.commit()
        assert _preset(conn) == before


def test_reseeding_refreshes_every_mutable_column(client):
    """The DO UPDATE SET names scope, permissions, grants and viewer_grants.
    Drift all of them at once — a clause that missed one would leave that
    column stale, which is §7.1's bug relocated rather than fixed.
    """
    with get_pool().connection() as conn:
        before = _preset(conn, "Vehicle Manager")

        conn.execute(
            """UPDATE roles
               SET scope = 'assigned',
                   grants = '{}',
                   viewer_grants = '{}',
                   permissions = '{}'::jsonb
               WHERE company_id IS NULL AND name = 'Vehicle Manager'""")
        conn.commit()

        seed_role_presets(conn)
        conn.commit()

        assert _preset(conn, "Vehicle Manager") == before


def test_reseeding_leaves_company_scoped_roles_alone(client, company):
    """The conflict target is (name) WHERE company_id IS NULL, so a custom
    role sharing a preset's name should be immune. Nothing else tests this,
    and there are no custom roles in dev to have revealed a problem."""
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
