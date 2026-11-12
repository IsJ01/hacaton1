"""Эмулятор буя с управлением через Swagger.

    ты в Swagger (:8000)  ──►  эмулятор берёт файл из sounds/  ──►  POST на бэкенд (:8001), как настоящий буй

  POST /send/sound     — выбрать буй и звуковой файл (по имени или номеру) → POST {BACKEND}/api/v1/events
  POST /send/salinity  — буй + уровень солёности → POST {BACKEND}/api/v1/salinity
  POST /send/satellite — снимок из sat_library/ + координаты → POST {BACKEND}/api/v1/satellite
  POST /send/telemetry — температура воды и уровень → POST {BACKEND}/api/v1/telemetry
  /scenario/history    — заготовленные сутки работы пяти буёв (scenarios.py) → на бэкенд
  /scenario/poacher    — живой сценарий «браконьер» по шагам; ход — GET /scenario/status
                          (оба работают и как GET: достаточно открыть адрес в браузере)
  При запуске: если на бэкенде пусто — эмулятор сам отправляет историю за сутки (AUTO_HISTORY=0 — не надо).
  POST /check          — проверка, что бэкенд принимает и сохраняет данные буя
  GET  /sounds, /images — что лежит в папках (с порядковыми номерами)
  GET  /buoys          — буи с бэкенда
  GET  /health, /log   — доступен ли бэкенд, журнал отправок

Звуки: положи свои файлы в папку sounds/ (wav, mp3, ogg, flac, m4a) — они сразу появятся в GET /sounds.
Адрес бэкенда: переменная окружения BACKEND_URL (по умолчанию http://localhost:8001).
"""
import asyncio
import base64
import os
import random
from collections import Counter, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

import scenarios

BASE = Path(__file__).parent
SOUNDS = BASE / "sounds"
IMAGES = BASE / "sat_library"
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8001").rstrip("/")
AUDIO_EXT = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac", ".webm"}
IMAGE_EXT = {".jpg", ".jpeg", ".png"}
LOG: deque = deque(maxlen=200)
AUTO_HISTORY = os.getenv("AUTO_HISTORY", "1") == "1"  # пустой бэкенд при старте → отправить историю за сутки


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


async def post(kind: str, path: str, sent: dict, quiet: bool = False, **kwargs) -> tuple[Optional[int], object]:
    """POST на бэкенд и запись в журнал. Возвращает (код ответа или None, тело)."""
    url = BACKEND_URL + path
    entry = {"time": now_iso(), "kind": kind, "to": url, "sent": sent}
    LOG.appendleft(entry)
    try:
        r = await app.state.http.post(url, **kwargs)
    except httpx.HTTPError as e:
        entry.update(backend_status=None, backend_response=f"недоступен: {type(e).__name__}")
        print(f"[{kind}] {sent} → бэкенд {BACKEND_URL} недоступен ({type(e).__name__})")
        return None, f"недоступен: {type(e).__name__}"
    try:
        body = r.json()
    except ValueError:
        body = r.text[:500]
    entry.update(backend_status=r.status_code, backend_response=body)
    if not quiet:
        print(f"[{kind}] {sent} → {r.status_code}")
    return r.status_code, body


async def send(kind: str, path: str, sent: dict, **kwargs) -> JSONResponse:
    """POST на бэкенд; в ответе — что отправили и что ответил бэкенд."""
    url = BACKEND_URL + path
    status, body = await post(kind, path, sent, **kwargs)
    if status is None:
        return error(502, "backend_unavailable", f"Cannot reach backend at {BACKEND_URL}", to=url, sent=sent)
    result = {"to": url, "sent": sent, "backend_status": status, "backend_response": body}
    if status >= 400:
        return JSONResponse(status_code=502, content={
            "error": "backend_error", "message": f"Backend answered {status}", **result})
    return JSONResponse(status_code=200, content=result)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=30)
    print(f"Эмулятор буя: звуков в sounds/ — {len(list_files(SOUNDS, AUDIO_EXT))}, отправка на {BACKEND_URL}")
    if AUTO_HISTORY:
        asyncio.create_task(auto_history())
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


