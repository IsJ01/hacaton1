"""Генерация синтетической библиотеки звуков (только stdlib).
Имя файла = <label>_NN.wav — по префиксу мок-классификатор «узнаёт» класс."""
import math, random, struct, wave
from pathlib import Path

SR = 16000
DUR = 2.0


def _seal(t, rnd):
    # серия «лающих» посылок 350–700 Гц
    phase = (t * 3.0) % 1.0
    env = math.exp(-phase * 6) if phase < 0.6 else 0.0
    f = 350 + 350 * math.exp(-phase * 4)
    return env * (math.sin(2 * math.pi * f * t) + 0.4 * math.sin(4 * math.pi * f * t)) * 0.6


def _boat(t, rnd):
    # низкий гул мотора + гармоники + шум воды
    s = sum(math.sin(2 * math.pi * 85 * k * t) / k for k in range(1, 6))
    return 0.35 * s * (1 + 0.2 * math.sin(2 * math.pi * 12 * t)) + 0.08 * rnd.uniform(-1, 1)


def _gunshot(t, rnd):
    # резкий шумовой импульс с затуханием
    t0 = 0.4
    if t < t0:
        return 0.01 * rnd.uniform(-1, 1)
    return math.exp(-(t - t0) * 9) * rnd.uniform(-1, 1)


def _anomaly(t, rnd):
    # свип 200→3000 Гц с дрожанием
    f = 200 + 1400 * t
    return 0.5 * math.sin(2 * math.pi * f * t) * (0.6 + 0.4 * math.sin(2 * math.pi * 7 * t))


GENERATORS = {"seal": _seal, "boat": _boat, "gunshot": _gunshot, "anomaly": _anomaly}


def generate_library(folder: Path, per_label: int = 2) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for label, fn in GENERATORS.items():
        for i in range(1, per_label + 1):
            path = folder / f"{label}_{i:02d}.wav"
            if path.exists():
                continue
            rnd = random.Random(f"{label}{i}")
            gain = rnd.uniform(0.7, 1.0)
            frames = bytearray()
            for n in range(int(SR * DUR)):
                v = max(-1.0, min(1.0, fn(n / SR, rnd) * gain))
                frames += struct.pack("<h", int(v * 32000))
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SR)
                w.writeframes(bytes(frames))


if __name__ == "__main__":
    generate_library(Path(__file__).parent / "library")
    print("ok")
