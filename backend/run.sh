#!/usr/bin/env bash
# Основной бэкенд без Docker: виртуальное окружение + uvicorn на :8001.
# Нужен Python 3.10–3.12 (под эти версии закреплены библиотеки модели).
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8001}"