# ---------- Телеметрия ----------
@app.post("/send/telemetry", tags=["телеметрия"], summary="Буй отправляет температуру воды и уровень → POST /api/v1/telemetry")
async def send_telemetry(
    buoy_id: str = Query(_BUOYS[0], description="От какого буя", json_schema_extra=dropdown(_BUOYS)),
    water_temp_c: Optional[float] = Query(11.4, description="Температура воды, °C"),
    level_m: Optional[float] = Query(-28.9, description="Уровень, м (Балтийская система); отклонение ≥0.3 м → аномалия"),
    timestamp: Optional[str] = Query(None, description="ISO-8601; пусто = сейчас"),
):
    payload = {"buoy_id": buoy_id, "water_temp_c": water_temp_c, "level_m": level_m, "timestamp": timestamp or now_iso()}
    return await send("telemetry", "/api/v1/telemetry", payload, json=payload)


# ---------- Сценарии: заготовленные данные буёв (scenarios.py) ----------
def build(step: dict, ts: str) -> tuple[str, str, dict, dict]:
    """Шаг сценария → (тип, путь на бэкенде, что записать в журнал, аргументы запроса) — те же, что у /send/*."""
    kind = step["kind"]
    if kind == "sound":
        f = SOUNDS / step["file"]
        raw = f.read_bytes()
        payload = {"buoy_id": step["buoy_id"], "timestamp": ts,
                   "audio": {"format": "wav", "data_base64": base64.b64encode(raw).decode()}}
        return kind, "/api/v1/events", {"buoy_id": step["buoy_id"], "timestamp": ts, "file_name": f.name}, {"json": payload}
    if kind == "salinity":
        payload = {"buoy_id": step["buoy_id"], "salinity": step["salinity"], "timestamp": ts}
        return kind, "/api/v1/salinity", payload, {"json": payload}
    if kind == "telemetry":
        payload = {"buoy_id": step["buoy_id"], "water_temp_c": step["water_temp_c"], "level_m": step["level_m"], "timestamp": ts}
        return kind, "/api/v1/telemetry", payload, {"json": payload}
    f = IMAGES / step["file"]
    sent = {"file_name": f.name, "geo": {"lat": step["lat"], "lon": step["lon"]}, "timestamp": ts}
    return kind, "/api/v1/satellite", sent, {
        "data": {"geo[lat]": str(step["lat"]), "geo[lon]": str(step["lon"]), "timestamp": ts},
        "files": {"file": (f.name, f.read_bytes(), "image/jpeg")}}


def expected_label(file_name: str) -> str:
    prefix = file_name.split("_")[0]
    return "anomaly" if prefix == "bg" else prefix  # фон бэкенд отдаёт как anomaly


def summarize(results: list[tuple[dict, Optional[int], object]]) -> dict:
    """Что принял бэкенд и что он из этого понял."""
    by_kind: dict[str, Counter] = {}
    labels, mismatches, created, errors = Counter(), [], Counter(), []
    for step, status, body in results:
        c = by_kind.setdefault(step["kind"], Counter())
        c["sent"] += 1
        if status is not None and status < 400:
            c["accepted"] += 1
        else:
            c["failed"] += 1
            if len(errors) < 5:
                errors.append({"step": {k: v for k, v in step.items() if k != "note"}, "status": status, "response": body})
            continue
        if step["kind"] == "sound" and isinstance(body, dict):
            got = body.get("event", {}).get("classification", {}).get("label")
            labels[got] += 1
            if got != expected_label(step["file"]):
                mismatches.append({"file": step["file"], "expected": expected_label(step["file"]), "model": got})
        elif isinstance(body, dict) and body.get("event"):
            created[body["event"]["type"]] += 1
    return {"by_kind": {k: dict(v) for k, v in by_kind.items()}, "sound_labels_from_model": dict(labels),
            "model_mismatches": mismatches, "water_events_created": dict(created), "errors": errors}


