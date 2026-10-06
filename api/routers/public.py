"""Public (unauthenticated) endpoints used by the marketing website.

    POST /public/demo-requests   "Schedule a Demo" form

Abuse controls: a dedicated per-IP rate limit (tighter than auth), strict
field limits, and a honeypot field that real visitors never see or fill.
Requests are stored for platform admins (/admin/demo-requests), written to the
application log, and emailed to MAIL_TO in a background task (api/mailer.py).
"""
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import config, mailer
from ..db import get_pool
from ..ratelimit import RateLimiter, client_ip

log = logging.getLogger("inspectit")

demo_limiter = RateLimiter(config.DEMO_RATE_LIMIT, config.DEMO_RATE_WINDOW_S)


def rate_limit_demo(request: Request):
    demo_limiter.check(client_ip(request))


router = APIRouter(prefix="/public", tags=["public"],
                   dependencies=[Depends(rate_limit_demo)])


class DemoRequestIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=5, max_length=320)
    count: Optional[int] = Field(default=None, ge=1, le=1_000_000)
    website: Optional[str] = Field(default=None, max_length=200)  # honeypot


def _email(raw: str) -> str:
    email = raw.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(422, "Invalid email address")
    return email


@router.post("/demo-requests", status_code=201)
def create_demo_request(body: DemoRequestIn, request: Request,
                        background: BackgroundTasks):
    email = _email(body.email)
    if body.website:                       # bots fill every field
        return {"ok": True}                # look successful, store nothing
    from ..requestmeta import request_meta
    meta = request_meta.get() or {}
    with get_pool().connection() as conn:
        conn.execute(
            """INSERT INTO demo_requests (name, email, asset_count, ip, user_agent)
               VALUES (%s, %s, %s, %s, %s)""",
            (body.name.strip(), email, body.count, meta.get("ip"),
             (meta.get("user_agent") or "")[:300]))
    subject, text = mailer.demo_request_email(body.name, email, body.count)
    background.add_task(mailer.send_mail, subject, text, email)
    log.info("DEMO REQUEST: %s <%s> (%s vehicles/properties)",
             body.name.strip(), email, body.count)
    return {"ok": True}
