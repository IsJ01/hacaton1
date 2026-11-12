"""Эмулятор буя с управлением через Swagger.

    ты в Swagger (:8000)  ──►  эмулятор берёт файл из sounds/  ──►  POST на бэкенд (:8001), как настоящий буй

  POST /send/sound     — выбрать буй и звуковой файл (по имени или номеру) → POST {BACKEND}/api/v1/events
  POST /send/salinity  — буй + уровень солёности → POST {BACKEND}/api/v1/salinity
  POST /send/satellite — снимок из sat_library/ + координаты → POST {BACKEND}/api/v1/satellite
  GET  /sounds, /images — что лежит в папках (с порядковыми номерами)
  GET  /buoys          — буи с бэкенда
  GET  /health, /log   — доступен ли бэкенд, журнал отправок

Звуки: положи свои файлы в папку sounds/ (wav, mp3, ogg, flac, m4a) — они сразу появятся в GET /sounds.
Адрес бэкенда: переменная окружения BACKEND_URL (по умолчанию http://localhost:8001).
"""
import base64
import os
import random
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

BASE = Path(__file__).parent
SOUNDS = BASE / "sounds"
IMAGES = BASE / "sat_library"
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8001").rstrip("/")
AUDIO_EXT = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac", ".webm"}
IMAGE_EXT = {".jpg", ".jpeg", ".png"}
LOG: deque = deque(maxlen=200)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def list_files(folder: Path, exts: set) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in exts)


def describe(files: list[Path]) -> list[dict]:
    return [{"index": i, "file_name": f.name, "size_bytes": f.stat().st_size} for i, f in enumerate(files, 1)]


def error(status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message, **extra})


def pick(files: list[Path], name: Optional[str], index: Optional[int], what: str):
    """Файл по имени, по порядковому номеру (с 1) или случайный. Возвращает (Path, None) или (None, ошибка)."""
    if not files:
        return None, error(404, "not_found", f"No {what} files in the folder")
    if name:
        for f in files:
            if f.name == Path(name).name:
                return f, None
        return None, error(404, "not_found", f"File '{name}' not found", available=describe(files))
    if index is not None:
        if 1 <= index <= len(files):
            return files[index - 1], None
        return None, error(404, "not_found", f"index must be 1..{len(files)}", available=describe(files))
    return random.choice(files), None


async def send(kind: str, path: str, sent: dict, **kwargs) -> JSONResponse:
    """POST на бэкенд; в ответе — что отправили и что ответил бэкенд."""
    url = BACKEND_URL + path
    entry = {"time": now_iso(), "kind": kind, "to": url, "sent": sent}
    LOG.appendleft(entry)
    try:
        r = await app.state.http.post(url, **kwargs)
    except httpx.HTTPError as e:
        entry.update(backend_status=None, backend_response=f"недоступен: {type(e).__name__}")
        print(f"[{kind}] {sent} → бэкенд {BACKEND_URL} недоступен ({type(e).__name__})")
        return error(502, "backend_unavailable", f"Cannot reach backend at {BACKEND_URL}", to=url, sent=sent)
    try:
        body = r.json()
    except ValueError:
        body = r.text[:500]
    entry.update(backend_status=r.status_code, backend_response=body)
    print(f"[{kind}] {sent} → {r.status_code}")
    result = {"to": url, "sent": sent, "backend_status": r.status_code, "backend_response": body}
    if r.status_code >= 400:
        return JSONResponse(status_code=502, content={
            "error": "backend_error", "message": f"Backend answered {r.status_code}", **result})
    return JSONResponse(status_code=200, content=result)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=30)
    print(f"Эмулятор буя: звуков в sounds/ — {len(list_files(SOUNDS, AUDIO_EXT))}, отправка на {BACKEND_URL}")
    yield
    await app.state.http.aclose()


