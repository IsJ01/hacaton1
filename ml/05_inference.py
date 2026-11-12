"""Инференс: классификация аудио-файла.

Возвращает ClassificationResult в формате API-контракта:
{
  "label": "seal" | "boat" | "gunshot" | "anomaly",
  "confidence": 0.94,
  "candidates": [...]
}
"""
import joblib
import librosa
import soundfile as sf
import numpy as np
from scipy.signal import butter, filtfilt
from pathlib import Path

MODEL_PATH = Path(__file__).parent / "models" / "classifier.pkl"
SR = 22050
N_MFCC = 13

model = joblib.load(str(MODEL_PATH))


def underwater_filter(audio, sr):
    cutoff = min(4000, sr / 2 - 1)
    b, a = butter(4, cutoff, btype='low', fs=sr)
    return filtfilt(b, a, audio)


def classify_audio(wav_path, return_candidates=True):
    """Классифицирует аудио-файл.

    Returns dict в формате ClassificationResult.
    """
    audio, orig_sr = sf.read(str(wav_path))
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if orig_sr != SR:
        audio = librosa.resample(audio, orig_sr=orig_sr, target_sr=SR)

    audio = underwater_filter(audio, SR)
    mfcc = librosa.feature.mfcc(y=audio, sr=SR, n_mfcc=N_MFCC)
    features = mfcc.mean(axis=1).reshape(1, -1)

    proba = model.predict_proba(features)[0]
    # proba[0] = background, proba[1] = boat

    if proba[1] > proba[0]:
        label = "boat"
        confidence = float(proba[1])
    else:
        label = "background"
        confidence = float(proba[0])

    result = {
        "label": label,
        "confidence": confidence,
    }

    if return_candidates:
        result["candidates"] = [
            {"label": "boat", "confidence": float(proba[1])},
            {"label": "background", "confidence": float(proba[0])}
        ]

    return result


def classify_bytes(audio_bytes, sr=SR):
    """Классифицирует аудио из байтов (для API)."""
    import io
    audio, orig_sr = sf.read(io.BytesIO(audio_bytes))
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if orig_sr != sr:
        audio = librosa.resample(audio, orig_sr=orig_sr, target_sr=sr)
    audio = underwater_filter(audio, sr)
    mfcc = librosa.feature.mfcc(y=audio, sr=sr, n_mfcc=N_MFCC)
    features = mfcc.mean(axis=1).reshape(1, -1)
    proba = model.predict_proba(features)[0]
    label = "boat" if proba[1] > proba[0] else "background"
    return {
        "label": label,
        "confidence": float(max(proba)),
        "candidates": [
            {"label": "boat", "confidence": float(proba[1])},
            {"label": "background", "confidence": float(proba[0])}
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