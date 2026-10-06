"""Inspectit API — application entry point.

Startup runs pending migrations and seeds the built-in role presets, so a
fresh database (local pgserver or DO Managed Postgres) self-initializes.
"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import logging

from . import config
from .accesslog import AccessLogMiddleware
from .bodylimit import BodySizeLimitMiddleware
from .db import cleanup_expired, get_pool, run_migrations
from .presets import seed_role_presets
from .ratelimit import client_ip
from .requestmeta import RequestMetaMiddleware
from .trial import trial_loop

logging.basicConfig(level=logging.INFO, format="%(message)s")
from .routers import (admin, assignments, auth, calendar, collections,
                      companies, entities, importer, me, members, public)


log = logging.getLogger("inspectit")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        run_migrations()
        with get_pool().connection() as conn:
            seed_role_presets(conn)
            cleanup_expired(conn)
            promoted = admin.promote_platform_admins(conn)
            if promoted:
                log.info("Platform admins: %s", ", ".join(promoted))
        log.info("Database ready")
    except Exception as exc:
        log.error("Startup DB init failed (app will serve, DB routes will error): %s", exc)
    task = asyncio.create_task(trial_loop())     # trial reminders / ended notices
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Inspectit API", version="0.1.0", lifespan=lifespan)

# Middleware order (added first = innermost): request-meta context (F12) and
# body limit sit inside; CORS wraps them so 413/411 rejections still carry
# CORS headers (a browser would otherwise mask them as opaque network
# errors); the access log (F13) is outermost and sees every request.
app.add_middleware(RequestMetaMiddleware)
app.add_middleware(BodySizeLimitMiddleware,
                   max_bytes=config.MAX_BODY_MB * 1024 * 1024)

# The app is served from a different origin (App Platform static site) than
# the API, so CORS is required. Origins come from ALLOWED_ORIGINS; production
# refuses to boot with a wildcard (config.check_production_config).
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(AccessLogMiddleware)

app.include_router(auth.router)
app.include_router(me.router)
app.include_router(members.router)
app.include_router(companies.router)
app.include_router(entities.router)
app.include_router(assignments.router)
app.include_router(importer.router)
app.include_router(collections.router)
app.include_router(admin.router)
app.include_router(public.router)
app.include_router(calendar.router)


@app.get("/health")
def health(request: Request):
    # client_ip is the key the auth rate limiter uses for this caller. If
    # every visitor sees the same value here, the proxy headers are not
    # being resolved correctly and logins will collapse into one bucket.
    result = {"ok": True, "client_ip": client_ip(request)}
    if config._PRODUCTION_PROBLEMS:
        result["config"] = config._PRODUCTION_PROBLEMS
    try:
        with get_pool().connection() as conn:
            conn.execute("SELECT 1")
        result["db"] = "connected"
    except Exception as exc:
        result["db"] = f"unavailable: {exc}"
    return result


# no-cache on the two HTML shells: the deployed files' mtimes are flattened
# to a fixed date by the build (observed: 1980-01-01 in production), which
# makes browsers' own heuristic freshness calculation treat them as
# effectively permanently fresh — independent of the service worker's own
# (separately fixed) caching bug. Static assets under /web otherwise keep
# normal caching; they're versioned by filename/rarely change.
_NO_CACHE = {"Cache-Control": "no-cache"}


# Marketing site (site/): served same-origin so its forms call /auth/trial-signup
# and /public/demo-requests without CORS. The previous landing page (index.html
# at the repo root) is the fallback if site/ is ever absent.
SITE_DIR = config.PROJECT_ROOT / "site"


@app.get("/")
def landing():
    page = SITE_DIR / "index.html"
    if not page.exists():
        page = config.PROJECT_ROOT / "index.html"
    return FileResponse(page, media_type="text/html", headers=_NO_CACHE)


def _account_page(name: str):
    # The reset page carries a secret token in its URL: never let it be cached
    # or leaked through the Referer header or search indexes.
    return FileResponse(SITE_DIR / name, media_type="text/html",
                        headers={**_NO_CACHE, "Referrer-Policy": "no-referrer",
                                 "X-Robots-Tag": "noindex"})


@app.get("/forgot-password")
def forgot_password_page():
    return _account_page("forgot-password.html")


@app.get("/reset-password")
def reset_password_page():
    return _account_page("reset-password.html")


@app.get("/web/inspectit-app.html")
def app_shell():
    return FileResponse(config.PROJECT_ROOT / "web" / "inspectit-app.html",
                        media_type="text/html", headers=_NO_CACHE)


@app.get("/web/admin.html")
def admin_shell():
    return FileResponse(config.PROJECT_ROOT / "web" / "admin.html",
                        media_type="text/html", headers=_NO_CACHE)


app.mount("/web", StaticFiles(directory=config.PROJECT_ROOT / "web", html=True),
          name="web")

# Static assets for the marketing site. Mounted last so the API routes above win.
for _name in ("css", "js", "assets"):
    if (SITE_DIR / _name).is_dir():
        app.mount(f"/{_name}", StaticFiles(directory=SITE_DIR / _name), name=f"site-{_name}")
