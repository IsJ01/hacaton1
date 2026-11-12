"""Основной бэкенд «Эко-Щит Каспия» — контракт v2, порт 8001.

    эмулятор буя (:8000) ──POST──► этот бэкенд (:8001) ──GET──► фронт (:3000)

  звук      → обученная модель (sound.py)              → SoundEvent
  солёность → сравнение с нормой                         → WaterEvent (illegal_discharge)
  снимок    → поиск шлейфа на воде (satellite.py)        → WaterEvent (illegal_discharge)
  прогноз   → модель присутствия тюленя (forecast.py)    → GET /api/v1/forecast
  телеметрия → температура воды и уровень; скачок уровня → WaterEvent (level_anomaly)
  аналитика → статус буя за сутки, соседи события, тюлени (analytics.py)
  находка туши тюленя → обратный дрейф + улики буёв + сейсмика → версии гибели (investigation.py)
  демо      → история за сутки при DEMO_SEED=1 (demo.py); обычно данные шлёт эмулятор буя

Хранилище — SQLite (storage.py), настройки — config.py, буи — buoys.json.
"""
import base64
import binascii
import json
import random
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

import analytics
import demo
import forecast
import investigation
import satellite
import sound
import storage
from config import (BUOYS_FILE, DATA_DIR, DEMO_SEED, FORECAST_MODEL_PATH, LEVEL_NORM_M, LEVEL_THRESHOLD_M, LIBRARY_DIR,
                    METHANE_NORM_NMOL, METHANE_THRESHOLD_NMOL,
                    MODEL_PATH, NEAREST_BUOY_KM, SALINITY_NORM, SALINITY_THRESHOLD, SEAL_WINDOW_H, WATER_COOLDOWN_MIN)

AUDIO_DIR = DATA_DIR / "audio"
IMAGES_DIR = DATA_DIR / "images"
BUOYS: dict[str, dict] = {}  # buoy_id -> Buoy


# ---------- Модели (как в контракте) ----------
class SoundLabel(str, Enum):
    seal = "seal"
    boat = "boat"
    gunshot = "gunshot"
    anomaly = "anomaly"


class WaterEventType(str, Enum):
    illegal_discharge = "illegal_discharge"
    level_anomaly = "level_anomaly"
    gas_seep = "gas_seep"  # сверх контракта v2: метан в воде выше порога (датчик буя)


class Geo(BaseModel):
    lat: float
    lon: float


class Buoy(BaseModel):
    buoy_id: str
    name: str
    geo: Geo


class BuoyList(BaseModel):
    buoys: list[Buoy]


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
    source: str = Field(description="buoy | library | user")
    timestamp: str
    classification: ClassificationResult
    audio_url: str


class EventList(BaseModel):
    total: int
    events: list[SoundEvent]


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


class ClassifyIn(BaseModel):
    buoy_id: Optional[str] = Field(None, examples=["b-001"])
    file_name: Optional[str] = Field(None, examples=["seal_000.wav"])


class ClassifyOut(BaseModel):
    event_id: str
    buoy_id: Optional[str] = None
    classification: ClassificationResult
    audio_url: str


class SalinityIn(BaseModel):
    buoy_id: str = Field(examples=["b-003"])
    salinity: float = Field(ge=0, le=400, examples=[12.4])
    timestamp: Optional[str] = Field(None, examples=["2026-11-12T12:00:00Z"])


class SalinityMeasurement(BaseModel):
    measurement_id: str
    buoy_id: str
    timestamp: str
    salinity: float


class SalinityList(BaseModel):
    measurements: list[SalinityMeasurement]


class WaterDetails(BaseModel):
    salinity: Optional[float] = None
    salinity_delta: Optional[float] = None
    level_delta_m: Optional[float] = None
    confidence: Optional[float] = None
    methane_nmol: Optional[float] = None


class WaterSource(BaseModel):
    buoy_id: Optional[str] = None
    image_id: Optional[str] = None


class WaterEvent(BaseModel):
    event_id: str
    type: WaterEventType
    geo: Geo
    timestamp: str
    details: WaterDetails
    source: WaterSource


class WaterEventList(BaseModel):
    total: int
    events: list[WaterEvent]


class SalinityCreated(BaseModel):
    measurement_id: str
    anomaly_detected: bool
    event: Optional[WaterEvent] = None


