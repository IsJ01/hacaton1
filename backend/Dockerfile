FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NUMBA_CACHE_DIR=/tmp/numba \
    MODEL_PATH=/ml/models/classifier.pkl \
    FORECAST_MODEL_PATH=/ml/models/seal_forecast.pkl \
    LIBRARY_DIR=/ml/data \
    DATA_DIR=/app/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# контейнер работает не от root — файлы должны читаться всеми
RUN chmod -R a+rX /app

EXPOSE 8001
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
