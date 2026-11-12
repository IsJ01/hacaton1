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

# Порядок классов модели (как в ml/02_preprocess.py: boat=0 … background=4)
MODEL_LABELS = ["boat", "gunshot", "seal", "anomaly", "background"]
# В контракте только 4 метки. Класса background в контракте нет,
# поэтому его вероятность прибавляем к anomaly («нераспознанный звук»).
CONTRACT_LABELS = ["seal", "boat", "gunshot", "anomaly"]

_model = None


class AudioError(Exception):
    pass


def load_model() -> None:
    global _model
    if not MODEL_PATH.is_file():
        raise RuntimeError(f"Не найдена модель: {MODEL_PATH} (задай MODEL_PATH)")
    _model = joblib.load(str(MODEL_PATH))
    classify(np.zeros(SR, dtype=np.float32), SR)  # прогрев: первый вызов librosa медленный


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
    proba["anomaly"] += proba.pop("background")

    candidates = sorted(
        ({"label": label, "confidence": round(float(proba[label]), 2)} for label in CONTRACT_LABELS),
        key=lambda c: -c["confidence"],
    )
    top = candidates[0]
    candidates = [c for c in candidates if c is top or c["confidence"] > 0]  # нулевые варианты не показываем
    # правило ML-команды (ml/05_inference.py): модель не уверена → anomaly
    label = top["label"] if top["confidence"] >= ANOMALY_THRESHOLD else "anomaly"
    return {"label": label, "confidence": top["confidence"], "candidates": candidates}
