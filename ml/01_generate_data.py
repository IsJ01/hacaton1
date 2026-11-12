"""Генерация синтетического датасета для аудио-классификации.

Синтетика вместо реальных подводных recordings:
- boat: шум мотора + гармоники винта (100-500 Гц) + розовый шум
- background: волны, ветер, фон

На следующем этапе — замена на реальные recordings из Freesound.
"""
import os
import numpy as np
import soundfile as sf
from pathlib import Path

SR = 22050
DURATION = 3.0
N_SAMPLES = 30  # сколько сгенерировать на каждый класс


def pink_noise(n):
    """Розовый шум (1/f) — более реалистичен белому для природных фонов."""
    white = np.random.randn(n)
    # Простой фильтр для приближения к розовому
    b = np.array([0.5, 0.5])
    a = np.array([1.0])
    from scipy.signal import lfilter
    return lfilter(b, a, white)


def generate_boat_motor(duration=DURATION, sr=SR):
    """Звук подвесного мотора: основная частота + гармоники винта."""
    n = int(sr * duration)
    t = np.linspace(0, duration, n, endpoint=False)
    signal = np.zeros(n)

    # Основной тон и гармоники (характерно для винта/коленвала)
    for freq in [120, 240, 360, 480, 600]:
        amp = 0.3 / (freq / 120)
        signal += amp * np.sin(2 * np.pi * freq * t)

    # Шум винта на высоких частотах
    signal += 0.15 * pink_noise(n)

    # Огибающая: рев мотора с затуханием
    env = np.exp(-t * 0.6)
    signal *= env

    # Нормировка
    signal = signal / (np.max(np.abs(signal)) + 1e-8) * 0.8
    return signal.astype(np.float32)


def generate_background(duration=DURATION, sr=SR):
    """Фон: волны + ветер + низкочастотный шум."""
    n = int(sr * duration)
    t = np.linspace(0, duration, n, endpoint=False)

    # Низкочастотный фон (волны)
    waves = 0.4 * np.sin(2 * np.pi * 0.3 * t) * np.sin(2 * np.pi * 0.05 * t)

    # Розовый шум (ветер)
    wind = 0.3 * pink_noise(n)

    # Случайные всплески (брызги)
    splashes = np.zeros(n)
    for _ in range(int(duration * 0.5)):
        pos = np.random.randint(0, n - 100)
        splashes[pos:pos + 100] += 0.1 * np.random.randn(100)

    signal = waves + wind + splashes
    signal = signal / (np.max(np.abs(signal)) + 1e-8) * 0.6
    return signal.astype(np.float32)


def main():
    data_dir = Path(__file__).parent / "data"
    for folder in ["boat", "background"]:
        (data_dir / folder).mkdir(parents=True, exist_ok=True)

    for i in range(N_SAMPLES):
        boat = generate_boat_motor()
        bg = generate_background()
        sf.write(str(data_dir / "boat" / f"boat_{i:03d}.wav"), boat, SR)
        sf.write(str(data_dir / "background" / f"bg_{i:03d}.wav"), bg, SR)

    print(f"Сгенерировано: {N_SAMPLES} boat + {N_SAMPLES} background")
    print(f"Папка: {data_dir}")


if __name__ == "__main__":
    main()