app = FastAPI(
    title="Эко-Щит Каспия — эмулятор буя",
    version="3.0.0",
    lifespan=lifespan,
    description=f"Отправляет события на бэкенд **{BACKEND_URL}** так, как это делал бы настоящий буй.\n\n"
                "Список файлов и их номера — `GET /sounds`. Свои звуки клади в папку `sounds/`.",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# выпадающие списки в Swagger (значения на момент запуска; новые файлы всё равно принимаются по имени/номеру)
_SOUND_NAMES = [f.name for f in list_files(SOUNDS, AUDIO_EXT)]
_IMAGE_NAMES = [f.name for f in list_files(IMAGES, IMAGE_EXT)]
_BUOYS = [b.strip() for b in os.getenv("BUOYS", "b-001,b-002,b-003,b-004,b-005").split(",") if b.strip()]


def dropdown(values: list[str]) -> Optional[dict]:
    return {"enum": values} if values else None


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs")


# ---------- Звук ----------
@app.get("/sounds", tags=["звук"], summary="Звуковые файлы в папке sounds/ (с порядковыми номерами)")
def sounds():
    return {"folder": str(SOUNDS), "files": describe(list_files(SOUNDS, AUDIO_EXT))}


@app.post("/send/sound", tags=["звук"], summary="Буй отправляет звук → POST /api/v1/events на бэкенд")
async def send_sound(
    buoy_id: str = Query(_BUOYS[0], description="От какого буя", json_schema_extra=dropdown(_BUOYS)),
    file: str = Query("", description="Имя файла из sounds/", json_schema_extra=dropdown(_SOUND_NAMES)),
    index: Optional[int] = Query(None, ge=1, description="Или порядковый номер из GET /sounds (с 1)"),
    timestamp: Optional[str] = Query(None, description="ISO-8601; пусто = сейчас"),
):
    """Не указаны ни `file`, ни `index` — берётся случайный файл. Указаны оба — используется `file`."""
    f, err = pick(list_files(SOUNDS, AUDIO_EXT), file, index, "sound")
    if err:
        return err
    raw = f.read_bytes()
    ts = timestamp or now_iso()
    payload = {"buoy_id": buoy_id, "timestamp": ts,
               "audio": {"format": f.suffix.lstrip(".").lower(), "data_base64": base64.b64encode(raw).decode()}}
    sent = {"buoy_id": buoy_id, "timestamp": ts, "file_name": f.name, "audio_bytes": len(raw)}
    return await send("sound", "/api/v1/events", sent, json=payload)


# ---------- Солёность ----------
@app.post("/send/salinity", tags=["солёность"], summary="Буй отправляет солёность → POST /api/v1/salinity на бэкенд")
async def send_salinity(
    buoy_id: str = Query(_BUOYS[0], description="От какого буя", json_schema_extra=dropdown(_BUOYS)),
    salinity: float = Query(..., description="Уровень солёности, ‰", examples=[12.5]),
    timestamp: Optional[str] = Query(None, description="ISO-8601; пусто = сейчас"),
):
    payload = {"buoy_id": buoy_id, "salinity": salinity, "timestamp": timestamp or now_iso()}
    return await send("salinity", "/api/v1/salinity", payload, json=payload)


# ---------- Спутник ----------
@app.get("/images", tags=["спутник"], summary="Снимки в папке sat_library/ (с порядковыми номерами)")
def images():
    return {"folder": str(IMAGES), "files": describe(list_files(IMAGES, IMAGE_EXT))}


@app.post("/send/satellite", tags=["спутник"], summary="Спутник отправляет снимок → POST /api/v1/satellite на бэкенд")
async def send_satellite(
    lat: float = Query(43.65, ge=-90, le=90, description="Широта снимка"),
    lon: float = Query(47.58, ge=-180, le=180, description="Долгота снимка"),
    file: str = Query("", description="Имя файла из sat_library/", json_schema_extra=dropdown(_IMAGE_NAMES)),
    index: Optional[int] = Query(None, ge=1, description="Или порядковый номер из GET /images (с 1)"),
    timestamp: Optional[str] = Query(None, description="ISO-8601; пусто = сейчас"),
):
    f, err = pick(list_files(IMAGES, IMAGE_EXT), file, index, "image")
    if err:
        return err
    raw = f.read_bytes()
    ts = timestamp or now_iso()
    mime = "image/png" if f.suffix.lower() == ".png" else "image/jpeg"
    sent = {"file_name": f.name, "geo": {"lat": lat, "lon": lon}, "timestamp": ts, "image_bytes": len(raw)}
    return await send("satellite", "/api/v1/satellite", sent,
                      data={"geo[lat]": str(lat), "geo[lon]": str(lon), "timestamp": ts},
                      files={"file": (f.name, raw, mime)})


# ---------- Служебное ----------
@app.get("/buoys", tags=["служебное"], summary="Буи, которые знает бэкенд (GET /api/v1/buoys)")
async def buoys():
    try:
        r = await app.state.http.get(BACKEND_URL + "/api/v1/buoys", timeout=5)
        return JSONResponse(status_code=r.status_code, content=r.json())
    except (httpx.HTTPError, ValueError):
        return error(502, "backend_unavailable", f"Cannot reach backend at {BACKEND_URL}")


@app.get("/health", tags=["служебное"], summary="Куда шлём и доступен ли бэкенд")
async def health():
    try:
        r = await app.state.http.get(BACKEND_URL + "/api/v1/buoys", timeout=5)
        backend = "ok" if r.status_code == 200 else f"http {r.status_code}"
    except httpx.HTTPError as e:
        backend = f"unavailable ({type(e).__name__})"
    return {"emulator": "ok", "backend_url": BACKEND_URL, "backend": backend,
            "sounds": len(list_files(SOUNDS, AUDIO_EXT)), "images": len(list_files(IMAGES, IMAGE_EXT))}


@app.get("/log", tags=["служебное"], summary="Журнал последних отправок")
def log(limit: int = Query(20, ge=1, le=200)):
    return {"total": len(LOG), "items": list(LOG)[:limit]}