async def backend_event_count() -> Optional[int]:
    """Сколько звуковых событий уже есть на бэкенде (None — бэкенд не отвечает)."""
    try:
        r = await app.state.http.get(BACKEND_URL + "/api/v1/events?limit=1", timeout=5)
        return r.json().get("total") if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


async def auto_history():
    """После старта ждём бэкенд (до 3 минут) и, если он пустой, отправляем историю за сутки."""
    for _ in range(90):
        total = await backend_event_count()
        if total is not None:
            if total == 0:
                print("[auto] бэкенд пустой — отправляю историю за сутки")
                await send_history(24)
            else:
                print(f"[auto] на бэкенде уже {total} событий — историю не отправляю")
            return
        await asyncio.sleep(2)
    print(f"[auto] бэкенд {BACKEND_URL} так и не ответил — историю не отправил")


@app.api_route("/scenario/history", methods=["GET", "POST"], tags=["сценарии"],
               summary="Заготовленные сутки работы буёв → на бэкенд (звук, солёность, телеметрия, снимки)")
async def scenario_history(hours: int = Query(24, ge=1, le=24, description="За сколько последних часов"),
                           force: bool = Query(False, description="Отправить, даже если на бэкенде уже есть данные")):
    """Отправляет на бэкенд сутки данных с прошлыми метками времени — обычными запросами по контракту.
    Если данные на бэкенде уже есть, ничего не делает (иначе история задвоится) — разве что `force=true`.
    Начать с чистого листа — `bash docker.sh reset` в backend/."""
    total = await backend_event_count()
    if total is None:
        return error(502, "backend_unavailable", f"Cannot reach backend at {BACKEND_URL}")
    if total and not force:
        return {"backend_url": BACKEND_URL, "sent": False, "events_on_backend": total,
                "message": "На бэкенде уже есть данные — история не отправлена, чтобы не задвоить. "
                           "Добавить всё равно: ?force=true. С чистого листа: bash docker.sh reset в backend/."}
    return await send_history(hours)


async def send_history(hours: int) -> JSONResponse:
    steps = scenarios.history_steps(hours)
    now = datetime.now(timezone.utc)
    sem = asyncio.Semaphore(8)

    async def one(step):
        ts = (now - timedelta(minutes=step["minutes_ago"])).strftime("%Y-%m-%dT%H:%M:%SZ")
        kind, path, sent, kwargs = build(step, ts)
        async with sem:
            status, body = await post(kind, path, sent, quiet=True, **kwargs)
        return step, status, body

    results = await asyncio.gather(*(one(s) for s in steps))
    report = summarize(results)
    ok = all(v.get("failed", 0) == 0 for v in report["by_kind"].values())
    print(f"[history] отправлено {len(steps)} → " + ", ".join(f"{k}: {v}" for k, v in report["by_kind"].items()))
    return JSONResponse(status_code=200 if ok else 502, content={"backend_url": BACKEND_URL, "hours": hours, "sent": True,
                                                                 "all_accepted": ok, **report})


SCENARIO = {"running": False, "started_at": None, "steps": []}


@app.api_route("/scenario/poacher", methods=["GET", "POST"], tags=["сценарии"],
               summary="Живой сценарий «браконьер»: шаги с паузами, ~30 с")
async def scenario_poacher():
    duration = sum(s["pause"] for s in scenarios.POACHER)
    if SCENARIO["running"]:
        return {"started": False, "duration_sec": duration, "message": "Сценарий уже идёт", **SCENARIO}
    SCENARIO.update(running=True, started_at=now_iso(), steps=[])

    async def run():
        results = []
        try:
            for step in scenarios.POACHER:
                await asyncio.sleep(step["pause"])
                kind, path, sent, kwargs = build(step, now_iso())
                status, body = await post(kind, path, sent, **kwargs)
                results.append((step, status, body))
                SCENARIO["steps"].append({"time": now_iso(), "note": step["note"], "backend_status": status})
            SCENARIO["report"] = summarize(results)
        finally:
            SCENARIO["running"] = False

    asyncio.create_task(run())
    return {"started": True, "duration_sec": duration, "message": "Сценарий запущен",
            "plan": [s["note"] for s in scenarios.POACHER]}


