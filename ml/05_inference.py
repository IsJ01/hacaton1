"""Инференс: multiclass классификация (boat, gunshot, seal, anomaly, background).

Логика anomaly:
- Модель предсказывает 5 классов.
- Если top-1 confidence < ANOMALY_THRESHOLD → "anomaly".
- Иначе — top-1 класс.

Возвращает ClassificationResult в формате API-контракта.
"""
import io
import joblib
import librosa
import soundfile as sf
import numpy as np
from scipy.signal import butter, filtfilt
from pathlib import Path

MODEL_PATH = Path(__file__).parent / "models" / "classifier.pkl"
SR = 22050
N_MFCC = 13

# Порог: ниже этого confidence → аномалия
ANOMALY_THRESHOLD = 0.6

LABELS = ["boat", "gunshot", "seal", "anomaly", "background"]

model = joblib.load(str(MODEL_PATH))


def underwater_filter(audio, sr):
    cutoff = min(4000, sr / 2 - 1)
    b, a = butter(4, cutoff, btype='low', fs=sr)
    return filtfilt(b, a, audio)


def _extract_features(audio, orig_sr):
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if orig_sr != SR:
        audio = librosa.resample(audio, orig_sr=orig_sr, target_sr=SR)
    audio = underwater_filter(audio, SR)
    mfcc = librosa.feature.mfcc(y=audio, sr=SR, n_mfcc=N_MFCC)
    return mfcc.mean(axis=1).reshape(1, -1)


def _predict(features):
    proba = model.predict_proba(features)[0]
    top_idx = int(np.argmax(proba))
    top_conf = float(proba[top_idx])
    top_label = LABELS[top_idx]

    # Anomaly fallback: если модель не уверена — это аномалия
    if top_conf < ANOMALY_THRESHOLD:
        return "anomaly", top_conf, proba

    return top_label, top_conf, proba


def classify_audio(wav_path, return_candidates=True):
    """Классифицирует аудио-файл."""
    audio, orig_sr = sf.read(str(wav_path))
    features = _extract_features(audio, orig_sr)
    label, confidence, proba = _predict(features)

    result = {"label": label, "confidence": confidence}
    if return_candidates:
        result["candidates"] = [
            {"label": LABELS[i], "confidence": float(proba[i])}
            for i in range(len(LABELS))
        ]
    return result


def classify_bytes(audio_bytes, sr=SR):
    """Классифицирует аудио из байтов (для API)."""
    audio, orig_sr = sf.read(io.BytesIO(audio_bytes))
    features = _extract_features(audio, orig_sr)
    label, confidence, proba = _predict(features)

    return {
        "label": label,
        "confidence": confidence,
        "candidates": [
            {"label": LABELS[i], "confidence": float(proba[i])}
            for i in range(len(LABELS))
        ]
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        path = str(Path(__file__).parent / "data" / "boat" / "boat_000.wav")

    result = classify_audio(path)
    print(f"Файл: {path}")
    print(f"Результат: {result}")