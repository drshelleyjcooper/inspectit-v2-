"""Test bootstrap: fresh database on the embedded Postgres + isolated file store.

Env is configured at import time — before any `api` module loads — so config
picks up the test values.
"""
import os
import pathlib
import shutil

import pgserver
import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = pathlib.Path(__file__).resolve().parent.parent

_server = pgserver.get_server(str(ROOT / ".pgdata"))
_base_uri = _server.get_uri()

with psycopg.connect(_base_uri, autocommit=True) as _conn:
    _conn.execute("DROP DATABASE IF EXISTS inspectit_test WITH (FORCE)")
    _conn.execute("CREATE DATABASE inspectit_test")

_params = conninfo_to_dict(_base_uri)
_params["dbname"] = "inspectit_test"

_store = ROOT / ".filestore_test"
shutil.rmtree(_store, ignore_errors=True)

os.environ["DATABASE_URL"] = make_conninfo(**_params)
os.environ["DEV_MODE"] = "1"
os.environ["STORAGE_DIR"] = str(_store)


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from api.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def storage_dir():
    return _store
