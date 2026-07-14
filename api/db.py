"""Database access: connection pool + a minimal SQL-file migration runner.

If DATABASE_URL is unset (local dev), an embedded PostgreSQL is started via
pgserver with its data directory at ./.pgdata — no Postgres install needed.
"""
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import config

MIGRATIONS_DIR = config.PROJECT_ROOT / "migrations"

_pool = None


def _database_url() -> str:
    if config.DATABASE_URL:
        return config.DATABASE_URL
    import pgserver  # local dev only

    server = pgserver.get_server(str(config.PROJECT_ROOT / ".pgdata"))
    return server.get_uri()


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            _database_url(), min_size=1, max_size=10, open=True,
            kwargs={"row_factory": dict_row},
        )
    return _pool


def run_migrations() -> list:
    """Apply migrations/*.sql in filename order, exactly once each."""
    applied = []
    with get_pool().connection() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                 filename text PRIMARY KEY,
                 applied_at timestamptz NOT NULL DEFAULT now()
               )"""
        )
        done = {r["filename"] for r in
                conn.execute("SELECT filename FROM schema_migrations").fetchall()}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migrations (filename) VALUES (%s)",
                         (path.name,))
            applied.append(path.name)
    return applied


def audit(conn, company_id, user_id, action, subject_type=None, subject_id=None,
          details=None):
    from psycopg.types.json import Jsonb
    conn.execute(
        """INSERT INTO audit_log (company_id, user_id, action, subject_type,
                                  subject_id, details)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (company_id, user_id, action, subject_type, subject_id,
         Jsonb(details) if details is not None else None),
    )
