# CASPIAN SENTINEL — ML-модуль (аудио-классификация)

Классификация подводных акустических сигналов: **boat (мотор) vs background (фон)**.

## Быстрый старт

```bash
pip install -r requirements.txt

# 1. Сгенерировать синтетический датасет
python 01_generate_data.py

# 2. Предобработка (underwater filter + MFCC)
python 02_preprocess.py

# 3. Обучение (XGBoost)
python 03_train.py

# 4. Оценка метрик
python 04_evaluate.py

# 5. Инференс на конкретном файле
python 05_inference.py data/boat/boat_000.wav
```

## Архитектура

```
аудио → underwater filter (low-pass) → MFCC (13 коэффициентов) → XGBoost → label + confidence
```

## Метрики (после обучения)

См. `models/metrics.json`. Пример: accuracy, precision, recall, F1, confusion matrix, cross-validation.

## Честные оговорки

- Датасет синтетический (генератор + underwater model). Field validation — следующий этап.
- Модель обнаруживает в пределах 50–100 м. На больших расстояниях сигнал маскируется фоном.
- Train/test split по source recordings (избегаем data leakage).
- Cross-validation: 5-fold, reported mean ± std.

## Интеграция с backend

`05_inference.py` exposes `classify_audio(wav_path)` и `classify_bytes(audio_bytes)`.
Возвращает `ClassificationResult` в формате API-контракта.