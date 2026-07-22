"""POST /companies/{company_id}/events — batched product-analytics ingest.

Mounted under the company prefix like every other tenant-scoped endpoint in
this codebase (collections, assignments, ...), reusing company_member —
not the bare `/events` path the originating brief sketched. Tenant and
actor come from that auth dependency chain, never the request body, so a
client cannot claim to be a different company or user by putting one in
the event JSON.

Fire-and-forget from the client's perspective (the emitter in
web/inspectit-app.html): validation is per-event, not per-batch. A batch
with 49 good events and 1 unknown event_name (e.g. from a slightly stale
client) inserts the 49 and reports the 1 rejected, rather than discarding
everything — matching "a failed emit must never break the app."
"""
import datetime as dt
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .. import config
from ..analytics import EVENT_NAMES, validate_props
from ..db import get_pool
from ..permissions import AuthContext, company_member
from ..ratelimit import events_limiter

router = APIRouter(prefix="/companies/{company_id}", tags=["events"])


class EventIn(BaseModel):
    event_name: str = Field(max_length=64)
    event_props: Dict[str, Any] = Field(default_factory=dict)
    session_id: str = Field(min_length=1, max_length=100)
    occurred_at: dt.datetime


class EventBatchIn(BaseModel):
    events: List[EventIn] = Field(min_length=1, max_length=config.EVENTS_MAX_BATCH)


@router.post("/events")
def ingest_events(body: EventBatchIn,
                  ctx: AuthContext = Depends(company_member)):
    events_limiter.check(str(ctx.user["id"]))

    received_at = dt.datetime.now(dt.timezone.utc)
    to_insert = []
    rejected = []
    for i, ev in enumerate(body.events):
        if ev.event_name not in EVENT_NAMES:
            rejected.append({"index": i, "reason": "unknown event_name"})
            continue
        err = validate_props(ev.event_props)
        if err:
            rejected.append({"index": i, "reason": err})
            continue
        occurred = ev.occurred_at
        if occurred.tzinfo is None:
            occurred = occurred.replace(tzinfo=dt.timezone.utc)
        to_insert.append((ctx.company_id, ctx.user["id"], ev.event_name,
                          Jsonb(ev.event_props), ev.session_id, occurred,
                          received_at))

    if to_insert:
        with get_pool().connection() as conn:
            for row in to_insert:
                conn.execute(
                    """INSERT INTO events (company_id, user_id, event_name,
                                           event_props, session_id,
                                           occurred_at, received_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    row)

    return {"accepted": len(to_insert), "rejected": rejected}
