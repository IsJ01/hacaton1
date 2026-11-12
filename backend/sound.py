"""Классификация звука обученной моделью из ml/models/classifier.pkl.

Признаки считаются точно так же, как при обучении (ml/02_preprocess.py, ml/05_inference.py):
  аудио → моно → 22050 Гц → low-pass 4 кГц («подводный» фильтр) → 13 MFCC → среднее по времени.
"""
import io
from pathlib import Path

import joblib
import librosa
import numpy as np
import soundfile as sf
from scipy.signal import butter, filtfilt

from config import ANOMALY_THRESHOLD, MODEL_PATH

SR = 22050
N_MFCC = 13
MIN_SAMPLES = 2048  # короче одного окна MFCC — дополняем тишиной

# Порядок классов модели (как в ml/02_preprocess.py: boat=0, gunshot=1, seal=2, background=3)
MODEL_LABELS = ["boat", "gunshot", "seal", "background"]
# В контракте 4 метки: seal, boat, gunshot, anomaly
CONTRACT_LABELS = ["seal", "boat", "gunshot", "anomaly"]

_model = None


class AudioError(Exception):
    pass


def load_model() -> None:
    global _model
    if not MODEL_PATH.is_file():
        raise RuntimeError(f"Не найдена модель: {MODEL_PATH} (задай MODEL_PATH)")
    _model = joblib.load(str(MODEL_PATH))
    # Прогрев: тихий шум вместо нулей, чтобы librosa не выдал предупреждения
    warm = np.random.randn(SR).astype(np.float32) * 0.001
    classify(warm, SR)


def decode(raw: bytes) -> tuple[np.ndarray, int]:
    """Байты аудио (wav/flac/ogg/mp3) → моно-сигнал и частота дискретизации."""
    try:
        audio, sr = sf.read(io.BytesIO(raw), dtype="float32")
    except Exception:
        raise AudioError("cannot decode audio (supported: wav, flac, ogg, mp3)")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if len(audio) == 0:
        raise AudioError("audio is empty")
    return audio, sr


def save_wav(path: Path, audio: np.ndarray, sr: int) -> None:
    sf.write(str(path), audio, sr, subtype="PCM_16")


def _features(audio: np.ndarray, sr: int) -> np.ndarray:
    audio = audio.astype(np.float64)
    if sr != SR:
        audio = librosa.resample(audio, orig_sr=sr, target_sr=SR)
    if len(audio) < MIN_SAMPLES:
        audio = np.pad(audio, (0, MIN_SAMPLES - len(audio)))
    b, a = butter(4, min(4000, SR / 2 - 1), btype="low", fs=SR)
    audio = filtfilt(b, a, audio)
    mfcc = librosa.feature.mfcc(y=audio, sr=SR, n_mfcc=N_MFCC)
    return mfcc.mean(axis=1).reshape(1, -1)


def classify(audio: np.ndarray, sr: int) -> dict:
    """Возвращает ClassificationResult из контракта: { label, confidence, candidates }."""
    proba = dict(zip(MODEL_LABELS, _model.predict_proba(_features(audio, sr))[0]))

    # Anomaly fallback: если модель не уверена → anomaly
    top_idx = int(np.argmax(list(proba.values())))
    top_conf = list(proba.values())[top_idx]
    top_label = MODEL_LABELS[top_idx]

    # Правило:
    # 1) Если top_conf < threshold → anomaly (модель не уверена)
    # 2) Если top_label == "background" → anomaly (background в контракте = anomaly)
    # 3) Иначе → top_label
    if top_conf < ANOMALY_THRESHOLD or top_label == "background":
        final_label = "anomaly"
        anomaly_conf = proba["background"] + (1 - top_conf) * 0.5
    else:
        final_label = top_label
        anomaly_conf = proba["background"]

    # Строим candidates в порядке контракта: seal, boat, gunshot, anomaly
    candidates = []
    for label in CONTRACT_LABELS:
        if label == "seal":
            conf = proba["seal"]
        elif label == "boat":
            conf = proba["boat"]
        elif label == "gunshot":
            conf = proba["gunshot"]
        elif label == "anomaly":
            conf = anomaly_conf
        candidates.append({"label": label, "confidence": round(float(conf), 2)})

    # Сортируем по убыванию confidence
    candidates.sort(key=lambda c: -c["confidence"])

    top = candidates[0]
    # Убираем нулевые варианты
    candidates = [c for c in candidates if c is top or c["confidence"] > 0]

    return {"label": final_label, "confidence": top_conf, "candidates": candidates}