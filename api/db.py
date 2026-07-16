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
    if config.IS_PRODUCTION:
        raise RuntimeError(
            "DATABASE_URL must be set in production "
            "(bind a Managed Postgres in the App Platform console)")
    import pgserver  # local dev only

    server = pgserver.get_server(str(config.PROJECT_ROOT / ".pgdata"))
    return server.get_uri()


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            _database_url(), min_size=config.POOL_MIN,
            max_size=config.POOL_MAX, open=True,
            kwargs={"row_factory": dict_row},
        )
    return _pool


def run_migrations() -> list:
    """Apply migrations/*.sql in filename order, exactly once each.

    Serialized via a transaction-scoped advisory lock so concurrently booting
    instances can't race (F5); the lock releases automatically at commit."""
    applied = []
    with get_pool().connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(727001)")
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


def cleanup_expired(conn) -> dict:
    """F9: purge rows that can never be used again. Runs at startup; login
    additionally does a per-user sweep. No scheduler needed at this scale."""
    stats = {}
    stats["refresh_tokens"] = conn.execute(
        """DELETE FROM refresh_tokens
           WHERE expires_at < now()
              OR (revoked_at IS NOT NULL
                  AND revoked_at < now() - interval '30 days')""").rowcount
    stats["password_resets"] = conn.execute(
        "DELETE FROM password_resets WHERE expires_at < now()").rowcount
    stats["invitations_expired"] = conn.execute(
        """UPDATE invitations SET status = 'expired'
           WHERE status = 'pending' AND expires_at < now()""").rowcount
    return stats


def cleanup_user_tokens(conn, user_id):
    """Per-user variant, piggybacked on login (cheap, targeted)."""
    conn.execute(
        """DELETE FROM refresh_tokens
           WHERE user_id = %s
             AND (expires_at < now() OR revoked_at IS NOT NULL)""",
        (user_id,))
    conn.execute(
        """DELETE FROM password_resets
           WHERE user_id = %s AND (expires_at < now() OR used_at IS NOT NULL)""",
        (user_id,))


def audit(conn, company_id, user_id, action, subject_type=None, subject_id=None,
          details=None):
    from psycopg.types.json import Jsonb

    from .requestmeta import request_meta
    meta = request_meta.get() or {}
    conn.execute(
        """INSERT INTO audit_log (company_id, user_id, action, subject_type,
                                  subject_id, details, ip, user_agent)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (company_id, user_id, action, subject_type, subject_id,
         Jsonb(details) if details is not None else None,
         meta.get("ip"), meta.get("user_agent")),
    )
