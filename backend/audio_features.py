"""Признаки звука v2 — один код для обучения (ml/06_train_robust.py) и для бэкенда (sound.py).

Почему не 13 усреднённых MFCC, как в v1:
  - среднее по времени стирает форму сигнала: выстрел (короткий импульс) и ровный шум с тем же спектром
    становятся неотличимы → добавлены разброс, динамика (delta) и признаки огибающей;
  - фильтр «вода гасит всё выше 4 кГц» срезал тюленьи свисты 4–8 кГц целиком → срез поднят до 8 кГц
    (на дистанциях до нескольких сотен метров эти частоты под водой доходят);
  - громкость зависит от дистанции до буя → признаки нормированы по уровню, громкость отдельно не учитывается.
"""
import librosa
import numpy as np
from scipy.signal import butter, sosfiltfilt

SR = 22050
N_MFCC = 20
CUTOFF_HZ = 8000
MIN_SAMPLES = 4096
VERSION = 2


def prepare(audio: np.ndarray, sr: int) -> np.ndarray:
    """Моно-сигнал любой частоты → 22050 Гц, срез выше 8 кГц, нормировка по пику."""
    audio = np.asarray(audio, dtype=np.float64)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != SR:
        audio = librosa.resample(audio, orig_sr=sr, target_sr=SR)
    if len(audio) < MIN_SAMPLES:
        audio = np.pad(audio, (0, MIN_SAMPLES - len(audio)))
    sos = butter(6, CUTOFF_HZ, btype="low", fs=SR, output="sos")
    audio = sosfiltfilt(sos, audio)
    peak = np.max(np.abs(audio))
    return audio / peak if peak > 1e-9 else audio


def features(audio: np.ndarray, sr: int) -> np.ndarray:
    """Вектор признаков (1, n) для модели."""
    y = prepare(audio, sr)
    mfcc = librosa.feature.mfcc(y=y, sr=SR, n_mfcc=N_MFCC)
    frames = mfcc.shape[1]
    width = min(9, frames if frames % 2 else frames - 1)  # окно delta — нечётное и не длиннее записи
    delta = librosa.feature.delta(mfcc, width=width, mode="nearest") if width >= 3 else np.zeros_like(mfcc)
    spec = np.abs(librosa.stft(y, n_fft=1024, hop_length=256)) + 1e-10
    centroid = librosa.feature.spectral_centroid(S=spec, sr=SR)[0] / (SR / 2)
    bandwidth = librosa.feature.spectral_bandwidth(S=spec, sr=SR)[0] / (SR / 2)
    rolloff = librosa.feature.spectral_rolloff(S=spec, sr=SR, roll_percent=0.85)[0] / (SR / 2)
    flatness = librosa.feature.spectral_flatness(S=spec)[0]
    zcr = librosa.feature.zero_crossing_rate(y, frame_length=1024, hop_length=256)[0]
    rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=256)[0]
    rms_db = 20 * np.log10(rms + 1e-6)
    # огибающая: насколько звук импульсный (выстрел) или ровный (мотор)
    crest = float(np.max(np.abs(y)) / (np.sqrt(np.mean(y ** 2)) + 1e-9))
    dyn = float(np.percentile(rms_db, 95) - np.percentile(rms_db, 10))
    loud_share = float(np.mean(rms_db > np.max(rms_db) - 10))
    onset = librosa.onset.onset_strength(S=librosa.amplitude_to_db(spec), sr=SR)
    onset_peak = float(np.max(onset) / (np.mean(onset) + 1e-9)) if len(onset) else 0.0
    # где сосредоточена энергия: доля по полосам
    freqs = np.linspace(0, SR / 2, spec.shape[0])
    power = (spec ** 2).sum(axis=1)
    bands = [(0, 300), (300, 1000), (1000, 2500), (2500, 5000), (5000, 8000)]
    band_share = [float(power[(freqs >= lo) & (freqs < hi)].sum() / (power.sum() + 1e-12)) for lo, hi in bands]

    stats = lambda v: [float(np.mean(v)), float(np.std(v))]
    vec = np.concatenate([
        mfcc.mean(axis=1), mfcc.std(axis=1), np.abs(delta).mean(axis=1)[:10],
        stats(centroid) + stats(bandwidth) + stats(rolloff) + stats(flatness) + stats(zcr),
        [crest, dyn, loud_share, onset_peak], band_share,
    ])
    return np.nan_to_num(vec).reshape(1, -1)
