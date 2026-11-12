"""Предобработка: underwater attenuation + MFCC.

Важно: вода гасит высокие частоты. Применяем low-pass фильтр,
имитирующий подводное затухание, перед извлечением MFCC.
"""
import numpy as np
import librosa
import soundfile as sf
from scipy.signal import butter, filtfilt
from pathlib import Path

SR = 22050
N_MFCC = 13
HOP_LENGTH = 512
N_FFT = 2048


def underwater_filter(audio, sr):
    """Low-pass фильтр: вода гасит высокие частоты (>4-5 кГц)."""
    cutoff = min(4000, sr / 2 - 1)
    b, a = butter(4, cutoff, btype='low', fs=sr)
    return filtfilt(b, a, audio)


def extract_mfcc(audio_path, sr=SR, n_mfcc=N_MFCC):
    """Извлекает MFCC из аудио-файла с underwater-фильтрацией."""
    audio, orig_sr = sf.read(str(audio_path))
    if audio.ndim > 1:
        audio = audio.mean(axis=1)  # stereo → mono
    if orig_sr != sr:
        audio = librosa.resample(audio, orig_sr=orig_sr, target_sr=sr)

    audio = underwater_filter(audio, sr)
    mfcc = librosa.feature.mfcc(
        y=audio, sr=sr, n_mfcc=n_mfcc,
        n_fft=N_FFT, hop_length=HOP_LENGTH
    )
    return mfcc.mean(axis=1)  # усреднение по времени


def load_dataset(data_dir=None):
    """Загружает датасет: X (признаки), y (метки)."""
    if data_dir is None:
        data_dir = Path(__file__).parent / "data"

    X, y = [], []
    # Anomaly исключён из датасета: он определяется через fallback-логику
    # (confidence < 0.6 → anomaly), а не как отдельный класс.
    for folder_name, label in [("boat", 0), ("gunshot", 1), ("seal", 2), ("background", 3)]:
        folder_path = Path(data_dir) / folder_name
        if not folder_path.exists():
            print(f"Папка не найдена: {folder_path}")
            continue
        for wav in sorted(folder_path.glob("*.wav")):
            try:
                features = extract_mfcc(wav)
                X.append(features)
                y.append(label)
            except Exception as e:
                print(f"Skip {wav.name}: {e}")

    return np.array(X), np.array(y)


if __name__ == "__main__":
    X, y = load_dataset()
    print(f"Датасет: {X.shape[0]} образцов, {X.shape[1]} признаков")
    from collections import Counter
    labels = ["boat", "gunshot", "seal", "background"]
    counts = Counter(y)
    print("Распределение:", {labels[k]: counts.get(k, 0) for k in range(4)})
    print(f"Пример признаков: {X[0][:5]}")