@app.get("/scenario/status", tags=["сценарии"], summary="Ход живого сценария")
def scenario_status():
    return SCENARIO


# ---------- Проверка бэкенда ----------
@app.post("/check", tags=["служебное"], summary="Проверить, что бэкенд принимает и сохраняет данные буя")
async def check(buoy_id: str = Query(_BUOYS[1] if len(_BUOYS) > 1 else _BUOYS[0], json_schema_extra=dropdown(_BUOYS))):
    """Отправляет по одному звуку, солёности и телеметрии, затем читает их обратно через GET бэкенда."""
    http, checks = app.state.http, []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    async def get(path):
        try:
            r = await http.get(BACKEND_URL + path, timeout=10)
            return r.status_code, r.json()
        except (httpx.HTTPError, ValueError) as e:
            return None, str(e)

    status, body = await get("/api/v1/buoys")
    add("бэкенд отвечает, знает буй", status == 200 and any(b["buoy_id"] == buoy_id for b in body.get("buoys", [])),
        f"GET /api/v1/buoys → {status}")
    if status != 200:
        return JSONResponse(status_code=502, content={"backend_url": BACKEND_URL, "ok": False, "checks": checks})

    ts = now_iso()
    kind, path, sent, kwargs = build({"kind": "sound", "buoy_id": buoy_id, "file": "seal_001.wav"}, ts)
    status, body = await post(kind, path, sent, **kwargs)
    event_id = body.get("event_id") if isinstance(body, dict) else None
    label = body.get("event", {}).get("classification", {}).get("label") if isinstance(body, dict) else None
    add("звук принят и классифицирован", status == 201 and event_id, f"POST /api/v1/events → {status}, модель: {label}")
    if event_id:
        s2, ev = await get(f"/api/v1/events/{event_id}")
        add("звук сохранён", s2 == 200 and ev.get("buoy_id") == buoy_id, f"GET /api/v1/events/{event_id} → {s2}")
        s3, _ = await get(f"/api/v1/events/{event_id}/neighbors")
        add("анализ соседей работает", s3 == 200, f"GET /api/v1/events/{event_id}/neighbors → {s3}")
        try:
            a = await http.get(BACKEND_URL + f"/audio/{event_id}.wav", timeout=10)
            add("запись доступна для прослушивания", a.status_code == 200 and a.content[:4] == b"RIFF", f"GET /audio/{event_id}.wav → {a.status_code}")
        except httpx.HTTPError as e:
            add("запись доступна для прослушивания", False, str(e))

    kind, path, sent, kwargs = build({"kind": "salinity", "buoy_id": buoy_id, "salinity": 12.5}, ts)
    status, body = await post(kind, path, sent, **kwargs)
    mid = body.get("measurement_id") if isinstance(body, dict) else None
    add("солёность принята", status == 201 and mid, f"POST /api/v1/salinity → {status}")
    s2, sal = await get(f"/api/v1/salinity?buoy_id={buoy_id}&limit=5")
    add("солёность сохранена", s2 == 200 and any(m["measurement_id"] == mid for m in sal.get("measurements", [])),
        f"GET /api/v1/salinity → {s2}")

    kind, path, sent, kwargs = build({"kind": "telemetry", "buoy_id": buoy_id, "water_temp_c": 11.4, "level_m": -28.9}, ts)
    status, body = await post(kind, path, sent, **kwargs)
    add("телеметрия принята", status == 201, f"POST /api/v1/telemetry → {status}")

    s2, st = await get("/api/v1/buoy-status")
    me = next((b for b in st.get("buoys", []) if b["buoy_id"] == buoy_id), {}) if s2 == 200 else {}
    add("статус буя обновился", me.get("online") and me.get("water_temp_c") is not None,
        f"GET /api/v1/buoy-status → {s2}, статус: {me.get('status')}, на связи: {me.get('online')}")
    ok = all(c["ok"] for c in checks)
    return JSONResponse(status_code=200 if ok else 502, content={"backend_url": BACKEND_URL, "ok": ok, "checks": checks})


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
