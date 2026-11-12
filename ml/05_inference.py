"""Инференс: 4-классовая классификация (boat, gunshot, seal, background) + anomaly fallback.

Логика anomaly:
- Модель предсказывает 4 класса: boat, gunshot, seal, background.
- Если top-1 confidence < ANOMALY_THRESHOLD → "anomaly".
- Иначе — top-1 класс.
- В ответе candidates включаем anomaly с вероятностью background (как в контракте).

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

# 4 класса модели (в порядке обучения)
MODEL_LABELS = ["boat", "gunshot", "seal", "background"]

# 4 метки контракта (anomaly вместо background)
CONTRACT_LABELS = ["seal", "boat", "gunshot", "anomaly"]

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
    top_label = MODEL_LABELS[top_idx]

    # Anomaly fallback: если модель не уверена — это аномалия
    if top_conf < ANOMALY_THRESHOLD:
        return "anomaly", top_conf, proba

    return top_label, top_conf, proba


def _build_candidates(proba, final_label):
    """Строит candidates для контракта: seal, boat, gunshot, anomaly.
    anomaly = background + (anomaly fallback weight)"""
    # Матрица пробабилити модели: [boat, gunshot, seal, background]
    # Контракт: seal, boat, gunshot, anomaly
    # anomaly = background (всегда) + поправка если final_label == anomaly
    candidates = []
    for label in CONTRACT_LABELS:
        if label == "seal":
            conf = float(proba[2])  # seal
        elif label == "boat":
            conf = float(proba[0])  # boat
        elif label == "gunshot":
            conf = float(proba[1])  # gunshot
        elif label == "anomaly":
            conf = float(proba[3])  # background
            if final_label == "anomaly":
                conf = max(conf, 0.5)  # поднимаем anomaly confidence
        candidates.append({"label": label, "confidence": round(conf, 2)})
    return sorted(candidates, key=lambda c: -c["confidence"])


def classify_audio(wav_path, return_candidates=True):
    """Классифицирует аудио-файл."""
    audio, orig_sr = sf.read(str(wav_path))
    features = _extract_features(audio, orig_sr)
    label, confidence, proba = _predict(features)

    candidates = _build_candidates(proba, label)

    result = {"label": label, "confidence": confidence}
    if return_candidates:
        result["candidates"] = candidates
    return result


def classify_bytes(audio_bytes, sr=SR):
    """Классифицирует аудио из байтов (для API)."""
    audio, orig_sr = sf.read(io.BytesIO(audio_bytes))
    features = _extract_features(audio, orig_sr)
    label, confidence, proba = _predict(features)

    candidates = _build_candidates(proba, label)

    return {"label": label, "confidence": confidence, "candidates": candidates}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        path = str(Path(__file__).parent / "data" / "boat" / "boat_000.wav")

    result = classify_audio(path)
    print(f"Файл: {path}")
    print(f"Результат: {result}")