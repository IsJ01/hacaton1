"""Дообучение модели звука: признаки v2, два генератора, аугментации, честная проверка на «чужих» звуках.

Что было не так у v1 (13 усреднённых MFCC, один генератор, срез 4 кГц):
  - модель знала звуки только одного генератора — файлы эмулятора (*_01.wav, *_02.wav) путала с лодкой;
  - фильтр 4 кГц срезал тюленьи свисты 4–8 кГц, тюлень распознавался по остаточному шуму.

Что делаем:
  1. Звуки двух независимых генераторов: A — ml/01_generate_data.py, B — mock-backend/audio_gen.py.
  2. Аугментации, похожие на реальную жизнь буя: шум моря с разным SNR, дальность (срез высоких частот),
     частота дискретизации (телефон 8–16 кГц, гидрофон 44.1 кГц), сдвиг высоты ±15 %, длина записи.
  3. Признаки v2 (backend/audio_features.py — один код с бэкендом).
  4. Проверка, которой нет у v1: обучить на одном генераторе и проверить на другом (cross-generator) —
     ближайшая к «а на реальных звуках?» оценка, пока нет записей с буя. Плюс шумный тест SNR 0–10 дБ.

Запуск (в образе бэкенда — те же версии библиотек, что и при загрузке модели):
  docker run --rm -v "$PWD/..:/repo" -w /repo/ml eco-backend python 06_train_robust.py
"""
import importlib.util
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

import joblib
import librosa
import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfiltfilt
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_score, recall_score
from sklearn.model_selection import StratifiedKFold, cross_val_score

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
import audio_features as af  # noqa: E402

LABELS = ["boat", "gunshot", "seal", "anomaly", "background"]  # порядок классов, как ждёт бэкенд
OUT = Path(__file__).resolve().parent / "models"
LABELS_DIR = Path(__file__).resolve().parent / "data_labeled"  # записи, размеченные инспекторами (выгрузка из бэкенда)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen_a = _load(Path(__file__).resolve().parent / "01_generate_data.py", "gen_a")
gen_b = _load(ROOT / "mock-backend" / "audio_gen.py", "gen_b")

A_FUNCS = {"boat": gen_a.generate_boat_motor, "gunshot": gen_a.generate_gunshot, "seal": gen_a.generate_seal_vocalization,
           "anomaly": gen_a.generate_anomaly, "background": gen_a.generate_background}


def sound_a(label: str, seed: int):
    np.random.seed(seed)
    return A_FUNCS[label](), gen_a.SR


def sound_b(label: str, seed: int):
    """Генератор эмулятора (фона у него нет — берём фон A с другим зерном)."""
    if label == "background":
        return sound_a("background", 10_000 + seed)
    rnd = random.Random(f"{label}-{seed}")
    fn, sr = gen_b.GENERATORS[label], gen_b.SR
    gain = rnd.uniform(0.7, 1.0)
    y = np.array([fn(n / sr, rnd) for n in range(int(sr * gen_b.DUR))]) * gain
    return np.clip(y, -1, 1), sr


def sea_noise(n: int, sr: int, rng: np.random.Generator) -> np.ndarray:
    """Шум моря: фон генератора A, коричневый или розовый шум."""
    kind = rng.integers(3)
    if kind == 0:
        np.random.seed(int(rng.integers(1e9)))
        bg = gen_a.generate_background()
        bg = librosa.resample(bg.astype(np.float64), orig_sr=gen_a.SR, target_sr=sr)
        return np.resize(bg, n)
    w = rng.standard_normal(n)
    if kind == 1:
        y = np.cumsum(w)
        y -= np.convolve(y, np.ones(256) / 256, mode="same")  # коричневый шум без дрейфа
    else:
        f = np.fft.rfftfreq(n)
        spec = np.fft.rfft(w) / np.sqrt(np.maximum(f, 1 / n))
        y = np.fft.irfft(spec, n)
    return y


