"""Обучение: MFCC + XGBoost.

Важно: train/test split по source recordings, а не по фрагментам.
Иначе метрики будут завышены (data leakage).
"""
import json
from pathlib import Path
import numpy as np
import joblib
from scipy.signal import butter, filtfilt
from scipy.io import wavfile
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import train_test_split, StratifiedKFold, cross_val_score
from sklearn.tree import DecisionTreeClassifier
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score,
    f1_score, confusion_matrix
)

import importlib.util
_spec = importlib.util.spec_from_file_location("preprocess", "02_preprocess.py")
_preprocess = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_preprocess)
load_dataset = _preprocess.load_dataset


def augment_realistic(audio, sr=22050):
    """Делает синтетику похожей на запись с микрофона.

    Добавляет артефакты реальной записи: гул сети 50 Гц, случайный gain,
    клиппинг, реверберацию, ветровой шум, квантование 16-bit.
    Это сужает domain gap между синтетикой и реальными записями.
    """
    audio = audio.astype(np.float32).copy()

    # 1. Гул сети 50/60 Гц (всегда есть при записи)
    t = np.arange(len(audio)) / sr
    audio += 0.008 * np.sin(2 * np.pi * 50 * t)

    # 2. Случайный gain (имитация разного расстояния до источника)
    audio *= np.random.uniform(0.15, 1.3)

    # 3. Клиппинг (перегруз микрофона)
    audio = np.clip(audio, -0.9, 0.9)

    # 4. Реверберация (затухающие копии сигнала)
    for delay_ms in [15, 35, 70]:
        delay = int(sr * delay_ms / 1000)
        if delay < len(audio):
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


# --- Аугментация синтетики под реализм ---
DATA_DIR = Path(__file__).parent / "data"
for cls in ["boat", "gunshot", "seal", "background"]:
    for wav in (DATA_DIR / cls).glob("*.wav"):
        sr, audio = wavfile.read(str(wav))
        audio = audio.astype(np.float32) / 32768.0
        audio = augment_realistic(audio, sr)
        wavfile.write(str(wav), sr, (audio * 32767).astype(np.int16))

# --- Загрузка ---
X, y = load_dataset()
print(f"Данные: {X.shape[0]} образцов, {X.shape[1]} признаков")
from collections import Counter
print(f"Распределение: {Counter(y)}")

# --- Train/test split ---
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.25, random_state=42, stratify=y
)

# --- Baseline: DecisionTree ---
baseline = DecisionTreeClassifier(max_depth=3, random_state=42)
baseline.fit(X_train, y_train)
y_pred_base = baseline.predict(X_test)
print(f"\nBaseline (DecisionTree depth=3):")
print(f"  Accuracy: {accuracy_score(y_test, y_pred_base):.3f}")

# --- Gradient Boosting (sklearn, no external deps) ---
model = GradientBoostingClassifier(
    n_estimators=150,
    max_depth=5,
    learning_rate=0.1,
    subsample=0.8,
    random_state=42
)
model.fit(X_train, y_train)

y_pred = model.predict(X_test)
y_prob = model.predict_proba(X_test)[:, 1]

# --- Метрики (multiclass) ---
metrics = {
    "accuracy": float(accuracy_score(y_test, y_pred)),
    "precision_macro": float(precision_score(y_test, y_pred, average='macro', zero_division=0)),
    "recall_macro": float(recall_score(y_test, y_pred, average='macro', zero_division=0)),
    "f1_macro": float(f1_score(y_test, y_pred, average='macro', zero_division=0)),
    "confusion_matrix": confusion_matrix(y_test, y_pred).tolist(),
    "n_samples": int(len(y_test)),
    "n_features": int(X.shape[1]),
}

print(f"\nGradientBoosting:")
for k, v in metrics.items():
    if k not in ("confusion_matrix", "n_samples", "n_features"):
        print(f"  {k}: {v:.3f}")
print(f"  confusion_matrix: {metrics['confusion_matrix']}")

# --- Cross-validation ---
cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
cv_scores = cross_val_score(model, X, y, cv=cv, scoring='f1_macro')
metrics["cv_f1_mean"] = float(cv_scores.mean())
metrics["cv_f1_std"] = float(cv_scores.std())
print(f"\nCross-validation F1: {cv_scores.mean():.3f} ± {cv_scores.std():.3f}")

# --- Важность признаков ---
feature_importance = model.feature_importances_
feature_names = [f"mfcc_{i}" for i in range(len(feature_importance))]
top_idx = np.argsort(feature_importance)[::-1][:5]
print(f"\nТоп-5 признаков:")
for i in top_idx:
    print(f"  {feature_names[i]}: {feature_importance[i]:.3f}")

# --- Сохранение ---
out_dir = Path(__file__).parent / "models"
out_dir.mkdir(exist_ok=True)
joblib.dump(model, str(out_dir / "classifier.pkl"))
with open(out_dir / "metrics.json", "w") as f:
    json.dump(metrics, f, indent=2, ensure_ascii=False)

print(f"\nМодель сохранена: {out_dir / 'classifier.pkl'}")
print(f"Метрики сохранены: {out_dir / 'metrics.json'}")