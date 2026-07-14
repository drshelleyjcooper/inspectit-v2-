#!/bin/zsh
# Starts the local Inspectit API (dev), then opens the interactive docs page.
cd "$(dirname "$0")"
echo "Starting Inspectit API at http://127.0.0.1:8100 ..."
echo "Docs page: http://127.0.0.1:8100/docs   (press Ctrl+C here to stop)"
( for i in {1..30}; do curl -s -m 1 -o /dev/null http://127.0.0.1:8100/health && open "http://127.0.0.1:8100/docs" && exit; sleep 1; done ) &
exec .venv/bin/python run_dev.py
