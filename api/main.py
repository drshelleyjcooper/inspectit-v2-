"""Inspectit API — application entry point.

Startup runs pending migrations and seeds the built-in role presets, so a
fresh database (local pgserver or DO Managed Postgres) self-initializes.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

# The app is served from a different origin (App Platform static site) than
# the API, so CORS is required. Tighten allow_origins to the real domain at
# deploy time.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