def augment(y: np.ndarray, sr: int, label: str, rng: np.random.Generator, snr_range=(0, 30)):
    y = np.asarray(y, dtype=np.float64)
    # высота/скорость ±15 %: другой мотор, другой тюлень
    k = rng.uniform(0.85, 1.15)
    y = librosa.resample(y, orig_sr=sr, target_sr=int(sr / k))
    # длина записи 1.2–3 с, случайный сдвиг
    target = int(sr * rng.uniform(1.2, 3.0))
    if len(y) > target:
        start = rng.integers(0, len(y) - target)
        y = y[start:start + target]
    else:
        pad = target - len(y)
        left = rng.integers(0, pad + 1)
        y = np.pad(y, (left, pad - left))
    # шум моря
    noise = sea_noise(len(y), sr, rng)
    if label == "background":
        y = y + noise * rng.uniform(0.2, 1.0) * (np.std(y) + 1e-9) / (np.std(noise) + 1e-9)
    else:
        snr = rng.uniform(*snr_range)
        y = y + noise * (np.std(y) / (np.std(noise) + 1e-9)) * 10 ** (-snr / 20)
    # дальность: срез высоких частот 3–10 кГц
    cutoff = min(rng.uniform(3000, 10000), sr / 2 - 100)
    y = sosfiltfilt(butter(4, cutoff, btype="low", fs=sr, output="sos"), y)
    # аппаратура: частота дискретизации телефона / гидрофона
    out_sr = int(rng.choice([8000, 16000, 22050, 44100]))
    if out_sr < 2 * cutoff:  # не допускаем наложения спектров
        out_sr = max(out_sr, 16000)
    y = librosa.resample(y, orig_sr=sr, target_sr=out_sr)
    return y / (np.max(np.abs(y)) + 1e-9), out_sr


def build(sources, seeds, n_aug, rng, snr_range=(0, 30)):
    X, y = [], []
    for label in LABELS:
        for seed in seeds:
            for src in sources:
                audio, sr = src(label, seed)
                if n_aug == 0:
                    X.append(af.features(audio, sr)[0]); y.append(LABELS.index(label))
                for _ in range(n_aug):
                    a, s = augment(audio, sr, label, rng, snr_range)
                    X.append(af.features(a, s)[0]); y.append(LABELS.index(label))
    return np.array(X), np.array(y)


def v1_features(audio, sr):
    """Признаки старой модели — для сравнения на тех же тестах."""
    audio = np.asarray(audio, dtype=np.float64)
    if sr != 22050:
        audio = librosa.resample(audio, orig_sr=sr, target_sr=22050)
    audio = np.pad(audio, (0, max(0, 2048 - len(audio))))
    audio = sosfiltfilt(butter(4, 4000, btype="low", fs=22050, output="sos"), audio)
    return librosa.feature.mfcc(y=audio, sr=22050, n_mfcc=13).mean(axis=1)


def model():
    return HistGradientBoostingClassifier(max_iter=300, learning_rate=0.08, max_leaf_nodes=24,
                                          l2_regularization=1.0, class_weight="balanced", random_state=42)


def score(clf, X, y, names=LABELS):
    present = sorted(set(y))
    pred = clf.predict(X)
    return {"accuracy": round(float(accuracy_score(y, pred)), 3),
            "f1_macro": round(float(f1_score(y, pred, labels=present, average="macro", zero_division=0)), 3),
            "n": int(len(y)),
            "confusion": {"labels": [names[i] for i in present],
                          "matrix": confusion_matrix(y, pred, labels=present).tolist()}}


def labeled_recordings():
    """Записи, которые инспекторы подтвердили или исправили в дашборде (backend → GET /api/v1/labels/export)."""
    X, y = [], []
    if not LABELS_DIR.is_dir():
        return X, y
    for label in LABELS:
        for wav in sorted((LABELS_DIR / label).glob("*.wav")):
            audio, sr = sf.read(str(wav))
            X.append(af.features(audio, sr)[0]); y.append(LABELS.index(label))
    return X, y


