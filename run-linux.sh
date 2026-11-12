#!/bin/sh
# Start Wardrive Uploader from the venv install-linux.sh made. Arguments pass through (--boot, --detect, ...).
cd "$(dirname "$0")"
exec .venv/bin/python run_app.py "$@"
