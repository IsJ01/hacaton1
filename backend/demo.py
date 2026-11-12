"""Демо-данные: история за сутки при пустой базе и сценарий «браконьер» по кнопке.

Звук всегда проходит через настоящую модель (process_sound из main.py), поэтому
на дашборде видно ровно то, что модель ответила на файл из ml/data.
"""
import math
import random
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

import storage

# (минут назад, буй, файл из ml/data). Сюжет суток:
#   тюлени с лежбища у о. Тюлений (буй 5) смещаются на юг — к бую 4, затем к бую 3;
#   у буя 3 ночью лодка и выстрел (красный), у буёв 1 и 4 аномальные звуки (жёлтый), буй 2 — норма.
SEAL_TRAIL = [(1400, "b-005"), (1340, "b-005"), (1290, "b-005"), (1220, "b-005"), (1150, "b-005"), (1090, "b-005"),
              (1040, "b-004"), (980, "b-005"), (910, "b-004"), (850, "b-004"), (780, "b-005"), (720, "b-004"),
              (650, "b-004"), (560, "b-003"), (500, "b-004"), (430, "b-003"), (380, "b-004"), (300, "b-003"),
              (240, "b-003"), (170, "b-003"), (120, "b-004"), (70, "b-003"), (35, "b-003")]
HISTORY_SOUNDS = [(m, b, f"seal_{i:03d}.wav") for i, (m, b) in enumerate(SEAL_TRAIL)] + [
    (600, "b-003", "boat_002.wav"), (594, "b-003", "gunshot_000.wav"),
    (760, "b-001", "anomaly_000.wav"), (330, "b-004", "anomaly_001.wav"),
]

# сценарий «браконьер»: (пауза перед шагом в секундах, тип, буй, файл или солёность)
SCENARIO = [
    (0, "sound", "b-001", "boat_004.wav"),     # лодка входит в зону слышимости
    (6, "sound", "b-002", "boat_005.wav"),     # её слышит соседний буй — подтверждение
    (6, "sound", "b-002", "gunshot_001.wav"),  # выстрел
    (5, "sound", "b-003", "gunshot_002.wav"),  # выстрел слышит второй буй
    (6, "salinity", "b-003", 9.6),             # сброс: солёность ниже нормы
]

_running = threading.Lock()


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(library_file: Callable, name: str) -> bytes:
    try:
        return library_file(name).read_bytes()
    except Exception:  # такого файла нет в библиотеке — берём любой
        return library_file(None).read_bytes()


def seed_if_empty(buoys: dict, library_file: Callable, process_sound: Callable, norm: float, level_norm: float) -> int:
    """Пустая база → история за 24 часа. Возвращает число созданных звуковых событий."""
    if storage.list_sound_events(None, None, None, None, 1, 0)[0]:
        return 0
    now = datetime.now(timezone.utc)
    created = 0
    for minutes, buoy_id, name in HISTORY_SOUNDS:
        if buoy_id in buoys:
            process_sound(_read(library_file, name), f"evt-demo-{created:03d}", buoy_id, "buoy",
                          _iso(now - timedelta(minutes=minutes)))
            created += 1
    # солёность, температура воды и уровень каждые 30 минут: норма ± суточное колебание
    rnd = random.Random(7)
    for bi, buoy_id in enumerate(buoys):
        for i in range(48, 0, -1):
            ts = _iso(now - timedelta(minutes=30 * i))
            s = norm + 0.35 * math.sin((i + bi * 5) / 6) + rnd.uniform(-0.08, 0.08)
            storage.add_measurement(f"sal-demo-{bi}-{i:02d}", buoy_id, ts, round(s, 2))
            temp = 11.2 - 0.25 * bi + 0.6 * math.sin((i + 6) / 7.6) + rnd.uniform(-0.05, 0.05)  # севернее — холоднее
            level = level_norm + 0.05 * math.sin(i / 4 + bi) + rnd.uniform(-0.01, 0.01)
            storage.add_telemetry(f"tel-demo-{bi}-{i:02d}", buoy_id, ts, round(temp, 1), round(level, 2))
    return created


def run_scenario(buoys: dict, library_file: Callable, process_sound: Callable, add_salinity: Callable,
                 new_id: Callable, now_iso: Callable) -> bool:
    """Запускает сценарий в фоне. False — если он уже идёт."""
    if not _running.acquire(blocking=False):
        return False

    def worker():
        try:
            for pause, kind, buoy_id, arg in SCENARIO:
                time.sleep(pause)
                if buoy_id not in buoys:
                    continue
                if kind == "sound":
                    process_sound(_read(library_file, arg), new_id("evt"), buoy_id, "buoy", now_iso())
                else:
                    add_salinity(buoy_id, arg)
        finally:
            _running.release()

    threading.Thread(target=worker, daemon=True).start()
    return True


def duration_sec() -> int:
    return sum(step[0] for step in SCENARIO)
