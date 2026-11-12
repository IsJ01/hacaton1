"""Заготовленные данные буёв для демо: что, от какого буя и когда отправить на бэкенд.

Здесь только данные — отправляет их emulator.py обычными HTTP-запросами по контракту,
как настоящие буи. Бэкенд ничего не знает о сценариях: он просто принимает и анализирует.

  history — сутки работы пяти буёв (отправляется с прошлыми метками времени):
            тюлени смещаются от о. Тюлений (буй 5) к бую 4, затем к бую 3;
            ночью у буя 3 лодка и выстрел; у буёв 1 и 4 аномальные звуки;
            каждые 30 минут — солёность, температура воды и уровень от каждого буя; два чистых снимка.
  poacher — живой сценарий «браконьер» (паузы в секундах между шагами):
            лодка у буя 1 → она же у буя 2 → выстрел у буя 2 → выстрел у буя 3 →
            солёность падает у буя 3 → спутник видит пятно там же.
"""
import math
import random

# координаты буёв (как в backend/buoys.json) — нужны для привязки спутниковых снимков
BUOY_GEO = {
    "b-001": (43.05, 47.65), "b-002": (43.38, 47.74), "b-003": (43.73, 47.84),
    "b-004": (44.03, 47.92), "b-005": (44.43, 47.65),
}
SALINITY_NORM = 12.5   # ‰
LEVEL_NORM = -28.9     # м, Балтийская система

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
            steps.append({"kind": "telemetry", "minutes_ago": m, "buoy_id": buoy,
                          "water_temp_c": round(temp, 1), "level_m": round(level, 2)})

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
