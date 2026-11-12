#!/usr/bin/env bash
# Запуск в обычном Docker (плагин compose не нужен). Контейнеры работают в сети хоста,
# поэтому эмулятор видит бэкенд на localhost:8001 так же, как без Docker.
#   bash docker.sh          — эмулятор буя: Swagger http://localhost:8000/docs, шлёт на BACKEND_URL
#   bash docker.sh mock     — мок основного бэкенда на :8001 (для проверки, пока нет настоящего)
#   bash docker.sh logs     — логи эмулятора   (bash docker.sh logs mock — логи мока)
#   bash docker.sh stop     — остановить всё
# Настройки: BACKEND_URL=http://localhost:8001  PORT=8000  MOCK_PORT=8001  AUTO_HISTORY=1 (пустой бэкенд → история за сутки)
#   пример: BACKEND_URL=http://192.168.1.50:8001 bash docker.sh
set -e
cd "$(dirname "$0")"

IMAGE=eco-buoy-emulator
EMU=eco-buoy-emulator
MOCK=eco-mock-backend
PORT="${PORT:-8000}"
MOCK_PORT="${MOCK_PORT:-8001}"
BACKEND_URL="${BACKEND_URL:-http://localhost:8001}"

case "${1:-up}" in
  up)
    docker build -t "$IMAGE" .
    docker rm -f "$EMU" >/dev/null 2>&1 || true
    # sounds/ и sat_library/ подключены папками: новые файлы видны без пересборки
    docker run -d --name "$EMU" --network host \
      -e BACKEND_URL="$BACKEND_URL" -e AUTO_HISTORY="${AUTO_HISTORY:-1}" \
      -v "$PWD/sounds:/app/sounds" -v "$PWD/sat_library:/app/sat_library" \
      "$IMAGE" uvicorn emulator:app --host 0.0.0.0 --port "$PORT" >/dev/null
    echo "Эмулятор буя: http://localhost:$PORT/docs  →  шлёт на $BACKEND_URL"
    ;;
  mock)
    docker build -t "$IMAGE" .
    docker rm -f "$MOCK" >/dev/null 2>&1 || true
    docker run -d --name "$MOCK" --network host \
      -e SEED_EVENTS="${SEED_EVENTS:-20}" \
      "$IMAGE" uvicorn main:app --host 0.0.0.0 --port "$MOCK_PORT" >/dev/null
    echo "Мок бэкенда: http://localhost:$MOCK_PORT/docs"
    ;;
  logs)
    if [ "$2" = "mock" ]; then docker logs -f "$MOCK"; else docker logs -f "$EMU"; fi
    ;;
  stop)
    docker rm -f "$EMU" "$MOCK" eco-mock-backend-emu eco-mock-backend-gateway >/dev/null 2>&1 || true
    docker network rm eco-mock-net >/dev/null 2>&1 || true
    echo "Остановлено."
    ;;
  *)
    echo "Использование: bash docker.sh [up|mock|logs|stop]"; exit 1
    ;;
esac
