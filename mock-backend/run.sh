#!/usr/bin/env bash
# Эмулятор буя: Swagger на http://localhost:8000/docs, события уходят на BACKEND_URL.
#   bash run.sh
#   BACKEND_URL=http://192.168.1.50:8001 bash run.sh
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
export BACKEND_URL="${BACKEND_URL:-http://localhost:8001}"
exec uvicorn emulator:app --host 0.0.0.0 --port "${PORT:-8000}"
