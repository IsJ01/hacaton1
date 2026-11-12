#!/usr/bin/env bash
# Запуск фронта: bash run.sh [порт]  →  http://localhost:3000
cd "$(dirname "$0")" || exit 1
PORT="${1:-3000}"
echo "Дашборд: http://localhost:$PORT"
python3 -m http.server "$PORT"
