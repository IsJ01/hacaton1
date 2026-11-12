#!/usr/bin/env bash
# Запуск: ./run.sh  → Swagger на http://localhost:8000/docs
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
