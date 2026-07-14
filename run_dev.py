"""Local dev server: embedded Postgres + API on http://127.0.0.1:8100
Run with:  .venv/bin/python run_dev.py
"""
import os

os.environ.setdefault("DEV_MODE", "1")

import uvicorn  # noqa: E402

from api.main import app  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8100)
