"""Хранилище на SQLite (файл data/eco.db). Данные переживают перезапуск.

Каждая функция возвращает уже готовые словари в формате контракта.
Время везде хранится строкой вида 2026-11-12T11:36:00Z (UTC), поэтому строки можно сравнивать как даты.
"""
import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS sound_events (
    event_id TEXT PRIMARY KEY, buoy_id TEXT, source TEXT, timestamp TEXT,
    label TEXT, confidence REAL, candidates TEXT
);
CREATE TABLE IF NOT EXISTS measurements (
    measurement_id TEXT PRIMARY KEY, buoy_id TEXT, timestamp TEXT, salinity REAL
);
CREATE TABLE IF NOT EXISTS images (
    image_id TEXT PRIMARY KEY, timestamp TEXT, lat REAL, lon REAL
);
CREATE TABLE IF NOT EXISTS water_events (
    event_id TEXT PRIMARY KEY, type TEXT, lat REAL, lon REAL, timestamp TEXT,
    salinity REAL, salinity_delta REAL, level_delta_m REAL, confidence REAL,
    buoy_id TEXT, image_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_sound_ts ON sound_events (timestamp);
CREATE INDEX IF NOT EXISTS idx_meas_buoy_ts ON measurements (buoy_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_water_ts ON water_events (timestamp);
"""

_db: Optional[sqlite3.Connection] = None
_lock = threading.Lock()


def init(path: Path) -> None:
    global _db
    path.parent.mkdir(parents=True, exist_ok=True)
    _db = sqlite3.connect(str(path), check_same_thread=False)
    _db.row_factory = sqlite3.Row
    _db.executescript(SCHEMA)


def _query(sql: str, args=()) -> list[sqlite3.Row]:
    with _lock:
        return _db.execute(sql, args).fetchall()


def _execute(sql: str, args=()) -> None:
    with _lock:
        _db.execute(sql, args)
        _db.commit()


def _where(conditions: list[tuple[str, object]]) -> tuple[str, list]:
    """[("buoy_id = ?", "b-001"), ("timestamp >= ?", None)] → (" WHERE buoy_id = ?", ["b-001"])"""
    used = [(sql, value) for sql, value in conditions if value is not None]
    if not used:
        return "", []
    return " WHERE " + " AND ".join(sql for sql, _ in used), [value for _, value in used]


# ---------- звуковые события ----------
def _sound(row: sqlite3.Row) -> dict:
    return {
        "event_id": row["event_id"],
        "buoy_id": row["buoy_id"],
        "source": row["source"],
        "timestamp": row["timestamp"],
        "classification": {
            "label": row["label"],
            "confidence": row["confidence"],
            "candidates": json.loads(row["candidates"]),
        },
        "audio_url": f"/audio/{row['event_id']}.wav",
    }


def add_sound_event(event_id: str, buoy_id: Optional[str], source: str, timestamp: str, classification: dict) -> dict:
    _execute(
        "INSERT INTO sound_events VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event_id, buoy_id, source, timestamp, classification["label"], classification["confidence"],
         json.dumps(classification["candidates"])),
    )
    return get_sound_event(event_id)


def get_sound_event(event_id: str) -> Optional[dict]:
    rows = _query("SELECT * FROM sound_events WHERE event_id = ?", (event_id,))
    return _sound(rows[0]) if rows else None


def list_sound_events(buoy_id, label, ts_from, ts_to, limit: int, offset: int) -> tuple[int, list[dict]]:
    where, args = _where([("buoy_id = ?", buoy_id), ("label = ?", label),
                          ("timestamp >= ?", ts_from), ("timestamp <= ?", ts_to)])
    total = _query("SELECT COUNT(*) FROM sound_events" + where, args)[0][0]
    rows = _query("SELECT * FROM sound_events" + where + " ORDER BY timestamp DESC, rowid DESC LIMIT ? OFFSET ?",
                  args + [limit, offset])
    return total, [_sound(r) for r in rows]


def count_sound_labels_since(ts_from: str) -> dict[str, dict[str, int]]:
    """Сколько событий каждой метки было у каждого буя начиная с ts_from: {buoy_id: {label: n}}."""
    rows = _query("SELECT buoy_id, label, COUNT(*) AS n FROM sound_events "
                  "WHERE timestamp >= ? AND buoy_id IS NOT NULL GROUP BY buoy_id, label", (ts_from,))
    result: dict[str, dict[str, int]] = {}
    for r in rows:
        result.setdefault(r["buoy_id"], {})[r["label"]] = r["n"]
    return result


# ---------- солёность ----------
def add_measurement(measurement_id: str, buoy_id: str, timestamp: str, salinity: float) -> None:
    _execute("INSERT INTO measurements VALUES (?, ?, ?, ?)", (measurement_id, buoy_id, timestamp, salinity))


def list_measurements(buoy_id, ts_from, ts_to, limit: int) -> list[dict]:
    """Последние limit измерений, по возрастанию времени."""
    where, args = _where([("buoy_id = ?", buoy_id), ("timestamp >= ?", ts_from), ("timestamp <= ?", ts_to)])
    rows = _query("SELECT * FROM measurements" + where + " ORDER BY timestamp DESC, rowid DESC LIMIT ?", args + [limit])
    return [dict(r) for r in reversed(rows)]


def nearest_salinity(buoy_id: str, timestamp: str) -> Optional[float]:
    """Солёность буя, ближайшая по времени к timestamp."""
    rows = _query(
        "SELECT salinity FROM measurements WHERE buoy_id = ? "
        "ORDER BY ABS(strftime('%s', timestamp) - strftime('%s', ?)), rowid DESC LIMIT 1",
        (buoy_id, timestamp),
    )
    return rows[0]["salinity"] if rows else None


# ---------- снимки ----------
def add_image(image_id: str, timestamp: str, lat: float, lon: float) -> None:
    _execute("INSERT INTO images VALUES (?, ?, ?, ?)", (image_id, timestamp, lat, lon))


# ---------- события воды ----------
def _water(row: sqlite3.Row) -> dict:
    return {
        "event_id": row["event_id"],
        "type": row["type"],
        "geo": {"lat": row["lat"], "lon": row["lon"]},
        "timestamp": row["timestamp"],
        "details": {k: row[k] for k in ("salinity", "salinity_delta", "level_delta_m", "confidence")},
        "source": {"buoy_id": row["buoy_id"], "image_id": row["image_id"]},
    }


def add_water_event(event_id: str, type_: str, geo: dict, timestamp: str, details: dict,
                    buoy_id: Optional[str] = None, image_id: Optional[str] = None) -> dict:
    _execute(
        "INSERT INTO water_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (event_id, type_, geo["lat"], geo["lon"], timestamp, details.get("salinity"), details.get("salinity_delta"),
         details.get("level_delta_m"), details.get("confidence"), buoy_id, image_id),
    )
    return get_water_event(event_id)


def get_water_event(event_id: str) -> Optional[dict]:
    rows = _query("SELECT * FROM water_events WHERE event_id = ?", (event_id,))
    return _water(rows[0]) if rows else None


def list_water_events(type_, ts_from, ts_to, limit: int, offset: int) -> tuple[int, list[dict]]:
    where, args = _where([("type = ?", type_), ("timestamp >= ?", ts_from), ("timestamp <= ?", ts_to)])
    total = _query("SELECT COUNT(*) FROM water_events" + where, args)[0][0]
    rows = _query("SELECT * FROM water_events" + where + " ORDER BY timestamp DESC, rowid DESC LIMIT ? OFFSET ?",
                  args + [limit, offset])
    return total, [_water(r) for r in rows]


def recent_buoy_discharge(buoy_id: str, timestamp: str, cooldown_min: float) -> Optional[dict]:
    """Сброс по солёности от этого же буя, зафиксированный не дальше cooldown_min минут от timestamp."""
    rows = _query(
        "SELECT * FROM water_events WHERE type = 'illegal_discharge' AND buoy_id = ? AND image_id IS NULL "
        "AND ABS(strftime('%s', timestamp) - strftime('%s', ?)) <= ? ORDER BY timestamp DESC LIMIT 1",
        (buoy_id, timestamp, cooldown_min * 60),
    )
    return _water(rows[0]) if rows else None
