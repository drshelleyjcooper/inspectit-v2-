"""Permanent deletion of a company: database rows, uploaded files, and the
members who belonged to nothing else.

This is irreversible, so it is built to fail safe:
  * it refuses unless the caller proves intent (the company is already removed,
    or an admin typed its exact name);
  * it refuses to run if the database has a company-owned table it does not
    know how to clean (so a future table can never be silently left behind);
  * files are deleted before rows, and everything is idempotent, so a failed
    run can simply be retried;
  * the log entry it leaves behind holds no personal data.
"""
import logging

from psycopg import errors
from psycopg.types.json import Jsonb

from . import config
from .db import get_pool
from .storage import get_storage

log = logging.getLogger("inspectit")

# Children first. Every table here has a company_id column.
_DELETE_BY_COMPANY = [
    "project_payments", "project_estimates", "projects", "warranties",
    "maintenance_spend", "maintenance_state", "maintenance_schedules",
    "repair_tickets", "inspections", "inspection_templates", "assignments",
    "vehicles", "properties", "option_lists", "app_collections",
    "calendar_feeds", "events", "invitations",
]
# Handled by hand below (they need special ordering). All have company_id.
_SPECIAL = {"memberships", "roles", "files", "audit_log"}
KNOWN_TABLES = set(_DELETE_BY_COMPANY) | _SPECIAL


class PurgeRefused(Exception):
    """The request was not safe to carry out (status code for the API)."""
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def _tables_with_company_id(conn):
    return {r["table_name"] for r in conn.execute(
        """SELECT table_name FROM information_schema.columns
           WHERE column_name = 'company_id' AND table_schema = 'public'
             AND table_name <> 'purge_log'""").fetchall()}


def _exists(conn, table) -> bool:
    return conn.execute("SELECT to_regclass(%s) AS t", (f"public.{table}",)).fetchone()["t"] is not None


def purge_company(company_id, reason: str, requested_by=None, *,
                  confirm_name: str = None, require_removed: bool = True) -> dict:
    """Delete everything a company owns. Returns a summary.

    require_removed=True  (retention path): the company must already be
        soft-deleted (deleted_at set).
    confirm_name          (admin path): must equal the company name exactly.
    """
    company_id = str(company_id)
    with get_pool().connection() as conn:
        co = conn.execute("SELECT id, name, deleted_at FROM companies WHERE id = %s",
                          (company_id,)).fetchone()
        if not co:
            raise PurgeRefused("Company not found", 404)
        if require_removed and co["deleted_at"] is None:
            raise PurgeRefused("Company has not been removed yet")
        if confirm_name is not None and confirm_name != co["name"]:
            raise PurgeRefused("The name you typed does not match this company", 422)
        unknown = {t for t in _tables_with_company_id(conn) if t not in KNOWN_TABLES
                   and t != "companies"}
        if unknown:
            raise PurgeRefused(
                "Refusing to delete: the database has tables this routine does not "
                "know how to clean: " + ", ".join(sorted(unknown)), 500)

    # 1. Stored files first (idempotent: deleting nothing is fine on a retry).
    files, nbytes = get_storage().delete_company(company_id)

    # 2. Rows, in one transaction.
    rows = {}
    users_deleted = users_kept = 0
    with get_pool().connection() as conn:
        # Everyone who was ever a member (any status) is a candidate to clean up.
        member_ids = [r["user_id"] for r in conn.execute(
            "SELECT DISTINCT user_id FROM memberships WHERE company_id = %s",
            (company_id,)).fetchall()]
        emails = {}
        if member_ids:
            emails = {r["id"]: r["email"] for r in conn.execute(
                "SELECT id, email FROM users WHERE id = ANY(%s)", (member_ids,)).fetchall()}

        for table in _DELETE_BY_COMPANY:
            if _exists(conn, table):
                rows[table] = conn.execute(
                    f"DELETE FROM {table} WHERE company_id = %s", (company_id,)).rowcount
        rows["membership_roles"] = conn.execute(
            """DELETE FROM membership_roles WHERE membership_id IN
               (SELECT id FROM memberships WHERE company_id = %s)""", (company_id,)).rowcount
        rows["memberships"] = conn.execute(
            "DELETE FROM memberships WHERE company_id = %s", (company_id,)).rowcount
        rows["roles"] = conn.execute(
            "DELETE FROM roles WHERE company_id = %s", (company_id,)).rowcount
        # Things that point at this company's files must let go before the files go.
        conn.execute("UPDATE companies SET logo_file_id = NULL WHERE id = %s", (company_id,))
        conn.execute("""UPDATE users SET photo_file_id = NULL WHERE photo_file_id IN
                        (SELECT id FROM files WHERE company_id = %s)""", (company_id,))
        rows["files"] = conn.execute("DELETE FROM files WHERE company_id = %s",
                                     (company_id,)).rowcount
        rows["audit_log"] = conn.execute("DELETE FROM audit_log WHERE company_id = %s",
                                         (company_id,)).rowcount
        rows["companies"] = conn.execute("DELETE FROM companies WHERE id = %s",
                                         (company_id,)).rowcount

        # Members who now belong to nothing at all: remove their accounts too.
        for uid in member_ids:
            u = conn.execute("SELECT is_platform_admin, email FROM users WHERE id = %s",
                             (uid,)).fetchone()
            if not u or u["is_platform_admin"] or \
                    (u["email"] or "").lower() in config.PLATFORM_ADMIN_EMAILS:
                users_kept += 1
                continue
            if conn.execute("SELECT 1 FROM memberships WHERE user_id = %s LIMIT 1",
                            (uid,)).fetchone():
                users_kept += 1                       # still belongs somewhere
                continue
            try:
                with conn.transaction():              # savepoint: a blocked delete keeps the user
                    conn.execute("DELETE FROM refresh_tokens WHERE user_id = %s", (uid,))
                    conn.execute("DELETE FROM password_resets WHERE user_id = %s", (uid,))
                    conn.execute("UPDATE users SET photo_file_id = NULL WHERE id = %s", (uid,))
                    conn.execute("DELETE FROM users WHERE id = %s", (uid,))
                users_deleted += 1
                # Their demo-request, if any, is their personal data too.
                conn.execute("DELETE FROM demo_requests WHERE lower(email) = lower(%s)",
                             (emails.get(uid, ""),))
            except errors.ForeignKeyViolation:
                users_kept += 1

        conn.execute(
            """INSERT INTO purge_log (company_id, reason, requested_by, files_deleted,
                                      bytes_deleted, rows_deleted, users_deleted, users_kept)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (company_id, reason, requested_by, files, nbytes, Jsonb(rows),
             users_deleted, users_kept))
    log.warning("PURGE: company %s deleted (%s): %d file(s), %d user account(s)",
                company_id, reason, files, users_deleted)
    return {"company_id": company_id, "files_deleted": files, "bytes_deleted": nbytes,
            "rows_deleted": rows, "users_deleted": users_deleted, "users_kept": users_kept}
