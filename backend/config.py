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

# --- звук ---
ANOMALY_THRESHOLD = float(os.getenv("ANOMALY_THRESHOLD", "0.6"))  # уверенность ниже порога → anomaly

# --- солёность ---
SALINITY_NORM = float(os.getenv("SALINITY_NORM", "12.5"))           # норма, ‰
SALINITY_THRESHOLD = float(os.getenv("SALINITY_THRESHOLD", "2.0"))  # отклонение, с которого считаем сбросом
WATER_COOLDOWN_MIN = float(os.getenv("WATER_COOLDOWN_MIN", "30"))   # не дублировать сброс от одного буя чаще

# --- спутник ---
PLUME_MIN_FRACTION = float(os.getenv("PLUME_MIN_FRACTION", "0.01"))  # доля снимка под шлейфом → сброс
NEAREST_BUOY_KM = float(os.getenv("NEAREST_BUOY_KM", "25"))          # снимок привязываем к бую ближе этого
