"""Calendar subscription: per-member settings + the public ICS feed.

  GET    /companies/{cid}/calendar        settings, links, which categories the role allows
  PUT    /companies/{cid}/calendar        save preferences (creates the link on first save)
  POST   /companies/{cid}/calendar/reset  new link; the old one stops working
  DELETE /companies/{cid}/calendar        turn the calendar off (link stops working)
  GET    /cal/{token}.ics                 the feed itself (no login: the link is the secret)

Every feed fetch re-checks membership, roles and the company's access state,
so a removed member or a role that lost a module gets nothing for it. The
feed reads the app's synced collections (app_collections), which is why the
calendar needs Cloud sync on. See api/calendar_feed.py for the rules.
"""
import hashlib
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .. import calendar_feed as cf
from .. import config
from ..db import audit, get_pool
from ..permissions import AuthContext, company_member
from ..ratelimit import RateLimiter
from ..trial import access_state, blocked

router = APIRouter(tags=["calendar"])

cal_limiter = RateLimiter(config.CALENDAR_RATE_LIMIT, config.CALENDAR_RATE_WINDOW_S)

# feed id -> {"at": monotonic, "day": date, "etag": str, "body": str}
_cache = {}


def invalidate(feed_id):
    _cache.pop(str(feed_id), None)


def _live_feed(conn, company_id, user_id):
    return conn.execute(
        """SELECT * FROM calendar_feeds
           WHERE company_id = %s AND user_id = %s AND revoked_at IS NULL""",
        (company_id, user_id)).fetchone()


def _has_data(conn, company_id) -> bool:
    row = conn.execute(
        "SELECT 1 FROM app_collections WHERE company_id = %s AND key = ANY(%s) LIMIT 1",
        (company_id, ["vehicles", "properties"])).fetchone()
    return bool(row)


def _settings(conn, ctx: AuthContext, row) -> dict:
    allowed = cf.allowed_categories(ctx)
    out = {
        "enabled": row is not None,
        "prefs": cf.normalize_prefs(row["prefs"] if row else {}),
        "allowed": [c for c in cf.CATEGORIES if c in allowed],
        "has_data": _has_data(conn, ctx.company_id),
        "links": cf.feed_links(row["id"], row["nonce"]) if row else None,
        "created_at": row["created_at"].isoformat() if row else None,
        "rotated_at": row["rotated_at"].isoformat() if row and row["rotated_at"] else None,
        "last_accessed_at": row["last_accessed_at"].isoformat() if row and row["last_accessed_at"] else None,
    }
    return out


def _no_store(response: Response):
    # The response carries the secret link.
    response.headers["Cache-Control"] = "no-store"


@router.get("/companies/{company_id}/calendar")
def get_calendar(response: Response, ctx: AuthContext = Depends(company_member)):
    _no_store(response)
    with get_pool().connection() as conn:
        return _settings(conn, ctx, _live_feed(conn, ctx.company_id, ctx.user["id"]))


class CalendarIn(BaseModel):
    prefs: Any = None


@router.put("/companies/{company_id}/calendar")
def put_calendar(body: CalendarIn, response: Response,
                 ctx: AuthContext = Depends(company_member)):
    _no_store(response)
    if not cf.allowed_categories(ctx):
        raise HTTPException(403, "Your role doesn't include any calendar reminders.")
    from psycopg.types.json import Jsonb
    prefs = cf.normalize_prefs(body.prefs)
    with get_pool().connection() as conn:
        row = _live_feed(conn, ctx.company_id, ctx.user["id"])
        if row:
            row = conn.execute(
                "UPDATE calendar_feeds SET prefs = %s WHERE id = %s RETURNING *",
                (Jsonb(prefs), row["id"])).fetchone()
            invalidate(row["id"])
        else:
            row = conn.execute(
                """INSERT INTO calendar_feeds (company_id, user_id, nonce, prefs)
                   VALUES (%s, %s, %s, %s) RETURNING *""",
                (ctx.company_id, ctx.user["id"], cf.new_nonce(), Jsonb(prefs))).fetchone()
            audit(conn, ctx.company_id, ctx.user["id"], "create", "calendar_feed",
                  row["id"], None)
        return _settings(conn, ctx, row)


@router.post("/companies/{company_id}/calendar/reset")
def reset_calendar(response: Response, ctx: AuthContext = Depends(company_member)):
    _no_store(response)
    with get_pool().connection() as conn:
        row = _live_feed(conn, ctx.company_id, ctx.user["id"])
        if not row:
            raise HTTPException(404, "Calendar isn't turned on")
        row = conn.execute(
            """UPDATE calendar_feeds SET nonce = %s, rotated_at = now(),
                      last_accessed_at = NULL
               WHERE id = %s RETURNING *""",
            (cf.new_nonce(), row["id"])).fetchone()
        invalidate(row["id"])
        audit(conn, ctx.company_id, ctx.user["id"], "update", "calendar_feed",
              row["id"], {"reset": True})
        return _settings(conn, ctx, row)


