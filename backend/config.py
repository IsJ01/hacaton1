"""Все настройки бэкенда. Любую можно переопределить переменной окружения."""
import os
from pathlib import Path

BASE = Path(__file__).parent  # папка backend/
ROOT = BASE.parent            # папка hacaton1/


def _path(env: str, default: Path) -> Path:
    return Path(os.getenv(env, str(default)))


# --- пути ---
MODEL_PATH = _path("MODEL_PATH", ROOT / "ml" / "models" / "classifier.pkl")  # обученная модель звука
LIBRARY_DIR = _path("LIBRARY_DIR", ROOT / "ml" / "data")  # библиотека звуков для /classify и /events без audio
DATA_DIR = _path("DATA_DIR", BASE / "data")               # база SQLite, сохранённые звуки и снимки
BUOYS_FILE = _path("BUOYS_FILE", BASE / "buoys.json")     # список буёв
FORECAST_MODEL_PATH = _path("FORECAST_MODEL_PATH", ROOT / "ml" / "models" / "seal_forecast.pkl")  # модель прогноза

# --- звук ---
ANOMALY_THRESHOLD = float(os.getenv("ANOMALY_THRESHOLD", "0.6"))  # уверенность ниже порога → anomaly

# --- солёность ---
SALINITY_NORM = float(os.getenv("SALINITY_NORM", "12.5"))           # норма, ‰
SALINITY_THRESHOLD = float(os.getenv("SALINITY_THRESHOLD", "2.0"))  # отклонение, с которого считаем сбросом
WATER_COOLDOWN_MIN = float(os.getenv("WATER_COOLDOWN_MIN", "30"))   # не дублировать сброс от одного буя чаще

# --- спутник ---
PLUME_MIN_FRACTION = float(os.getenv("PLUME_MIN_FRACTION", "0.01"))  # доля снимка под шлейфом → сброс
NEAREST_BUOY_KM = float(os.getenv("NEAREST_BUOY_KM", "25"))          # снимок привязываем к бую ближе этого

# --- прогноз ---
USE_WEATHER_API = os.getenv("USE_WEATHER_API", "1") == "1"  # 0 — не ходить в интернет, брать климатическую норму
WEATHER_URL = os.getenv("WEATHER_URL", "https://api.open-meteo.com/v1/forecast")
WEATHER_TIMEOUT_SEC = float(os.getenv("WEATHER_TIMEOUT_SEC", "4"))

# --- уровень воды (телеметрия буя) ---
LEVEL_NORM_M = float(os.getenv("LEVEL_NORM_M", "-28.9"))            # уровень Каспия, м (Балтийская система)
LEVEL_THRESHOLD_M = float(os.getenv("LEVEL_THRESHOLD_M", "0.3"))    # отклонение, с которого → level_anomaly

# --- статус буя и соседи ---
STATUS_WINDOW_H = float(os.getenv("STATUS_WINDOW_H", "24"))         # статус буя считается по событиям за это окно
CRITICAL_LABELS = os.getenv("CRITICAL_LABELS", "gunshot,boat").split(",")  # звуки, после которых буй красный
ANOMALY_SALINITY_DELTA = float(os.getenv("ANOMALY_SALINITY_DELTA", "1.0"))  # отклонение солёности → буй жёлтый
BUOY_ONLINE_MIN = float(os.getenv("BUOY_ONLINE_MIN", "90"))         # буй на связи, если присылал данные за это время
LISTEN_RADIUS_KM = float(os.getenv("LISTEN_RADIUS_KM", "25"))       # радиус слышимости, если в buoys.json не задан
NEIGHBOR_WINDOW_MIN = float(os.getenv("NEIGHBOR_WINDOW_MIN", "10"))  # сосед «слышал то же», если в пределах ± минут

# --- тюлени ---
SEAL_WINDOW_H = float(os.getenv("SEAL_WINDOW_H", "24"))             # окно анализа вокализаций тюленя
SEAL_TRACK_BINS = int(os.getenv("SEAL_TRACK_BINS", "4"))            # на сколько отрезков делим окно для трека дрейфа

# --- демо ---
DEMO_SEED = os.getenv("DEMO_SEED", "1") == "1"  # пустая база при старте → история за сутки (0 — стартовать пустым)
