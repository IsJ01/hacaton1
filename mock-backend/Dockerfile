FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BACKEND_URL=http://localhost:8001

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
# по умолчанию — эмулятор буя; мок бэкенда: uvicorn main:app --port 8001
CMD ["uvicorn", "emulator:app", "--host", "0.0.0.0", "--port", "8000"]