@router.delete("/companies/{company_id}/calendar")
def delete_calendar(ctx: AuthContext = Depends(company_member)):
    with get_pool().connection() as conn:
        row = conn.execute(
            """UPDATE calendar_feeds SET revoked_at = now()
               WHERE company_id = %s AND user_id = %s AND revoked_at IS NULL
               RETURNING id""",
            (ctx.company_id, ctx.user["id"])).fetchone()
        if row:
            invalidate(row["id"])
            audit(conn, ctx.company_id, ctx.user["id"], "delete", "calendar_feed",
                  row["id"], None)
    return {"ok": True}


# ------------------------------------------------------------ the feed ----

_FEED_HEADERS = {
    "Cache-Control": "private, max-age=900",
    "X-Robots-Tag": "noindex, nofollow",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def _member_ctx(conn, company_id, user_id) -> Optional[AuthContext]:
    user = conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
    if not user or user.get("disabled_at"):
        return None
    m = conn.execute(
        """SELECT id FROM memberships
           WHERE company_id = %s AND user_id = %s AND status = 'active'
             AND deleted_at IS NULL""", (company_id, user_id)).fetchone()
    if not m:
        return None
    roles = conn.execute(
        """SELECT r.id, r.name, r.scope, r.permissions, r.grants, r.viewer_grants
           FROM membership_roles mr JOIN roles r ON r.id = mr.role_id
           WHERE mr.membership_id = %s AND r.deleted_at IS NULL""",
        (m["id"],)).fetchall()
    return AuthContext(user=user, company_id=str(company_id),
                       membership_id=str(m["id"]), roles=roles)


def _not_found():
    raise HTTPException(404, "Calendar not found", headers=dict(_FEED_HEADERS))


@router.api_route("/cal/{name}", methods=["GET", "HEAD"], include_in_schema=False)
def feed(name: str, request: Request):
    token = name[:-4] if name.endswith(".ics") else name
    parsed = cf.parse_token(token)
    if not parsed:
        _not_found()
    feed_id, mac = parsed
    cal_limiter.check("cal:" + str(feed_id))
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT * FROM calendar_feeds WHERE id = %s AND revoked_at IS NULL",
            (feed_id,)).fetchone()
        if not row or not cf.token_matches(row["id"], row["nonce"], mac):
            _not_found()
        co = conn.execute(
            """SELECT trial_ends_at, subscribed_at, deleted_at, complimentary
               FROM companies WHERE id = %s""", (row["company_id"],)).fetchone()
        if not co or co["deleted_at"] is not None:
            _not_found()
        ctx = _member_ctx(conn, row["company_id"], row["user_id"])
        if ctx is None:
            _not_found()
        conn.execute(
            """UPDATE calendar_feeds SET last_accessed_at = now()
               WHERE id = %s AND (last_accessed_at IS NULL
                                  OR last_accessed_at < now() - interval '10 minutes')""",
            (row["id"],))
        paused = blocked(access_state(co["trial_ends_at"], co["subscribed_at"],
                                      complimentary=co["complimentary"]))
        prefs = cf.normalize_prefs(row["prefs"])
        today = cf.today_in(prefs["tz"])
        key = str(row["id"])
        hit = _cache.get(key)
        if hit and hit["day"] == today and hit["paused"] == paused \
                and time.monotonic() - hit["at"] < config.CALENDAR_CACHE_S:
            body, etag = hit["body"], hit["etag"]
        else:
            body = _build(conn, row, ctx, prefs, today, paused)
            etag = '"' + hashlib.sha256(body.encode()).hexdigest()[:32] + '"'
            _cache[key] = {"at": time.monotonic(), "day": today, "paused": paused,
                           "etag": etag, "body": body}
    headers = dict(_FEED_HEADERS, ETag=etag)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    headers["Content-Disposition"] = 'inline; filename="inspectit.ics"'
    return Response(content=body if request.method == "GET" else b"",
                    media_type="text/calendar; charset=utf-8", headers=headers)


def _build(conn, row, ctx, prefs, today, paused) -> str:
    allowed = cf.allowed_categories(ctx)
    cats = {c: m for c, m in allowed.items() if prefs["categories"].get(c)}
    # DTSTAMP = newest source change (not the feed row, whose updated_at moves
    # with every fetch), so an unchanged calendar is byte-identical between
    # rebuilds and the ETag lets pollers get a 304.
    stamp = row["rotated_at"] or row["created_at"]
    events = []
    if cats and not paused:
        rows = conn.execute(
            """SELECT key, data, updated_at FROM app_collections
               WHERE company_id = %s AND key = ANY(%s)""",
            (row["company_id"], cf.SOURCE_KEYS)).fetchall()
        data = {r["key"]: r["data"] for r in rows}
        for r in rows:
            stamp = max(stamp, r["updated_at"])
        items = cf.collect_items(data, cats, prefs, today)
        events = cf.build_events(items, prefs, today, row["company_id"])
    return cf.render_ics(events, stamp, paused=paused)
