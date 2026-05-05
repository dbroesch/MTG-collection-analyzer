#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/website"
export DATABASE_URL="postgresql://localhost/mtg_collection"
exec ./venv/bin/python app.py
