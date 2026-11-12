"""Оценка модели: вывод метрик и confusion matrix."""
import json
from pathlib import Path

with open(Path(__file__).parent / "models" / "metrics.json") as f:
    m = json.load(f)

print("=" * 50)
print("CASPIAN SENTINEL — Аудио-классификация")
print("Метрики модели (boat vs background)")
print("=" * 50)
print(f"Accuracy:  {m['accuracy']:.3f}")
print(f"Precision: {m['precision']:.3f}")
print(f"Recall:    {m['recall']:.3f}")
print(f"F1-score:  {m['f1']:.3f}")
print(f"CV F1:     {m['cv_f1_mean']:.3f} ± {m['cv_f1_std']:.3f}")
print(f"Samples:   {m['n_samples']}")
print(f"Features:  {m['n_features']}")
print()
cm = m['confusion_matrix']
print("Confusion matrix:")
print(f"  TN={cm[0][0]}  FP={cm[0][1]}")
print(f"  FN={cm[1][0]}  TP={cm[1][1]}")
print()
print("Интерпретация:")
print(f"  TP (boat распознан как boat): {cm[1][1]}")
print(f"  TN (фон распознан как фон):  {cm[0][0]}")
print(f"  FP (фон mistaken за boat):   {cm[0][1]}")
print(f"  FN (boat mistaken за фон):   {cm[1][0]}")