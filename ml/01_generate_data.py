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
N_SAMPLES = 30  # сколько сгенерировать на каждый class


def pink_noise(n):
    """Розовый шум (1/f) — более реалистичен белому для природных фонов."""
    white = np.random.randn(n)
    # Простой фильтр для приближения к розовому
    b = np.array([0.5, 0.5])
    a = np.array([1.0])
    from scipy.signal import lfilter
    return lfilter(b, a, white)

def augment_realistic(audio, sr=22050):
    """Делает синтетику похожей на запись с микрофона."""
    import numpy as np
    from scipy.signal import butter, filtfilt

    # 1. Гул сети 50 Гц (всегда есть при записи)
    t = np.arange(len(audio)) / sr
    audio += 0.008 * np.sin(2 * np.pi * 50 * t)

    # 2. Случайный gain (0.1–1.5) — имитация разного расстояния
    audio *= np.random.uniform(0.15, 1.3)

    # 3. Клиппинг (перегруз микрофона)
    audio = np.clip(audio, -0.9, 0.9)

    # 4. Реверберация (затухающие копии)
    for delay_ms in [15, 35, 70]:
        delay = int(sr * delay_ms / 1000)
        audio[delay:] += np.random.uniform(0.08, 0.2) * audio[:-delay]

    # 5. Ветровой шум (низкочастотный)
    wind = np.random.randn(len(audio))
    b, a = butter(2, 150, btype='low', fs=sr)
    wind = filtfilt(b, a, wind)
    audio += 0.03 * wind

    # 6. Квантование 16-bit (имитация ADC)
    audio = np.round(audio * 32767) / 32767

    # Нормализация
    audio = audio / (np.max(np.abs(audio)) + 1e-8) * 0.85
    return audio.astype(np.float32)

def generate_boat_motor(duration=DURATION, sr=SR):
    """Звук подвесного мотора: тон + гармоники винта + шум."""
    n = int(sr * duration)
    t = np.linspace(0, duration, n, endpoint=False)
    signal = np.zeros(n)

    for freq in [120, 240, 360, 480, 600]:
        amp = 0.3 / (freq / 120)
        signal += amp * np.sin(2 * np.pi * freq * t)

    signal += 0.15 * pink_noise(n)
    env = np.exp(-t * 0.6)
    signal *= env
    signal = signal / (np.max(np.abs(signal)) + 1e-8) * 0.8
    return signal.astype(np.float32)

def generate_gunshot(duration=1.0, sr=SR):
    """Выстрел: резкий импульс + длинный хвост реверберации."""
    n = int(sr * duration)
    t = np.linspace(0, duration, n, endpoint=False)

    # Острый начальный импульс (0–50 мс)
    impulse = np.zeros(n)
    attack = int(0.05 * sr)
    impulse[:attack] = np.random.randn(attack) * 0.9

    # Экспоненциальный затухающий хвост
    tail = np.exp(-t * 8.0) * 0.7

    signal = impulse + tail
    signal = signal / (np.max(np.abs(signal)) + 1e-8) * 0.9
    return signal.astype(np.float32)

def generate_seal_vocalization(duration=DURATION, sr=SR):
    """Тюлений звук: высокочастотный свист/щебетание."""
    n = int(sr * duration)
    t = np.linspace(0, duration, n, endpoint=False)

    signal = np.zeros(n)
    # Короткие свисты (2–5 кГц) с частотной модуляцией
    num_whistles = 8
    for _ in range(num_whistles):
        start = np.random.randint(0, n - int(0.3 * sr))
        length = int(np.random.uniform(0.1, 0.4) * sr)
        freq = np.random.uniform(2000, 5000)
        # Частота "кружится" вверх-вниз
        tt = t[start:start + length]
        fm = freq + 500 * np.sin(2 * np.pi * 3 * tt)
        whistle = np.sin(2 * np.pi * fm * tt)
        env = np.exp(-tt * 5)
        signal[start:start + length] += whistle * env * 0.3

    signal += 0.05 * pink_noise(n)
    signal = signal / (np.max(np.abs(signal)) + 1e-8) * 0.7
    return signal.astype(np.float32)

def generate_anomaly(duration=DURATION, sr=SR):
    """Аномалия: металлический скрежет + удары + неизвестный шум."""
    n = int(sr * duration)
    t = np.linspace(0, duration, n, endpoint=False)

    signal = np.zeros(n)
    # Металлический скрежет (высокие частоты, трение)
    for _ in range(5):
        start = np.random.randint(0, n - int(0.2 * sr))
        length = int(np.random.uniform(0.05, 0.2) * sr)
        tt = t[start:start + length]
        screech = np.sin(2 * np.pi * np.random.uniform(3000, 7000) * tt)
        env = np.exp(-tt * 20)
        signal[start:start + length] += screech * env * 0.4

    # Случайные удары (низкочастотные импульсы)
    for _ in range(3):
        pos = np.random.randint(0, n - 100)
        signal[pos:pos + 100] += 0.3 * np.random.randn(100)

    signal += 0.1 * pink_noise(n)
    signal = signal / (np.max(np.abs(signal)) + 1e-8) * 0.7
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
    for folder in ["boat", "gunshot", "seal", "anomaly", "background"]:
        (data_dir / folder).mkdir(parents=True, exist_ok=True)

    N_SAMPLES = 30
    for i in range(N_SAMPLES):
        sf.write(str(data_dir / "boat" / f"boat_{i:03d}.wav"),
                 generate_boat_motor(), SR)
        sf.write(str(data_dir / "gunshot" / f"gunshot_{i:03d}.wav"),
                 generate_gunshot(), SR)
        sf.write(str(data_dir / "seal" / f"seal_{i:03d}.wav"),
                 generate_seal_vocalization(), SR)
        sf.write(str(data_dir / "anomaly" / f"anomaly_{i:03d}.wav"),
                 generate_anomaly(), SR)
        sf.write(str(data_dir / "background" / f"bg_{i:03d}.wav"),
                 generate_background(), SR)

    print(f"Сгенерировано: {N_SAMPLES} boat + gunshot + seal + anomaly + background")


if __name__ == "__main__":
    main()