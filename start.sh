#!/bin/bash
# Start the transcriber from a terminal (uses the project's virtualenv).
cd "$(dirname "$0")"
exec .venv/bin/python app.py
