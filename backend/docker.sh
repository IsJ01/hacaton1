#!/usr/bin/env bash
# Основной бэкенд в обычном Docker (плагин compose не нужен), сеть хоста — как у эмулятора.
#   bash docker.sh        — собрать и запустить: Swagger http://localhost:8001/docs
#   bash docker.sh logs   — логи
#   bash docker.sh stop   — остановить
#   bash docker.sh reset  — остановить и стереть накопленные данные (папка data/)
# Модель и библиотека звуков берутся из ../ml (подключаются папками, без пересборки).
set -e
cd "$(dirname "$0")"

IMAGE=eco-backend
NAME=eco-backend
PORT="${PORT:-8001}"

case "${1:-up}" in
  up)
    docker build -t "$IMAGE" .
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    # мок из mock-backend занимает тот же порт 8001 — останавливаем его
    if docker rm -f eco-mock-backend >/dev/null 2>&1; then echo "Мок бэкенда остановлен (порт $PORT нужен основному)."; fi
    mkdir -p data
    docker run -d --name "$NAME" --network host --user "$(id -u):$(id -g)" \
      -v "$PWD/data:/app/data" \
      -v "$PWD/../ml/models:/ml/models:ro" \
      -v "$PWD/../ml/data:/ml/data:ro" \
      "$IMAGE" uvicorn main:app --host 0.0.0.0 --port "$PORT" >/dev/null
    echo "Основной бэкенд: http://localhost:$PORT/docs   (логи: bash docker.sh logs)"
    ;;
  logs)
    docker logs -f "$NAME"
    ;;
  stop)
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    echo "Остановлено."
    ;;
  reset)
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    rm -rf data
    echo "Остановлено, данные стёрты."
    ;;
  *)
    echo "Использование: bash docker.sh [up|logs|stop|reset]"; exit 1
    ;;
esac
