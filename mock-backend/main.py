"""Эмулирующий бэкенд «Эко-Щит Каспия» — реализует API-контракт целиком.

Классификатор — заглушка: для файлов из библиотеки класс берётся из имени
файла (seal_01.wav → seal), для чужого аудио — случайный класс.
Хранилище — в памяти (+ wav-файлы в data/audio). Перезапуск = чистое состояние.

Настройки через переменные окружения:
  SEED_EVENTS   — сколько событий создать при старте (по умолчанию 20)
  AUTO_EMIT_SEC — если > 0, каждые N секунд само создаётся событие от случайного буя
"""
import asyncio
import base64
import binascii
import os
import random
import secrets
import shutil
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum
from itertools import count
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from audio_gen import generate_library

BASE = Path(__file__).parent
LIBRARY = BASE / "library"
AUDIO = BASE / "data" / "audio"

SEED_EVENTS = int(os.getenv("SEED_EVENTS", "20"))
AUTO_EMIT_SEC = float(os.getenv("AUTO_EMIT_SEC", "0"))


# ---------- Модели (как в контракте) ----------
class SoundLabel(str, Enum):
    seal = "seal"
    boat = "boat"
    gunshot = "gunshot"
    anomaly = "anomaly"


class Geo(BaseModel):
    lat: float
    lon: float


class Buoy(BaseModel):
    buoy_id: str
    name: str
    geo: Geo


class Candidate(BaseModel):
    label: SoundLabel
    confidence: float


class ClassificationResult(BaseModel):
    label: SoundLabel
    confidence: float
    candidates: list[Candidate]


class SoundEvent(BaseModel):
    event_id: str
    buoy_id: Optional[str] = None
    source: str = Field(examples=["buoy"], description="buoy | library | user")
    timestamp: str
    classification: ClassificationResult
    audio_url: str


class BuoyList(BaseModel):
    buoys: list[Buoy]


class AudioPayload(BaseModel):
    format: str = "wav"
    data_base64: str


class EventIn(BaseModel):
    buoy_id: str = Field(examples=["b-001"])
    timestamp: Optional[str] = Field(None, examples=["2026-11-12T11:36:00Z"])
    audio: Optional[AudioPayload] = None

    model_config = {"json_schema_extra": {"examples": [{"buoy_id": "b-001", "timestamp": "2026-11-12T11:36:00Z"}]}}


class EventCreated(BaseModel):
    event_id: str
    status: str = "classified"
    event: SoundEvent


class EventList(BaseModel):
    total: int
    events: list[SoundEvent]


class ClassifyIn(BaseModel):
    buoy_id: Optional[str] = Field(None, examples=["b-001"])
    file_name: Optional[str] = Field(None, examples=["seal_01.wav"])


class ClassifyOut(BaseModel):
    event_id: str
    buoy_id: Optional[str] = None
    classification: ClassificationResult
    audio_url: str


class ErrorOut(BaseModel):
    error: str
    message: str


class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str):
        self.status, self.error, self.message = status, error, message


# ---------- Данные ----------
# Координаты — побережье Дагестана (в примере контракта стояли 68.97/33.07, это Мурманск).
BUOYS: dict[str, Buoy] = {
    b.buoy_id: b
    for b in [
        Buoy(buoy_id="b-001", name="Буй №1", geo=Geo(lat=43.0100, lon=47.5500)),
        Buoy(buoy_id="b-002", name="Буй №2", geo=Geo(lat=43.3200, lon=47.6200)),
        Buoy(buoy_id="b-003", name="Буй №3", geo=Geo(lat=43.6500, lon=47.5800)),
        Buoy(buoy_id="b-004", name="Буй №4", geo=Geo(lat=44.0500, lon=47.6500)),
        Buoy(buoy_id="b-005", name="Буй №5", geo=Geo(lat=44.4500, lon=47.2000)),
    ]
}
EVENTS: dict[str, dict] = {}  # event_id -> SoundEvent (dict)
_cls_seq, _user_seq = count(1), count(1)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: str, field: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.strip().replace(" ", "+").replace("Z", "+00:00"))
    except ValueError:
        raise ApiError(400, "bad_request", f"'{field}' must be ISO-8601, e.g. 2026-11-12T11:36:00Z")
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def require_buoy(buoy_id: Optional[str]) -> None:
    if buoy_id is not None and buoy_id not in BUOYS:
        raise ApiError(404, "not_found", "Buoy not found")


def library_files() -> list[Path]:
    return sorted(LIBRARY.glob("*.wav"))


