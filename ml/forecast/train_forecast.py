"""Предиктивный модуль: вероятность присутствия каспийского тюленя у буя в заданный день.

Запуск (из папки ml):  python forecast/train_forecast.py
Результат:             models/seal_forecast.pkl, models/seal_forecast_metrics.json

ВАЖНО: реальной разметки «тюлень был / не был» у нас нет, поэтому обучающая выборка
СИНТЕТИЧЕСКАЯ. Она сгенерирована из предположений о сезонности (функция true_logit ниже):
  - весной тюлень идёт из Северного Каспия на юг вдоль побережья (пик ~ конец апреля);
  - поздней осенью возвращается на север к ледоставу (пик ~ середина ноября, сильнее у северных буёв);
  - зимой взрослые особи на льдах Северного Каспия, летом — на нагуле в открытом море;
  - шторм и жара снижают вероятность, активность лодок и выстрелы распугивают.
Когда накопятся настоящие детекции с буёв, выборку надо заменить на них — код обучения не изменится.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import accuracy_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import train_test_split

MODELS = Path(__file__).resolve().parent.parent / "models"
SEED = 42
N = 30000

# Признаки. Бэкенд (backend/forecast.py) собирает их в этом же порядке.
FEATURES = ["doy_sin", "doy_cos", "lat", "air_temp_c", "wind_ms", "seal_7d", "threat_7d"]

# Примерная климатическая норма для побережья Дагестана (Махачкала): средняя температура воздуха по месяцам, °C.
# Используется в генераторе и как запасной вариант в бэкенде, когда нет интернета для прогноза погоды.
CLIMATE_TEMP_BY_MONTH = [1, 1, 5, 11, 17, 22, 25, 25, 20, 14, 8, 3]
CLIMATE_WIND_MS = 5.0


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


def peak(doy, center, width):
    """Колокол вокруг дня года center (с переходом через Новый год)."""
    d = np.abs(doy - center)
    d = np.minimum(d, 365 - d)
    return np.exp(-0.5 * (d / width) ** 2)


def climate_temp(doy):
    month = np.clip((doy - 1) // 30.5, 0, 11).astype(int)
    return np.array(CLIMATE_TEMP_BY_MONTH)[month]


def true_logit(doy, lat):
    """Сезонное «истинное» присутствие тюленя — предположения, из которых строится синтетика."""
    north = (lat - 42.5) / 2.5  # 0 — южные буи, 1 — северные
    return -2.3 + 2.6 * peak(doy, 115, 28) + 3.0 * peak(doy, 320, 30) * (0.7 + 0.5 * north)


def generate(n, rng):
    doy = rng.integers(1, 366, n)
    lat = rng.uniform(42.5, 45.0, n)
    temp = climate_temp(doy) + rng.normal(0, 3, n)
    wind = rng.gamma(shape=4, scale=CLIMATE_WIND_MS / 4, size=n)
    threat_7d = rng.poisson(1.0, n)

    # местная особенность недели (корм, течение…) — её модель видит только через недавние детекции
    local = rng.normal(0, 1.0, n)
    level = true_logit(doy, lat) + local
    seal_7d = rng.poisson(1.5 * sigmoid(level))  # буй слышит тюленя редко, даже когда он рядом

    logit = (level
             - 0.10 * np.maximum(0, temp - 22)   # жара
             - 0.15 * np.maximum(0, wind - 8)    # шторм
             - 0.25 * np.minimum(threat_7d, 6))  # беспокойство
    present = rng.random(n) < sigmoid(logit)

    angle = 2 * np.pi * doy / 365
    X = np.column_stack([np.sin(angle), np.cos(angle), lat, temp, wind, seal_7d, threat_7d])
    return X, present.astype(int)


def main():
    rng = np.random.default_rng(SEED)
    X, y = generate(N, rng)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=SEED, stratify=y)

    model = GradientBoostingClassifier(n_estimators=200, max_depth=3, learning_rate=0.08, random_state=SEED)
    model.fit(X_train, y_train)

    proba = model.predict_proba(X_test)[:, 1]
    metrics = {
        "data": "synthetic",
        "samples": N,
        "positive_share": round(float(y.mean()), 3),
        "roc_auc": round(float(roc_auc_score(y_test, proba)), 3),
        "brier": round(float(brier_score_loss(y_test, proba)), 3),
        "accuracy": round(float(accuracy_score(y_test, proba >= 0.5)), 3),
        "feature_importance": {f: round(float(v), 3) for f, v in zip(FEATURES, model.feature_importances_)},
    }

    MODELS.mkdir(exist_ok=True)
    joblib.dump({
        "model": model,
        "features": FEATURES,
        "climate_temp_by_month": CLIMATE_TEMP_BY_MONTH,
        "climate_wind_ms": CLIMATE_WIND_MS,
        "trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "metrics": metrics,
    }, MODELS / "seal_forecast.pkl", compress=3)
    (MODELS / "seal_forecast_metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False))
    print(json.dumps(metrics, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
