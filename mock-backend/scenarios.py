"""Заготовленные данные буёв для демо: что, от какого буя и когда отправить на бэкенд.

Здесь только данные — отправляет их emulator.py обычными HTTP-запросами по контракту,
как настоящие буи. Бэкенд ничего не знает о сценариях: он просто принимает и анализирует.

  history — сутки работы пяти буёв (отправляется с прошлыми метками времени):
            тюлени смещаются от о. Тюлений (буй 5) к бую 4, затем к бую 3;
            ночью у буя 3 лодка и выстрел; у буёв 1 и 4 аномальные звуки;
            каждые 30 минут — солёность, температура воды и уровень от каждого буя; два чистых снимка.
  mortality — массовая гибель тюленей (как каждую осень в Дагестане): данные буёв за 3 суток до находки
            и две находки туш на берегу. У Аграханского залива перед гибелью — всплеск метана у буёв 3 и 4
            (версия «выброс газа»), у Махачкалы — ночные лодки и выстрел у буя 1 (версия «сети и браконьеры»).
            Ветер для обратного дрейфа и землетрясения бэкенд берёт из реальных открытых данных.
  poacher — живой сценарий «браконьер» (паузы в секундах между шагами):
            лодка у буя 1 → она же у буя 2 → выстрел у буя 2 → выстрел у буя 3 →
            солёность падает у буя 3 → спутник видит пятно там же.
"""
import math
import random
from datetime import datetime, timedelta, timezone

# координаты буёв (как в backend/buoys.json) — нужны для привязки спутниковых снимков
BUOY_GEO = {
    "b-001": (43.05, 47.65), "b-002": (43.38, 47.74), "b-003": (43.73, 47.84),
    "b-004": (44.03, 47.92), "b-005": (44.43, 47.65),
}
SALINITY_NORM = 12.5   # ‰
LEVEL_NORM = -28.9     # м, Балтийская система
METHANE_BG = 10.0      # растворённый метан, нмоль/л (фон)

# Файлы, которые модель бэкенда распознаёт уверенно (проверено через /api/v1/classify/upload).
# Файлы *_01.wav / *_02.wav сделаны другим генератором — модель их путает, в сценариях не используем.
SEAL_FILES = ["seal_001.wav", "seal_010.wav", "seal_011.wav", "seal_012.wav", "seal_014.wav", "seal_015.wav"]

# (минут назад, буй) — путь вокализаций тюленя за сутки
SEAL_TRAIL = [(1400, "b-005"), (1340, "b-005"), (1290, "b-005"), (1220, "b-005"), (1150, "b-005"), (1090, "b-005"),
              (1040, "b-004"), (980, "b-005"), (910, "b-004"), (850, "b-004"), (780, "b-005"), (720, "b-004"),
              (650, "b-004"), (560, "b-003"), (500, "b-004"), (430, "b-003"), (380, "b-004"), (300, "b-003"),
              (240, "b-003"), (170, "b-003"), (120, "b-004"), (70, "b-003"), (35, "b-003")]


def history_steps(hours: int = 24) -> list[dict]:
    """Все отправки за последние hours часов, от старых к новым. minutes_ago — когда «случилось»."""
    steps = []
    for i, (m, buoy) in enumerate(SEAL_TRAIL):
        if m <= hours * 60:
            steps.append({"kind": "sound", "minutes_ago": m, "buoy_id": buoy, "file": SEAL_FILES[i % len(SEAL_FILES)]})
    for m, buoy, f in [(600, "b-003", "boat_000.wav"), (594, "b-003", "gunshot_000.wav"),
                       (760, "b-001", "anomaly_000.wav"), (330, "b-004", "anomaly_001.wav")]:
        if m <= hours * 60:
            steps.append({"kind": "sound", "minutes_ago": m, "buoy_id": buoy, "file": f})

    rnd = random.Random(7)
    for bi, buoy in enumerate(BUOY_GEO):
        for i in range(hours * 2, 0, -1):
            m = 30 * i
            sal = SALINITY_NORM + 0.35 * math.sin((i + bi * 5) / 6) + rnd.uniform(-0.08, 0.08)
            temp = 11.2 - 0.25 * bi + 0.6 * math.sin((i + 6) / 7.6) + rnd.uniform(-0.05, 0.05)  # севернее — холоднее
            level = LEVEL_NORM + 0.05 * math.sin(i / 4 + bi) + rnd.uniform(-0.01, 0.01)
            steps.append({"kind": "salinity", "minutes_ago": m, "buoy_id": buoy, "salinity": round(sal, 2)})
            steps.append({"kind": "telemetry", "minutes_ago": m, "buoy_id": buoy, "water_temp_c": round(temp, 1),
                          "level_m": round(level, 2), "methane_nmol": round(METHANE_BG + rnd.uniform(-2, 3), 1)})

    for m, f, (lat, lon) in [(1100, "clean_01.jpg", (44.30, 47.80)), (480, "clean_02.jpg", (43.55, 47.90))]:
        if m <= hours * 60:
            steps.append({"kind": "satellite", "minutes_ago": m, "file": f, "lat": lat, "lon": lon})
    steps.sort(key=lambda s: -s["minutes_ago"])
    return steps