def label_from_name(name: str) -> Optional[str]:
    prefix = name.lower().split("_")[0].split(".")[0]
    return prefix if prefix in SoundLabel.__members__ else None


def label_from_bytes(raw: bytes) -> Optional[str]:
    """Если прислали байт-в-байт файл из библиотеки — «узнаём» его класс."""
    for f in library_files():
        if f.stat().st_size == len(raw) and f.read_bytes() == raw:
            return label_from_name(f.name)
    return None


def fake_classify(true_label: Optional[str] = None) -> dict:
    """Заглушка модели: уверенный топ-1 + остаток, размазанный по другим классам."""
    labels = [l.value for l in SoundLabel]
    label = true_label or random.choices(labels, weights=[45, 30, 10, 15])[0]
    conf = round(random.uniform(0.72, 0.98), 2)
    others = [l for l in labels if l != label]
    cuts = sorted(random.random() for _ in range(len(others) - 1))
    parts = [b - a for a, b in zip([0.0] + cuts, cuts + [1.0])]
    cands = [{"label": label, "confidence": conf}]
    cands += [{"label": l, "confidence": round((1 - conf) * p, 2)} for l, p in zip(others, parts)]
    cands.sort(key=lambda c: -c["confidence"])
    return {"label": label, "confidence": conf, "candidates": cands}


def save_event(event_id: str, buoy_id: Optional[str], source: str, timestamp: str, classification: dict) -> dict:
    ev = {
        "event_id": event_id,
        "buoy_id": buoy_id,
        "source": source,
        "timestamp": timestamp,
        "classification": classification,
        "audio_url": f"/audio/{event_id}.wav",
    }
    EVENTS[event_id] = ev
    return ev


def event_from_library(event_id: str, buoy_id: Optional[str], source: str, timestamp: str,
                       file: Optional[Path] = None) -> dict:
    file = file or random.choice(library_files())
    shutil.copyfile(file, AUDIO / f"{event_id}.wav")
    return save_event(event_id, buoy_id, source, timestamp, fake_classify(label_from_name(file.name)))


def new_buoy_event_id() -> str:
    return f"evt-{secrets.token_hex(2)}{secrets.token_hex(1)}"


async def auto_emitter():
    while True:
        await asyncio.sleep(AUTO_EMIT_SEC)
        event_from_library(new_buoy_event_id(), random.choice(list(BUOYS)), "buoy", now_iso())


@asynccontextmanager
async def lifespan(_: FastAPI):
    generate_library(LIBRARY)
    shutil.rmtree(AUDIO, ignore_errors=True)
    AUDIO.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    for i in range(SEED_EVENTS):  # история за последние сутки
        ts = now - timedelta(minutes=random.randint(1, 24 * 60))
        event_from_library(new_buoy_event_id(), random.choice(list(BUOYS)), "buoy", to_iso(ts))
    task = asyncio.create_task(auto_emitter()) if AUTO_EMIT_SEC > 0 else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Эко-Щит Каспия — эмулирующий бэкенд", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------- Ошибки в формате контракта: { "error": ..., "message": ... } ----------
@app.exception_handler(ApiError)
async def api_error_handler(_: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status, content={"error": exc.error, "message": exc.message})


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError):
    e = exc.errors()[0]
    where = ".".join(str(x) for x in e["loc"] if x != "body")
    return JSONResponse(status_code=422, content={"error": "validation_error", "message": f"{where}: {e['msg']}"})


@app.exception_handler(StarletteHTTPException)
async def http_handler(_: Request, exc: StarletteHTTPException):
    code = "not_found" if exc.status_code == 404 else "error"
    return JSONResponse(status_code=exc.status_code, content={"error": code, "message": str(exc.detail)})


NOT_FOUND = {404: {"model": ErrorOut}}


# ---------- 1–2. Буи ----------
@app.get("/api/v1/buoys", response_model=BuoyList, tags=["buoys"], summary="Список буёв на карту")
def list_buoys():
    return {"buoys": list(BUOYS.values())}


@app.get("/api/v1/buoys/{buoy_id}", response_model=Buoy, responses=NOT_FOUND, tags=["buoys"], summary="Один буй")
def get_buoy(buoy_id: str):
    require_buoy(buoy_id)
    return BUOYS[buoy_id]


# ---------- 3–5. События ----------
@app.post("/api/v1/events", response_model=EventCreated, status_code=201, responses=NOT_FOUND,
          tags=["events"], summary="Событие от буя → классификация")
