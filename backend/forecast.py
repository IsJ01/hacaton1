"""Предиктивный модуль: прогноз присутствия каспийского тюленя у каждого буя на ближайшие дни.

Модель обучается скриптом ml/forecast/train_forecast.py (сейчас — на синтетических данных)
и лежит в ml/models/seal_forecast.pkl. Признаки на каждый день:
  день года, широта буя, прогноз погоды (температура, ветер),
  сколько раз буй слышал тюленя за 7 дней, сколько было лодок и выстрелов за 7 дней.

Погода берётся из открытого API Open-Meteo. Если интернета нет — из климатической нормы по месяцам.
"""
import json
import math
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import joblib

from config import FORECAST_MODEL_PATH, USE_WEATHER_API, WEATHER_TIMEOUT_SEC, WEATHER_URL

_bundle: Optional[dict] = None
_weather_cache: dict = {}  # (lat, lon, сегодня) -> (время запроса, [(температура, ветер), ...])
WEATHER_CACHE_SEC = 3600


def load_model() -> bool:
    global _bundle
    if not FORECAST_MODEL_PATH.is_file():
        print(f"Модель прогноза не найдена: {FORECAST_MODEL_PATH} — /api/v1/forecast будет отвечать 503")
        return False
    _bundle = joblib.load(str(FORECAST_MODEL_PATH))
    return True


def is_ready() -> bool:
    return _bundle is not None


def _climate(day: date) -> tuple[float, float]:
    return float(_bundle["climate_temp_by_month"][day.month - 1]), float(_bundle["climate_wind_ms"])


def _fetch_weather(lat: float, lon: float, days: int) -> Optional[list[tuple[float, float]]]:
    """Прогноз Open-Meteo по дням, начиная с сегодня: [(температура °C, макс. ветер м/с), ...] или None."""
    key = (round(lat, 2), round(lon, 2), date.today().isoformat())
    cached = _weather_cache.get(key)
    if cached and time.time() - cached[0] < WEATHER_CACHE_SEC and len(cached[1]) >= days:
        return cached[1][:days]
    query = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon,
        "daily": "temperature_2m_mean,wind_speed_10m_max",
        "wind_speed_unit": "ms", "forecast_days": 7, "timezone": "UTC",
    })
    try:
        with urllib.request.urlopen(f"{WEATHER_URL}?{query}", timeout=WEATHER_TIMEOUT_SEC) as resp:
            daily = json.load(resp)["daily"]
        rows = [(float(t), float(w)) for t, w in zip(daily["temperature_2m_mean"], daily["wind_speed_10m_max"])]
    except Exception:
        return None  # нет сети, таймаут, неожиданный ответ — перейдём на климатическую норму
    if len(rows) < days:
        return None
    _weather_cache[key] = (time.time(), rows)
    return rows[:days]


def _risk(seal_probability: float, threat_7d: int) -> tuple[float, str]:
    """Приоритет патрулирования: вероятность тюленя, усиленная недавними лодками и выстрелами."""
    score = seal_probability * (1 + min(threat_7d, 5) / 5) / 2
    level = "high" if score >= 0.5 else "medium" if score >= 0.25 else "low"
    return round(score, 2), level


def build(buoys: list[dict], counts: dict[str, dict[str, int]], days: int) -> dict:
    """counts: buoy_id -> {"seal": n, "boat": n, "gunshot": n} за последние 7 дней."""
    today = datetime.now(timezone.utc).date()
    dates = [today + timedelta(days=i) for i in range(days)]
    model = _bundle["model"]
    use_api = USE_WEATHER_API
    forecasts = []

    for buoy in buoys:
        lat, lon = buoy["geo"]["lat"], buoy["geo"]["lon"]
        weather = _fetch_weather(lat, lon, days) if use_api else None
        if weather is None:
            use_api = False  # один сбой — дальше не ждём таймаут на каждом буе
            weather = [_climate(d) for d in dates]

        c = counts.get(buoy["buoy_id"], {})
        seal_7d = c.get("seal", 0)
        threat_7d = c.get("boat", 0) + c.get("gunshot", 0)

        rows = []
        for d, (temp, wind) in zip(dates, weather):
            angle = 2 * math.pi * d.timetuple().tm_yday / 365
            rows.append([math.sin(angle), math.cos(angle), lat, temp, wind, seal_7d, threat_7d])
        probabilities = model.predict_proba(rows)[:, 1]

        day_items = []
        for d, (temp, wind), p in zip(dates, weather, probabilities):
            score, level = _risk(float(p), threat_7d)
            day_items.append({
                "date": d.isoformat(),
                "seal_probability": round(float(p), 2),
                "risk_score": score,
                "risk_level": level,
                "air_temp_c": round(temp, 1),
                "wind_ms": round(wind, 1),
            })
        forecasts.append({"buoy_id": buoy["buoy_id"], "seal_events_7d": seal_7d,
                          "threat_events_7d": threat_7d, "days": day_items})

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "weather_source": "open-meteo" if use_api else "climatology",
        "training_data": _bundle["metrics"]["data"],
        "forecasts": forecasts,
    }