POACHER = [
    {"pause": 0, "kind": "sound", "buoy_id": "b-001", "file": "boat_001.wav", "note": "лодка входит в зону буя 1"},
    {"pause": 6, "kind": "sound", "buoy_id": "b-002", "file": "boat_002.wav", "note": "ту же лодку слышит буй 2"},
    {"pause": 6, "kind": "sound", "buoy_id": "b-002", "file": "gunshot_001.wav", "note": "выстрел у буя 2"},
    {"pause": 5, "kind": "sound", "buoy_id": "b-003", "file": "gunshot_002.wav", "note": "выстрел слышит буй 3"},
    {"pause": 6, "kind": "salinity", "buoy_id": "b-003", "salinity": 9.6, "note": "солёность у буя 3 упала — сброс"},
    {"pause": 5, "kind": "satellite", "file": "discharge_01.jpg", "lat": 43.73, "lon": 47.86, "note": "спутник видит пятно у буя 3"},
]


def mortality_steps() -> list[dict]:
    """Трое суток до находки (от 72 до 24 ч назад — дальше история за сутки) и сами находки туш."""
    steps = []
    rnd = random.Random(11)
    for bi, buoy in enumerate(BUOY_GEO):
        for i in range(144, 48, -1):  # каждые 30 минут, 72…24 ч назад
            m = 30 * i
            methane = METHANE_BG + rnd.uniform(-2, 3)
            # всплеск метана у буёв 3 и 4 примерно 30 ч назад (у буя 4 сильнее — ближе к источнику)
            if buoy in ("b-003", "b-004") and 1680 <= m <= 1920:
                peak = 160 if buoy == "b-004" else 75
                methane += peak * math.exp(-((m - 1800) / 70) ** 2)
            steps.append({"kind": "telemetry", "minutes_ago": m, "buoy_id": buoy,
                          "water_temp_c": round(11.4 - 0.25 * bi + 0.5 * math.sin(i / 7.6), 1),
                          "level_m": round(LEVEL_NORM + 0.05 * math.sin(i / 4 + bi), 2), "methane_nmol": round(methane, 1)})
            steps.append({"kind": "salinity", "minutes_ago": m, "buoy_id": buoy,
                          "salinity": round(SALINITY_NORM + 0.3 * math.sin((i + bi * 5) / 6) + rnd.uniform(-0.08, 0.08), 2)})
    # ночные лодки и выстрел у буя 1: трое суток назад, около 23:00 по местному времени (UTC+3)
    now = datetime.now(timezone.utc)
    night = (now + timedelta(hours=3)).replace(hour=23, minute=0, second=0, microsecond=0) - timedelta(days=3, hours=3)
    base = int((now - night).total_seconds() // 60)
    for shift, f in [(30, "boat_000.wav"), (0, "boat_001.wav"), (-50, "gunshot_000.wav"), (-110, "boat_002.wav")]:
        steps.append({"kind": "sound", "minutes_ago": base + shift, "buoy_id": "b-001", "file": f})
    # тюлени шли мимо буёв 3–4 в те же сутки
    for m, b in [(2000, "b-004"), (1900, "b-004"), (1850, "b-003"), (1700, "b-003")]:
        steps.append({"kind": "sound", "minutes_ago": m, "buoy_id": b, "file": SEAL_FILES[m % len(SEAL_FILES)]})
    # находки на берегу (инспекторы обходят побережье утром)
    steps += [
        {"kind": "carcass", "minutes_ago": 300, "lat": 43.80, "lon": 47.80, "count": 37, "condition": "fresh",
         "note": "Аграханский полуостров, восточный берег; без видимых травм"},
        {"kind": "carcass", "minutes_ago": 240, "lat": 43.02, "lon": 47.56, "count": 12, "condition": "moderate",
         "note": "пляж Махачкалы; рядом обрывки сети"},
    ]
    steps.sort(key=lambda s: -s["minutes_ago"])
    return steps