def main():
    t0 = time.time()
    rng = np.random.default_rng(42)
    train_seeds, test_seeds = range(0, 60), range(500, 530)
    report = {}

    # --- 1. как было: v1 на тех же тестах (обучение — только генератор A, как у старой модели) ---
    def v1_set(src, seeds):
        X, y = [], []
        for label in LABELS:
            for s in seeds:
                a, sr = src(label, s); X.append(v1_features(a, sr)); y.append(LABELS.index(label))
        return np.array(X), np.array(y)
    Xa1, ya1 = v1_set(sound_a, train_seeds)
    v1 = model().fit(Xa1, ya1)
    Xb1, yb1 = v1_set(sound_b, test_seeds)
    keep = yb1 != LABELS.index("background")
    report["v1_cross_generator"] = score(v1, Xb1[keep], yb1[keep])
    print("v1 (как было), обучение A → тест B:", report["v1_cross_generator"]["accuracy"])

    # --- 2. v2 cross-generator: обучение только на A с аугментациями → тест на чистом B ---
    Xa, ya = build([sound_a], train_seeds, 3, rng)
    cross = model().fit(Xa, ya)
    Xb, yb = build([sound_b], test_seeds, 0, rng)
    keep = yb != LABELS.index("background")
    report["v2_cross_generator"] = score(cross, Xb[keep], yb[keep])
    print("v2, обучение A → тест B:", report["v2_cross_generator"]["accuracy"])

    # --- 3. финальная модель: A + B + аугментации (+ размеченные инспекторами записи) ---
    Xt, yt = build([sound_a, sound_b], train_seeds, 3, rng)
    Xl, yl = labeled_recordings()
    if Xl:
        Xt, yt = np.vstack([Xt, Xl]), np.concatenate([yt, yl])
    final = model().fit(Xt, yt)
    cv = cross_val_score(model(), Xt, yt, cv=StratifiedKFold(5, shuffle=True, random_state=42), scoring="f1_macro")
    report["cv_f1_mean"], report["cv_f1_std"] = round(float(cv.mean()), 3), round(float(cv.std()), 3)

    # шумный тест на новых зёрнах: SNR 0–10 дБ — буй далеко, море шумит
    Xn, yn = build([sound_a, sound_b], test_seeds, 1, rng, snr_range=(0, 10))
    report["v2_noisy_test"] = score(final, Xn, yn)
    print("v2 финальная, шумный тест SNR 0–10 дБ:", report["v2_noisy_test"]["accuracy"])

    # файлы, которые реально шлёт эмулятор (в т.ч. *_01/_02 — их v1 путала)
    files = []
    for wav in sorted((ROOT / "mock-backend" / "sounds").glob("*.wav")):
        expect = "background" if wav.name.startswith("bg") else wav.name.split("_")[0]
        audio, sr = sf.read(str(wav))
        proba = final.predict_proba(af.features(audio, sr))[0]
        got = LABELS[int(np.argmax(proba))]
        files.append({"file": wav.name, "expected": expect, "model": got, "confidence": round(float(proba.max()), 2)})
    ok = sum(f["expected"] == f["model"] for f in files)
    report["emulator_files"] = {"correct": ok, "total": len(files), "errors": [f for f in files if f["expected"] != f["model"]]}
    print(f"Файлы эмулятора: {ok}/{len(files)} верно")

    OUT.mkdir(exist_ok=True)
    joblib.dump({"model": final, "labels": LABELS, "feature_version": af.VERSION}, OUT / "classifier.pkl")
    pred = final.predict(Xn)
    metrics = {
        "model": "HistGradientBoosting, признаки v2 (backend/audio_features.py)",
        "feature_version": af.VERSION,
        "n_features": int(Xt.shape[1]),
        "n_train": int(len(yt)),
        "train_distribution": {LABELS[k]: v for k, v in sorted(Counter(yt.tolist()).items())},
        "labeled_by_inspectors": len(yl),
        "accuracy": report["v2_noisy_test"]["accuracy"],
        "f1_macro": report["v2_noisy_test"]["f1_macro"],
        "precision_macro": round(float(precision_score(yn, pred, average="macro", zero_division=0)), 3),
        "recall_macro": round(float(recall_score(yn, pred, average="macro", zero_division=0)), 3),
        "n_samples": int(len(yn)),
        "cv_f1_mean": report["cv_f1_mean"], "cv_f1_std": report["cv_f1_std"],
        "anomaly_threshold": 0.6, "n_classes": 5,
        "evaluation": report,
        "train_seconds": round(time.time() - t0),
    }
    (OUT / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Сохранено: {OUT / 'classifier.pkl'}, {OUT / 'metrics.json'} ({metrics['train_seconds']} с)")


if __name__ == "__main__":
    main()
