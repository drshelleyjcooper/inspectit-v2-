"""Inspectit API — application entry point.

Startup runs pending migrations and seeds the built-in role presets, so a
fresh database (local pgserver or DO Managed Postgres) self-initializes.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import logging

from . import config
from .accesslog import AccessLogMiddleware
from .bodylimit import BodySizeLimitMiddleware
from .db import cleanup_expired, get_pool, run_migrations
from .presets import seed_role_presets
from .requestmeta import RequestMetaMiddleware

logging.basicConfig(level=logging.INFO, format="%(message)s")
from .routers import (assignments, auth, collections, entities, importer, me,
                      members)


log = logging.getLogger("inspectit")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        run_migrations()
        with get_pool().connection() as conn:
            seed_role_presets(conn)
            cleanup_expired(conn)
        log.info("Database ready")
    except Exception as exc:
        log.error("Startup DB init failed (app will serve, DB routes will error): %s", exc)
    yield


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
app.include_router(entities.router)
app.include_router(assignments.router)
app.include_router(importer.router)
app.include_router(collections.router)


@app.get("/health")
def health():
    result = {"ok": True}
    try:
        with get_pool().connection() as conn:
            conn.execute("SELECT 1")
        result["db"] = "connected"
    except Exception:
        result["db"] = "unavailable"
    return result
