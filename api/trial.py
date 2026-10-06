"""Free-trial lifecycle: access state, reminder + ended emails, background loop.

Access state per company (see migrations/006, 007):
    complimentary  test/comped company set by a platform admin: never restricted
    none        no trial on record (existing companies): never restricted
    trialing    trial_ends_at in the future
    grace       trial over, within TRIAL_GRACE_DAYS: full access, nudged to subscribe
    subscribed  subscribed_at set (paid): always allowed
    suspended   grace over, records kept DATA_RETENTION_DAYS: company routes answer 402
    expired     retention over: still 402; records are due for deletion

Emails are exactly-once across worker processes: each cycle first *claims* the
companies it will email with an atomic UPDATE ... RETURNING on the marker
column, so two workers (or a restart) can't both send. If every send for a
company fails, the claim is released so the next cycle retries.
"""
import asyncio
import datetime as dt
import logging

from . import config, mailer
from .db import audit, get_pool

log = logging.getLogger("inspectit")


def timeline(trial_ends_at):
    """Key dates derived from the trial end."""
    grace_end = trial_ends_at + dt.timedelta(days=config.TRIAL_GRACE_DAYS)
    delete_at = grace_end + dt.timedelta(days=config.DATA_RETENTION_DAYS)
    return {"grace_ends_at": grace_end, "delete_at": delete_at}


def access_state(trial_ends_at, subscribed_at, now=None,
                 complimentary=False) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    if complimentary:
        return "complimentary"
    if subscribed_at is not None:
        return "subscribed"
    if trial_ends_at is None:
        return "none"
    if now < trial_ends_at:
        return "trialing"
    t = timeline(trial_ends_at)
    if now < t["grace_ends_at"]:
        return "grace"
    return "suspended" if now < t["delete_at"] else "expired"


def blocked(state: str) -> bool:
    return state in ("suspended", "expired")


def _admin_recipients(conn, company_id) -> list:
    """Active members holding company:admin (Company Administrator, Manager)."""
    rows = conn.execute(
        """SELECT DISTINCT u.email, u.name
           FROM memberships m
           JOIN users u ON u.id = m.user_id AND u.disabled_at IS NULL
           JOIN membership_roles mr ON mr.membership_id = m.id
           JOIN roles r ON r.id = mr.role_id AND r.deleted_at IS NULL
           WHERE m.company_id = %s AND m.status = 'active' AND m.deleted_at IS NULL
             AND r.permissions->'company' ? 'admin'""",
        (company_id,)).fetchall()
    return rows


def _notify(conn, claimed, build, marker_column) -> int:
    """Email each claimed company's admins; release the claim if none got it."""
    sent_companies = 0
    for c in claimed:
        recipients = _admin_recipients(conn, c["id"])
        ok = 0
        for r in recipients:
            subject, body = build(r["name"], c)
            if mailer.send_mail(subject, body, None, r["email"]):
                ok += 1
        if recipients and not ok:
            conn.execute(f"UPDATE companies SET {marker_column} = NULL WHERE id = %s",
                         (c["id"],))
            log.warning("trial email failed for company %s; will retry", c["id"])
        else:
            sent_companies += 1
    return sent_companies


