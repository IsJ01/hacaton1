# CASPIAN SENTINEL — ML-модуль (аудио-классификация)

Классификация подводных акустических сигналов: **boat (мотор) vs background (фон)**.

## Модель v2 (дообучение) — актуальная

```bash
# в образе бэкенда: те же версии библиотек, что и при загрузке модели
docker run --rm -e NUMBA_CACHE_DIR=/tmp/numba -v "$PWD/..:/repo" -w /repo/ml eco-backend python 06_train_robust.py
```

- Признаки v2 — `backend/audio_features.py` (один код для обучения и бэкенда): 20 MFCC со средним и разбросом,
  динамика, форма спектра, огибающая (импульс выстрела), доли энергии по полосам. Срез 8 кГц вместо 4 кГц —
  иначе тюленьи свисты 4–8 кГц пропадали.
- Данные: два независимых генератора (этот и `mock-backend/audio_gen.py`) + аугментации: шум моря SNR 0–30 дБ,
  дальность (срез 3–10 кГц), частота дискретизации 8–44.1 кГц, высота ±15 %, длина 1.2–3 с.
- Честная проверка: обучили на генераторе A → проверили на B (v1 против v2), шумный тест SNR 0–10 дБ,
  файлы эмулятора. Всё пишется в `models/metrics.json` → вкладка «О системе».
- Разметка инспекторов: `GET http://localhost:8001/api/v1/labels/export` → распаковать в `ml/data_labeled/`
  (папки по классам) → перезапустить обучение: записи с моря войдут в обучающую выборку.
- Бэкенд читает новую модель сам (формат `{"model", "labels", "feature_version": 2}`), старую v1 тоже понимает.
  После обучения: `cd ../backend && bash docker.sh` (модель подключается папкой, пересобирать не нужно).

## Быстрый старт (v1, исторически)

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