class SatelliteCreated(BaseModel):
    image_id: str
    anomaly_detected: bool
    event: Optional[WaterEvent] = None
    image_url: str


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class ForecastDay(BaseModel):
    date: str = Field(description="YYYY-MM-DD")
    seal_probability: float = Field(description="вероятность присутствия тюленя, 0..1")
    risk_score: float = Field(description="приоритет патрулирования, 0..1")
    risk_level: RiskLevel
    air_temp_c: float
    wind_ms: float


class BuoyForecast(BaseModel):
    buoy_id: str
    seal_events_7d: int = Field(description="сколько раз буй слышал тюленя за 7 дней")
    threat_events_7d: int = Field(description="лодки + выстрелы у буя за 7 дней")
    days: list[ForecastDay]


class ForecastOut(BaseModel):
    generated_at: str
    weather_source: str = Field(description="open-meteo | climatology")
    training_data: str = Field(description="на чём обучена модель: synthetic | real")
    forecasts: list[BuoyForecast]


class ErrorOut(BaseModel):
    error: str
    message: str


class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str):
        self.status, self.error, self.message = status, error, message


# ---------- Вспомогательное ----------
def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_ts(value: Optional[str], field: str) -> Optional[str]:
    """Любой ISO-8601 → строка UTC вида 2026-11-12T11:36:00Z (None остаётся None)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace(" ", "+").replace("Z", "+00:00"))
    except ValueError:
        raise ApiError(400, "bad_request", f"'{field}' must be ISO-8601, e.g. 2026-11-12T11:36:00Z")
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def require_buoy(buoy_id: Optional[str]) -> None:
    if buoy_id is not None and buoy_id not in BUOYS:
        raise ApiError(404, "not_found", "Buoy not found")


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(3)}"


def distance_km(a: dict, b: dict) -> float:
    la1, lo1, la2, lo2 = map(radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = sin((la2 - la1) / 2) ** 2 + cos(la1) * cos(la2) * sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * asin(sqrt(h))


def library_files() -> list[Path]:
    return sorted(LIBRARY_DIR.rglob("*.wav")) if LIBRARY_DIR.is_dir() else []


def library_file(file_name: Optional[str]) -> Path:
    """Файл библиотеки по имени или случайный."""
    files = library_files()
    if not files:
        raise ApiError(404, "not_found", f"Sound library is empty: {LIBRARY_DIR}")
    if not file_name:
        return random.choice(files)
    for f in files:
        if f.name == Path(file_name).name:
            return f
    raise ApiError(404, "not_found", "File not found in library, e.g. " + ", ".join(f.name for f in files[:5]))


def process_sound(raw: bytes, event_id: str, buoy_id: Optional[str], source: str, timestamp: str) -> dict:
    """Главный путь звука: декодировать → классифицировать моделью → сохранить wav и событие."""
    try:
        audio, sr = sound.decode(raw)
    except sound.AudioError as e:
        raise ApiError(400, "bad_request", str(e))
    classification = sound.classify(audio, sr)
    sound.save_wav(AUDIO_DIR / f"{event_id}.wav", audio, sr)
    return storage.add_sound_event(event_id, buoy_id, source, timestamp, classification)


def classify_out(ev: dict) -> dict:
    return {k: ev[k] for k in ("event_id", "buoy_id", "classification", "audio_url")}


# ---------- Приложение ----------
@asynccontextmanager
async def lifespan(_: FastAPI):
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    storage.init(DATA_DIR / "eco.db")
    BUOYS.update({b["buoy_id"]: b for b in json.loads(BUOYS_FILE.read_text(encoding="utf-8"))})
    sound.load_model()
    forecast.load_model()
    if DEMO_SEED:
        n = demo.seed_if_empty(BUOYS, library_file, process_sound, SALINITY_NORM, LEVEL_NORM_M)
        if n:
            print(f"Демо-история: {n} звуковых событий за сутки")
    print(f"Бэкенд готов: буёв {len(BUOYS)}, звуков в библиотеке {len(library_files())}, данные в {DATA_DIR}")
    yield


app = FastAPI(title="Эко-Щит Каспия — основной бэкенд", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# Ошибки в формате контракта: { "error": ..., "message": ... }
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


# ---------- Буи ----------
@app.get("/api/v1/buoys", response_model=BuoyList, tags=["buoys"], summary="Список буёв на карту")
def list_buoys():
    return {"buoys": list(BUOYS.values())}


@app.get("/api/v1/buoys/{buoy_id}", response_model=Buoy, responses=NOT_FOUND, tags=["buoys"], summary="Один буй")
def get_buoy(buoy_id: str):
    require_buoy(buoy_id)
    return BUOYS[buoy_id]


# ---------- Звуковые события ----------
@app.post("/api/v1/events", response_model=EventCreated, status_code=201, responses=NOT_FOUND,
          tags=["events"], summary="Звук от буя → классификация моделью")
def create_event(body: EventIn):
    require_buoy(body.buoy_id)
    ts = norm_ts(body.timestamp, "timestamp") or now_iso()
    if body.audio is None:  # без audio — случайный файл из библиотеки (для Swagger-демо)
        raw = library_file(None).read_bytes()
    else:
        try:
            raw = base64.b64decode(body.audio.data_base64, validate=True)
        except (binascii.Error, ValueError):
            raise ApiError(400, "bad_request", "audio.data_base64 is not valid base64")
    ev = process_sound(raw, new_id("evt"), body.buoy_id, "buoy", ts)
    return {"event_id": ev["event_id"], "status": "classified", "event": ev}


@app.get("/api/v1/events", response_model=EventList, tags=["events"], summary="История звуковых событий")
def list_events(
    buoy_id: Optional[str] = None,
    label: Optional[SoundLabel] = None,
    from_: Optional[str] = Query(None, alias="from", description="ISO-8601"),
    to: Optional[str] = Query(None, description="ISO-8601"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    total, events = storage.list_sound_events(buoy_id, label.value if label else None,
                                              norm_ts(from_, "from"), norm_ts(to, "to"), limit, offset)
    return {"total": total, "events": events}


@app.get("/api/v1/events/{event_id}", response_model=SoundEvent, responses=NOT_FOUND,
         tags=["events"], summary="Детали звукового события")
def get_event(event_id: str):
    ev = storage.get_sound_event(event_id)
    if not ev:
        raise ApiError(404, "not_found", "Event not found")
    return ev


# ---------- Классификация ----------
@app.post("/api/v1/classify", response_model=ClassifyOut, responses=NOT_FOUND,
          tags=["classify"], summary="Классификация файла из библиотеки")
def classify_library(body: ClassifyIn = ClassifyIn()):
    require_buoy(body.buoy_id)
    raw = library_file(body.file_name).read_bytes()
    return classify_out(process_sound(raw, new_id("evt-cls"), body.buoy_id, "library", now_iso()))


@app.post("/api/v1/classify/upload", response_model=ClassifyOut, responses=NOT_FOUND,
          tags=["classify"], summary="Загрузка звука с телефона")
def classify_upload(file: UploadFile = File(...), buoy_id: Optional[str] = Form(None)):
    buoy_id = buoy_id or None
    require_buoy(buoy_id)
    raw = file.file.read()
    if not raw:
        raise ApiError(400, "bad_request", "file is empty")
    return classify_out(process_sound(raw, new_id("evt-user"), buoy_id, "user", now_iso()))


# ---------- Солёность ----------
@app.post("/api/v1/salinity", response_model=SalinityCreated, status_code=201, responses=NOT_FOUND,
          tags=["salinity"], summary="Солёность от буя → детект сброса")
def post_salinity(body: SalinityIn):
    require_buoy(body.buoy_id)
    ts = norm_ts(body.timestamp, "timestamp") or now_iso()
    measurement_id = new_id("sal")
    storage.add_measurement(measurement_id, body.buoy_id, ts, body.salinity)

    event = None
    delta = round(body.salinity - SALINITY_NORM, 2)
    if abs(delta) >= SALINITY_THRESHOLD:
        # тот же сброс от этого буя уже зафиксирован недавно — не дублируем
        event = storage.recent_buoy_discharge(body.buoy_id, ts, WATER_COOLDOWN_MIN)
        if event is None:
            confidence = round(min(0.99, 0.55 + 0.15 * abs(delta) / SALINITY_THRESHOLD), 2)
            event = storage.add_water_event(
                new_id("wtr"), "illegal_discharge", BUOYS[body.buoy_id]["geo"], ts,
                {"salinity": body.salinity, "salinity_delta": delta, "confidence": confidence},
                buoy_id=body.buoy_id)
    return {"measurement_id": measurement_id, "anomaly_detected": event is not None, "event": event}


@app.get("/api/v1/salinity", response_model=SalinityList, tags=["salinity"], summary="История солёности (график)")
def list_salinity(
    buoy_id: Optional[str] = None,
    from_: Optional[str] = Query(None, alias="from", description="ISO-8601"),
    to: Optional[str] = Query(None, description="ISO-8601"),
    limit: int = Query(100, ge=1, le=5000),
):
    return {"measurements": storage.list_measurements(buoy_id, norm_ts(from_, "from"), norm_ts(to, "to"), limit)}


# ---------- Спутник ----------
@app.post("/api/v1/satellite", response_model=SatelliteCreated, status_code=201,
          tags=["satellite"], summary="Фото со спутника → детект сброса")
def post_satellite(
    file: UploadFile = File(...),
    lat: float = Form(..., alias="geo[lat]", examples=[43.67]),
    lon: float = Form(..., alias="geo[lon]", examples=[47.61]),
    timestamp: Optional[str] = Form(None),
):
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ApiError(400, "bad_request", "geo[lat] must be in -90..90, geo[lon] in -180..180")
    raw = file.file.read()
    if not raw:
        raise ApiError(400, "bad_request", "file is empty")
    try:
        jpeg, detected, confidence, _ = satellite.analyze(raw)
    except satellite.ImageError as e:
        raise ApiError(400, "bad_request", str(e))

    ts = norm_ts(timestamp, "timestamp") or now_iso()
    geo = {"lat": lat, "lon": lon}
    image_id = new_id("img")
    (IMAGES_DIR / f"{image_id}.jpg").write_bytes(jpeg)
    storage.add_image(image_id, ts, lat, lon)

    event = None
    if detected:
        details = {"confidence": confidence}
        # если рядом есть буй — добавляем его солёность как подтверждение
        near = min(BUOYS.values(), key=lambda b: distance_km(b["geo"], geo))
        buoy_id = near["buoy_id"] if distance_km(near["geo"], geo) <= NEAREST_BUOY_KM else None
        if buoy_id:
            salinity = storage.nearest_salinity(buoy_id, ts)
            if salinity is not None:
                details.update(salinity=salinity, salinity_delta=round(salinity - SALINITY_NORM, 2))
        event = storage.add_water_event(new_id("wtr"), "illegal_discharge", geo, ts, details,
                                        buoy_id=buoy_id, image_id=image_id)
    return {"image_id": image_id, "anomaly_detected": detected, "event": event,
            "image_url": f"/images/{image_id}.jpg"}


# ---------- События воды ----------
@app.get("/api/v1/water-events", response_model=WaterEventList, tags=["water-events"],
         summary="Координаты сбросов/аномалий")
def list_water_events(
    type_: Optional[WaterEventType] = Query(None, alias="type"),
    from_: Optional[str] = Query(None, alias="from", description="ISO-8601"),
    to: Optional[str] = Query(None, description="ISO-8601"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    total, events = storage.list_water_events(type_.value if type_ else None,
                                              norm_ts(from_, "from"), norm_ts(to, "to"), limit, offset)
    return {"total": total, "events": events}


@app.get("/api/v1/water-events/{event_id}", response_model=WaterEvent, responses=NOT_FOUND,
         tags=["water-events"], summary="Детали события воды")
def get_water_event(event_id: str):
    ev = storage.get_water_event(event_id)
    if not ev:
        raise ApiError(404, "not_found", "Water event not found")
    return ev


# ---------- Прогноз ----------
@app.get("/api/v1/forecast", response_model=ForecastOut, responses={404: {"model": ErrorOut}, 503: {"model": ErrorOut}},
         tags=["forecast"], summary="Прогноз присутствия тюленя по буям на ближайшие дни")
def get_forecast(buoy_id: Optional[str] = None, days: int = Query(7, ge=1, le=7)):
    require_buoy(buoy_id)
    if not forecast.is_ready():
        raise ApiError(503, "unavailable", "Forecast model is not loaded")
    buoys = [BUOYS[buoy_id]] if buoy_id else list(BUOYS.values())
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return forecast.build(buoys, storage.count_sound_labels_since(week_ago), days)


# ---------- Телеметрия буя (сверх контракта v2) ----------
class TelemetryIn(BaseModel):
    buoy_id: str = Field(examples=["b-003"])
    water_temp_c: Optional[float] = Field(None, ge=-5, le=40, examples=[11.4])
    level_m: Optional[float] = Field(None, ge=-40, le=0, examples=[-28.9], description="уровень, м (Балтийская система)")
    methane_nmol: Optional[float] = Field(None, ge=0, le=100000, examples=[12.0], description="растворённый метан, нмоль/л")
    timestamp: Optional[str] = Field(None, examples=["2026-11-12T12:00:00Z"])


@app.post("/api/v1/telemetry", status_code=201, responses=NOT_FOUND, tags=["telemetry"],
          summary="Температура воды и уровень от буя → детект аномалии уровня")
def post_telemetry(body: TelemetryIn):
    require_buoy(body.buoy_id)
    ts = norm_ts(body.timestamp, "timestamp") or now_iso()
    record = storage.add_telemetry(new_id("tel"), body.buoy_id, ts, body.water_temp_c, body.level_m, body.methane_nmol)
    event = None
    if body.methane_nmol is not None and body.methane_nmol >= METHANE_THRESHOLD_NMOL:
        confidence = round(min(0.99, 0.6 + 0.1 * body.methane_nmol / METHANE_THRESHOLD_NMOL), 2)
        event = storage.add_water_event(new_id("wtr"), "gas_seep", BUOYS[body.buoy_id]["geo"], ts,
                                        {"methane_nmol": body.methane_nmol, "confidence": confidence}, buoy_id=body.buoy_id)
    if body.level_m is not None and abs(body.level_m - LEVEL_NORM_M) >= LEVEL_THRESHOLD_M:
        delta = round(body.level_m - LEVEL_NORM_M, 2)
        confidence = round(min(0.99, 0.55 + 0.15 * abs(delta) / LEVEL_THRESHOLD_M), 2)
        event = storage.add_water_event(new_id("wtr"), "level_anomaly", BUOYS[body.buoy_id]["geo"], ts,
                                        {"level_delta_m": delta, "confidence": confidence}, buoy_id=body.buoy_id)
    return {**record, "anomaly_detected": event is not None, "event": event}


@app.get("/api/v1/telemetry", responses=NOT_FOUND, tags=["telemetry"], summary="История телеметрии буя")
def list_telemetry(buoy_id: Optional[str] = None,
                   from_: Optional[str] = Query(None, alias="from", description="ISO-8601"),
                   limit: int = Query(100, ge=1, le=5000)):
    require_buoy(buoy_id)
    return {"telemetry": storage.list_telemetry(buoy_id, norm_ts(from_, "from"), limit)}


# ---------- Аналитика (сверх контракта v2) ----------
@app.get("/api/v1/buoy-status", tags=["analytics"],
         summary="Статус буёв за сутки (critical / anomaly / normal), радиус, солёность, температура, уровень")
def get_buoy_status():
    return {"buoys": analytics.buoy_status(BUOYS, distance_km)}


@app.get("/api/v1/events/{event_id}/neighbors", responses=NOT_FOUND, tags=["analytics"],
         summary="Какие соседние буи слышали то же самое (проверка ложных срабатываний)")
def get_event_neighbors(event_id: str):
    ev = storage.get_sound_event(event_id)
    if not ev:
        raise ApiError(404, "not_found", "Event not found")
    return analytics.sound_neighbors(ev, BUOYS, distance_km)


@app.get("/api/v1/water-events/{event_id}/neighbors", responses=NOT_FOUND, tags=["analytics"],
         summary="У каких соседних буёв тоже отклонилась солёность")
def get_water_event_neighbors(event_id: str):
    ev = storage.get_water_event(event_id)
    if not ev:
        raise ApiError(404, "not_found", "Water event not found")
    return analytics.water_neighbors(ev, BUOYS, distance_km)


@app.get("/api/v1/events/{event_id}/context", responses=NOT_FOUND, tags=["analytics"],
         summary="Что происходило в ± minutes вокруг звукового события (все буи, звук и вода)")
def get_event_context(event_id: str, minutes: float = Query(5, ge=1, le=120)):
    ev = storage.get_sound_event(event_id)
    if not ev:
        raise ApiError(404, "not_found", "Event not found")
    return analytics.context("sound", ev, BUOYS, distance_km, minutes)


@app.get("/api/v1/water-events/{event_id}/context", responses=NOT_FOUND, tags=["analytics"],
         summary="Что происходило в ± minutes вокруг события воды")
def get_water_event_context(event_id: str, minutes: float = Query(5, ge=1, le=120)):
    ev = storage.get_water_event(event_id)
    if not ev:
        raise ApiError(404, "not_found", "Water event not found")
    return analytics.context("water", ev, BUOYS, distance_km, minutes)


# ---------- Гибель тюленей: находки и расследование (сверх контракта v2) ----------
class CarcassIn(BaseModel):
    geo: Geo
    count: int = Field(1, ge=1, le=10000, description="сколько туш в этом месте")
    condition: str = Field("moderate", description="fresh — свежая | moderate — средней давности | decomposed — разложившаяся")
    found_at: Optional[str] = Field(None, examples=["2026-11-12T08:00:00Z"])
    note: Optional[str] = Field(None, max_length=500)


@app.post("/api/v1/carcasses", status_code=201, tags=["seal-mortality"],
          summary="Находка погибших тюленей на берегу (инспектор, волонтёр)")
def post_carcass(body: CarcassIn):
    if body.condition not in investigation.CONDITIONS:
        raise ApiError(400, "bad_request", "condition must be fresh | moderate | decomposed")
    found_at = norm_ts(body.found_at, "found_at") or now_iso()
    return storage.add_carcass(new_id("car"), found_at, body.geo.model_dump(), body.count, body.condition, body.note)


@app.get("/api/v1/carcasses", tags=["seal-mortality"], summary="Находки погибших тюленей")
def list_carcasses(limit: int = Query(100, ge=1, le=1000)):
    items = storage.list_carcasses(limit)
    return {"total": sum(c["count"] for c in items), "carcasses": items}


@app.get("/api/v1/carcasses/{carcass_id}/investigation", responses=NOT_FOUND, tags=["seal-mortality"],
         summary="Где и когда животное могло погибнуть и что в это время фиксировали буи → версии")
def get_investigation(carcass_id: str):
    c = storage.get_carcass(carcass_id)
    if not c:
        raise ApiError(404, "not_found", "Carcass report not found")
    return investigation.investigate(c, BUOYS, distance_km)


@app.get("/api/v1/seals", tags=["analytics"], summary="Тюлени: где слышны, оценка численности, куда дрейфуют")
def get_seals(hours: float = Query(SEAL_WINDOW_H, ge=1, le=168)):
    return analytics.seals(BUOYS, distance_km, hours)


# ---------- Демо и описание моделей (сверх контракта v2) ----------
def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@app.get("/api/v1/model-info", tags=["demo"], summary="Метрики моделей для страницы «О модели»")
def model_info():
    return {
        "sound": _read_json(MODEL_PATH.parent / "metrics.json"),
        "forecast": _read_json(FORECAST_MODEL_PATH.parent / "seal_forecast_metrics.json"),
        "library_size": len(library_files()),
    }


# ---------- Статика ----------
@app.get("/audio/{event_id}.wav", tags=["static"], summary="Проиграть звук события",
         responses={200: {"content": {"audio/wav": {}}}, **NOT_FOUND}, response_class=FileResponse)
def get_audio(event_id: str):
    path = AUDIO_DIR / f"{Path(event_id).name}.wav"
    if not path.is_file():
        raise ApiError(404, "not_found", "Audio not found")
    return FileResponse(path, media_type="audio/wav")


@app.get("/images/{image_id}.jpg", tags=["static"], summary="Спутниковый снимок",
         responses={200: {"content": {"image/jpeg": {}}}, **NOT_FOUND}, response_class=FileResponse)
def get_image(image_id: str):
    path = IMAGES_DIR / f"{Path(image_id).name}.jpg"
    if not path.is_file():
        raise ApiError(404, "not_found", "Image not found")
    return FileResponse(path, media_type="image/jpeg")