def run_trial_cycle() -> dict:
    """One pass. Safe to call from any worker, any number of times."""
    if not mailer.enabled():
        log.info("trial cycle skipped: email is not configured")
        return {"skipped": True}
    link = f"{config.APP_BASE_URL}/#pricing"
    with get_pool().connection() as conn:
        reminders = conn.execute(
            f"""UPDATE companies SET trial_reminder_sent_at = now()
                WHERE deleted_at IS NULL AND subscribed_at IS NULL AND NOT complimentary
                  AND trial_ends_at IS NOT NULL
                  AND trial_reminder_sent_at IS NULL
                  AND trial_ends_at > now()
                  AND trial_ends_at <= now() + make_interval(days => %s)
                RETURNING id, name, trial_ends_at""",
            (config.TRIAL_REMINDER_DAYS,)).fetchall()
        n_rem = _notify(
            conn, reminders,
            lambda name, c: mailer.trial_reminder_email(name, c["name"], c["trial_ends_at"], link),
            "trial_reminder_sent_at")
        ended = conn.execute(
            """UPDATE companies SET trial_ended_notice_sent_at = now()
               WHERE deleted_at IS NULL AND subscribed_at IS NULL AND NOT complimentary
                 AND trial_ends_at IS NOT NULL
                 AND trial_ended_notice_sent_at IS NULL
                 AND trial_ends_at <= now()
               RETURNING id, name, trial_ends_at""").fetchall()
        n_end = _notify(
            conn, ended,
            lambda name, c: mailer.trial_ended_email(
                name, c["name"], timeline(c["trial_ends_at"])["grace_ends_at"], link),
            "trial_ended_notice_sent_at")
        paused = conn.execute(
            """UPDATE companies SET suspended_notice_sent_at = now()
               WHERE deleted_at IS NULL AND subscribed_at IS NULL AND NOT complimentary
                 AND trial_ends_at IS NOT NULL
                 AND suspended_notice_sent_at IS NULL
                 AND trial_ends_at + make_interval(days => %s) <= now()
               RETURNING id, name, trial_ends_at""",
            (config.TRIAL_GRACE_DAYS,)).fetchall()
        n_pause = _notify(
            conn, paused,
            lambda name, c: mailer.account_paused_email(
                name, c["name"], timeline(c["trial_ends_at"])["delete_at"], link),
            "suspended_notice_sent_at")
        warn_after = config.TRIAL_GRACE_DAYS + config.DATA_RETENTION_DAYS - config.RETENTION_WARNING_DAYS
        warned = conn.execute(
            """UPDATE companies SET deletion_warning_sent_at = now()
               WHERE deleted_at IS NULL AND subscribed_at IS NULL AND NOT complimentary
                 AND trial_ends_at IS NOT NULL
                 AND deletion_warning_sent_at IS NULL
                 AND trial_ends_at + make_interval(days => %s) <= now()
               RETURNING id, name, trial_ends_at""", (warn_after,)).fetchall()
        n_warn = _notify(
            conn, warned,
            lambda name, c: mailer.deletion_warning_email(
                name, c["name"], timeline(c["trial_ends_at"])["delete_at"], link),
            "deletion_warning_sent_at")
        purged = 0
        if config.RETENTION_PURGE_ENABLED:
            # Only companies whose warning email went out at least
            # RETENTION_WARNING_DAYS ago are ever removed, so a customer always
            # gets the full notice even if the server was down or email was off.
            gone = conn.execute(
                """UPDATE companies SET deleted_at = now()
                   WHERE deleted_at IS NULL AND subscribed_at IS NULL AND NOT complimentary
                     AND trial_ends_at IS NOT NULL
                     AND deletion_warning_sent_at IS NOT NULL
                     AND deletion_warning_sent_at <= now() - make_interval(days => %s)
                     AND trial_ends_at + make_interval(days => %s) <= now()
                   RETURNING id, name""",
                (config.RETENTION_WARNING_DAYS,
                 config.TRIAL_GRACE_DAYS + config.DATA_RETENTION_DAYS)).fetchall()
            for g in gone:
                audit(conn, g["id"], None, "delete", "company", g["id"],
                      {"event": "retention_expired"})
                log.warning("retention: company %s (%s) removed", g["id"], g["name"])
            purged = len(gone)
    if reminders or ended or paused or warned or purged:
        log.info("trial cycle: %d reminder(s), %d ended, %d paused, %d warned, %d removed",
                 n_rem, n_end, n_pause, n_warn, purged)
    return {"reminders": n_rem, "ended": n_end, "paused": n_pause,
            "warned": n_warn, "removed": purged}


async def trial_loop():
    """Background task started in the app lifespan. Interval 0 disables it."""
    interval = config.TRIAL_CHECK_INTERVAL_S
    if interval <= 0:
        return
    await asyncio.sleep(30)            # let startup finish first
    while True:
        try:
            await asyncio.to_thread(run_trial_cycle)
        except Exception as exc:       # never let the loop die
            log.error("trial cycle failed: %s", exc)
        await asyncio.sleep(interval)
