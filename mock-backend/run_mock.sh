#!/usr/bin/env bash
# Мок основного бэкенда на :8001 — чтобы проверять эмулятор, пока настоящего бэкенда нет.
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
exec uvicorn main:app --host 0.0.0.0 --port "${MOCK_PORT:-8001}"
