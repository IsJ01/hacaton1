#!/usr/bin/env bash
# Запуск в обычном Docker, без плагина compose.
#   bash docker.sh         — собрать образ и запустить бэкенд (в фоне) на http://localhost:8000
#   bash docker.sh emu     — то же + эмулятор буёв в этом терминале (Ctrl+C останавливает эмулятор)
#   bash docker.sh logs    — логи бэкенда
#   bash docker.sh stop    — остановить и удалить контейнеры
# Настройки: PORT=8000 SEED_EVENTS=20 AUTO_EMIT_SEC=0 SALINITY_NORM=12.5 SALINITY_THRESHOLD=2.0
#   пример: AUTO_EMIT_SEC=5 bash docker.sh
set -e
cd "$(dirname "$0")"

IMAGE=eco-mock
NAME=eco-mock-backend
NET=eco-mock-net
PORT="${PORT:-8000}"

stop() {
  docker rm -f "$NAME" "$NAME-emu" >/dev/null 2>&1 || true
}

start_backend() {
  docker build -t "$IMAGE" .
  stop
  docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null
  docker run -d --name "$NAME" --network "$NET" -p "$PORT:8000" \
    -e SEED_EVENTS="${SEED_EVENTS:-20}" \
    -e AUTO_EMIT_SEC="${AUTO_EMIT_SEC:-0}" \
    -e SALINITY_NORM="${SALINITY_NORM:-12.5}" \
    -e SALINITY_THRESHOLD="${SALINITY_THRESHOLD:-2.0}" \
    "$IMAGE" >/dev/null
  echo "Бэкенд запущен: http://localhost:$PORT/docs   (остановить: bash docker.sh stop)"
}

case "${1:-up}" in
  up)
    start_backend
    ;;
  emu)
    start_backend
    echo "Эмулятор буёв (Ctrl+C — остановить эмулятор, бэкенд продолжит работать):"
    docker run --rm -it --name "$NAME-emu" --network "$NET" "$IMAGE" \
      python buoy_emulator.py --url "http://$NAME:8000" --audio --interval 3
    ;;
  logs)
    docker logs -f "$NAME"
    ;;
  stop)
    stop
    docker network rm "$NET" >/dev/null 2>&1 || true
    echo "Остановлено."
    ;;
  *)
    echo "Использование: bash docker.sh [up|emu|logs|stop]"; exit 1
    ;;
esac
