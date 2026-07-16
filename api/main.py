"""Inspectit API — application entry point.

Startup runs pending migrations and seeds the built-in role presets, so a
fresh database (local pgserver or DO Managed Postgres) self-initializes.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config
from .bodylimit import BodySizeLimitMiddleware
from .db import get_pool, run_migrations
from .presets import seed_role_presets
from .routers import (assignments, auth, collections, entities, importer, me,
                      members)


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_migrations()
    with get_pool().connection() as conn:
        seed_role_presets(conn)
    yield


app = FastAPI(title="Inspectit API", version="0.1.0", lifespan=lifespan)

# Body limit first, CORS second: middleware added later wraps earlier, so
# CORS ends up outermost and 413/411 rejections still carry CORS headers
# (a browser would otherwise mask them as opaque network errors).
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

app.include_router(auth.router)
app.include_router(me.router)
app.include_router(members.router)
app.include_router(entities.router)
app.include_router(assignments.router)
app.include_router(importer.router)
app.include_router(collections.router)


@app.get("/health")
def health():
    with get_pool().connection() as conn:
        conn.execute("SELECT 1")
    return {"ok": True}