def create_event(body: EventIn):
    require_buoy(body.buoy_id)
    ts = to_iso(parse_ts(body.timestamp, "timestamp")) if body.timestamp else now_iso()
    event_id = new_buoy_event_id()
    if body.audio is None:
        ev = event_from_library(event_id, body.buoy_id, "buoy", ts)
    else:
        try:
            raw = base64.b64decode(body.audio.data_base64, validate=True)
        except (binascii.Error, ValueError):
            raise ApiError(400, "bad_request", "audio.data_base64 is not valid base64")
        (AUDIO / f"{event_id}.wav").write_bytes(raw)
        ev = save_event(event_id, body.buoy_id, "buoy", ts, fake_classify(label_from_bytes(raw)))
    return {"event_id": event_id, "status": "classified", "event": ev}


@app.get("/api/v1/events", response_model=EventList, tags=["events"], summary="История событий с фильтрами")
def list_events(
    buoy_id: Optional[str] = None,
    label: Optional[SoundLabel] = None,
    from_: Optional[str] = Query(None, alias="from", description="ISO-8601"),
    to: Optional[str] = Query(None, description="ISO-8601"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    dt_from = parse_ts(from_, "from") if from_ else None
    dt_to = parse_ts(to, "to") if to else None
    items = []
    for ev in EVENTS.values():
        if buoy_id and ev["buoy_id"] != buoy_id:
            continue
        if label and ev["classification"]["label"] != label.value:
            continue
        ts = parse_ts(ev["timestamp"], "timestamp")
        if (dt_from and ts < dt_from) or (dt_to and ts > dt_to):
            continue
        items.append(ev)
    items.sort(key=lambda e: e["timestamp"], reverse=True)  # новые сверху
    return {"total": len(items), "events": items[offset:offset + limit]}


@app.get("/api/v1/events/{event_id}", response_model=SoundEvent, responses=NOT_FOUND,
         tags=["events"], summary="Детали события")
def get_event(event_id: str):
    if event_id not in EVENTS:
        raise ApiError(404, "not_found", "Event not found")
    return EVENTS[event_id]


# ---------- 6–7. Классификация ----------
def _classify_out(ev: dict) -> dict:
    return {k: ev[k] for k in ("event_id", "buoy_id", "classification", "audio_url")}


@app.post("/api/v1/classify", response_model=ClassifyOut, responses=NOT_FOUND,
          tags=["classify"], summary="Классификация файла из библиотеки")
def classify_library(body: ClassifyIn = ClassifyIn()):
    require_buoy(body.buoy_id)
    file = None
    if body.file_name:
        file = LIBRARY / Path(body.file_name).name
        if not file.is_file():
            names = ", ".join(f.name for f in library_files())
            raise ApiError(404, "not_found", f"File not found in library. Available: {names}")
    ev = event_from_library(f"evt-cls-{next(_cls_seq)}", body.buoy_id, "library", now_iso(), file)
    return _classify_out(ev)


@app.post("/api/v1/classify/upload", response_model=ClassifyOut, responses=NOT_FOUND,
          tags=["classify"], summary="Загрузка файла с телефона")
async def classify_upload(file: UploadFile = File(...), buoy_id: Optional[str] = Form(None)):
    buoy_id = buoy_id or None
    require_buoy(buoy_id)
    raw = await file.read()
    if not raw:
        raise ApiError(400, "bad_request", "file is empty")
    event_id = f"evt-user-{next(_user_seq)}"
    (AUDIO / f"{event_id}.wav").write_bytes(raw)
    # если имя файла начинается с класса (seal_xxx.wav) — «узнаём» его, иначе случайно
    ev = save_event(event_id, buoy_id, "user", now_iso(), fake_classify(label_from_bytes(raw) or label_from_name(file.filename or "")))
    return _classify_out(ev)


@app.get("/api/v1/library", tags=["classify"], summary="(доп.) Имена файлов библиотеки для /classify")
def list_library():
    return {"files": [f.name for f in library_files()]}


# ---------- 8. Аудио ----------
@app.get("/audio/{event_id}.wav", tags=["audio"], summary="Скачать/проиграть звук",
         responses={200: {"content": {"audio/wav": {}}}, **NOT_FOUND}, response_class=FileResponse)
def get_audio(event_id: str):
    path = AUDIO / f"{Path(event_id).name}.wav"
    if not path.is_file():
        raise ApiError(404, "not_found", "Audio not found")
    return FileResponse(path, media_type="audio/wav")
