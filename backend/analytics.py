"""Аналитика поверх событий от буёв: статус буя за сутки, соседи события, тюлени.

Буи только присылают звук, солёность и телеметрию — всё, что ниже, считает бэкенд.

  статус буя  → critical (лодка, выстрел, сброс) | anomaly (аномальный звук, скачок солёности) | normal
  точка звука → слышали ≥ 3 буя: источник по разнице времени прихода (как в системах определения выстрелов)
  соседи      → какие буи рядом слышали то же самое в пределах ± NEIGHBOR_WINDOW_MIN; никто не слышал — повод
                 заподозрить ложное срабатывание
  контекст    → всё, что случилось в ± N минут вокруг события, с расстоянием и сдвигом по времени
  тюлени      → где слышно тюленей, оценка численности, куда смещается группа (центр по буям во времени)
"""
from datetime import datetime, timedelta, timezone
import math
from math import atan2, cos, degrees, radians, sin
from typing import Callable, Optional

import storage
import numpy as np

from config import (ANOMALY_SALINITY_DELTA, BUOY_ONLINE_MIN, CRITICAL_LABELS, LISTEN_RADIUS_KM, NEIGHBOR_WINDOW_MIN,
                    SALINITY_NORM, SEAL_TRACK_BINS, SOUND_SPEED_MS, STATUS_WINDOW_H)

DIRECTIONS = ["С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ"]
LABEL_RU = {"seal": "тюлень", "boat": "лодка", "gunshot": "выстрел", "anomaly": "аномальный звук"}


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def radius(buoy: dict) -> float:
    return float(buoy.get("listen_radius_km") or LISTEN_RADIUS_KM)


def bearing(a: dict, b: dict) -> float:
    """Направление из a в b, градусы от севера по часовой."""
    la1, la2, dlo = radians(a["lat"]), radians(b["lat"]), radians(b["lon"] - a["lon"])
    x = sin(dlo) * cos(la2)
    y = cos(la1) * sin(la2) - sin(la1) * cos(la2) * cos(dlo)
    return (degrees(atan2(x, y)) + 360) % 360


def direction(deg: float) -> str:
    return DIRECTIONS[int((deg + 22.5) // 45) % 8]


# ---------- статус буя за сутки ----------
def buoy_status(buoys: dict, distance_km: Callable) -> list[dict]:
    now = datetime.now(timezone.utc)
    since = _iso(now - timedelta(hours=STATUS_WINDOW_H))
    _, sounds = storage.list_sound_events(None, None, since, None, 5000, 0)
    _, water = storage.list_water_events(None, since, None, 5000, 0)
    result = []
    for b in buoys.values():
        bid = b["buoy_id"]
        own = [e for e in sounds if e["buoy_id"] == bid]
        counts: dict[str, int] = {}
        for e in own:
            label = e["classification"]["label"]
            counts[label] = counts.get(label, 0) + 1
        # события воды этого буя или в его зоне слышимости (например, пятно со спутника)
        own_water = [w for w in water if w["source"]["buoy_id"] == bid
                     or (w["source"]["buoy_id"] is None and distance_km(w["geo"], b["geo"]) <= radius(b))]
        sal = storage.list_measurements(bid, since, None, 5000)
        sal_jumps = [m for m in sal if abs(m["salinity"] - SALINITY_NORM) >= ANOMALY_SALINITY_DELTA]

        critical = [LABEL_RU.get(l, l) + " ×" + str(n) for l, n in counts.items() if l in CRITICAL_LABELS]
        critical += [{"illegal_discharge": "сброс", "gas_seep": "выброс газа"}.get(w["type"], "аномалия уровня") for w in own_water]
        anomaly = (["аномальный звук ×" + str(counts["anomaly"])] if counts.get("anomaly") else [])
        anomaly += (["скачок солёности ×" + str(len(sal_jumps))] if sal_jumps else [])
        status = "critical" if critical else "anomaly" if anomaly else "normal"
        reason = ", ".join(dict.fromkeys(critical or anomaly)) or "за сутки без происшествий"

        temp, level = storage.last_value(bid, "water_temp_c"), storage.last_value(bid, "level_m")
        methane = storage.last_value(bid, "methane_nmol")
        stamps = [own[0]["timestamp"]] if own else []
        stamps += [sal[-1]["timestamp"]] if sal else []
        stamps += [x["timestamp"] for x in (temp, level) if x]
        last_signal = max(stamps) if stamps else None
        result.append({
            "buoy_id": bid,
            "name": b["name"],
            "geo": b["geo"],
            "listen_radius_km": radius(b),
            "status": status,
            "reason": reason,
            "window_h": STATUS_WINDOW_H,
            "counts": counts,
            "water_events": len(own_water),
            "last_label": own[0]["classification"]["label"] if own else None,
            "last_signal": last_signal,
            "online": bool(last_signal) and (now - _ts(last_signal)).total_seconds() <= BUOY_ONLINE_MIN * 60,
            "salinity": sal[-1]["salinity"] if sal else storage.nearest_salinity(bid, _iso(now)),
            "water_temp_c": temp["value"] if temp else None,
            "level_m": level["value"] if level else None,
            "methane_nmol": methane["value"] if methane else None,
            "telemetry_at": max((x["timestamp"] for x in (temp, level) if x), default=None),
        })
    return result


# ---------- соседи события ----------
def _verdict(neighbors: list[dict]) -> tuple[str, str]:
    in_range = [n for n in neighbors if n["in_range"]]
    heard = [n for n in in_range if n["heard"]]
    if heard:
        return "confirmed", f"Подтверждено: слышали ещё {len(heard)} из {len(in_range)} соседних буёв"
    if in_range:
        return "unconfirmed", f"Соседние буи ({len(in_range)}) этого не слышали — возможно ложное срабатывание"
    return "isolated", "Рядом нет буёв, которые могли это услышать — подтвердить нечем"


def sound_neighbors(event: dict, buoys: dict, distance_km: Callable) -> dict:
    origin = buoys.get(event["buoy_id"]) if event.get("buoy_id") else None
    if not origin:
        return {"event_id": event["event_id"], "verdict": "isolated",
                "message": "Событие не привязано к бую (запись с телефона без буя)", "neighbors": []}
    t = _ts(event["timestamp"])
    label = event["classification"]["label"]
    window = timedelta(minutes=NEIGHBOR_WINDOW_MIN)
    _, near = storage.list_sound_events(None, label, _iso(t - window), _iso(t + window), 500, 0)
    neighbors = []
    for b in buoys.values():
        if b["buoy_id"] == origin["buoy_id"]:
            continue
        d = distance_km(origin["geo"], b["geo"])
        matches = [e for e in near if e["buoy_id"] == b["buoy_id"]]
        best = min(matches, key=lambda e: abs((_ts(e["timestamp"]) - t).total_seconds())) if matches else None
        neighbors.append({
            "buoy_id": b["buoy_id"],
            "name": b["name"],
            "distance_km": round(d, 1),
            "in_range": d <= radius(origin) + radius(b),  # зоны слышимости пересекаются
            "heard": best is not None,
            "event_id": best["event_id"] if best else None,
            "confidence": best["classification"]["confidence"] if best else None,
            "delta_sec": round((_ts(best["timestamp"]) - t).total_seconds()) if best else None,
        })
    neighbors.sort(key=lambda n: n["distance_km"])
    verdict, message = _verdict(neighbors)
    heard = [(origin["geo"], t)] + [(buoys[n["buoy_id"]]["geo"], t + timedelta(seconds=n["delta_sec"]))
                                    for n in neighbors if n["heard"]]
    return {"event_id": event["event_id"], "label": label, "window_min": NEIGHBOR_WINDOW_MIN,
            "verdict": verdict, "message": message, "neighbors": neighbors, "location": locate(heard)}


def locate(heard: list[tuple[dict, datetime]]) -> Optional[dict]:
    """Точка источника по разнице времени прихода звука (TDOA) на ≥ 3 буя: перебор сетки, минимум невязки.

    Время событий хранится с точностью до секунды → погрешность порядка скорости звука × 1 с ≈ 1.5 км.
    """
    if len(heard) < 3:
        return None
    lat0 = sum(g["lat"] for g, _ in heard) / len(heard)
    kx, ky = 111.32 * math.cos(math.radians(lat0)), 111.32  # км на градус
    pts = np.array([[g["lon"] * kx, g["lat"] * ky] for g, _ in heard])
    t0 = min(t for _, t in heard)
    times = np.array([(t - t0).total_seconds() for _, t in heard])
    c = SOUND_SPEED_MS / 1000  # км/с
    span = max(np.ptp(pts[:, 0]), np.ptp(pts[:, 1])) / 2 + 30
    cx, cy = pts[:, 0].mean(), pts[:, 1].mean()
    best = None
    for step in (1.0, 0.1):  # грубо, затем точно вокруг лучшей точки
        if best is not None:
            cx, cy, span = best[0], best[1], 3.0
        xs = np.arange(cx - span, cx + span + step, step)
        ys = np.arange(cy - span, cy + span + step, step)
        gx, gy = np.meshgrid(xs, ys)
        d = np.sqrt((gx[..., None] - pts[:, 0]) ** 2 + (gy[..., None] - pts[:, 1]) ** 2)  # км до каждого буя
        emit = times - d / c  # момент выстрела по каждому бую: в верной точке они совпадают
        resid = emit.std(axis=-1)
        i = np.unravel_index(np.argmin(resid), resid.shape)
        best = (gx[i], gy[i], float(resid[i]))
    err_km = round(max(1.5, best[2] * SOUND_SPEED_MS / 1000 * 2), 1)
    return {"geo": {"lat": round(best[1] / ky, 4), "lon": round(best[0] / kx, 4)}, "error_km": err_km,
            "buoys_used": len(heard), "method": f"разница времени прихода звука, скорость звука {SOUND_SPEED_MS:.0f} м/с"}


def water_neighbors(event: dict, buoys: dict, distance_km: Callable) -> dict:
    """Для сброса: у каких буёв рядом солёность тоже отклонилась от нормы в пределах ± часа."""
    t = event["timestamp"]
    src = event["source"]["buoy_id"]
    reach = max(radius(b) for b in buoys.values()) * 2
    neighbors = []
    for b in buoys.values():
        if b["buoy_id"] == src:
            continue
        d = distance_km(event["geo"], b["geo"])
        sal = storage.list_measurements(b["buoy_id"], _iso(_ts(t) - timedelta(hours=1)), _iso(_ts(t) + timedelta(hours=1)), 500)
        worst = max(sal, key=lambda m: abs(m["salinity"] - SALINITY_NORM)) if sal else None
        delta = round(worst["salinity"] - SALINITY_NORM, 2) if worst else None
        neighbors.append({
            "buoy_id": b["buoy_id"], "name": b["name"], "distance_km": round(d, 1),
            "in_range": d <= reach,
            "heard": delta is not None and abs(delta) >= ANOMALY_SALINITY_DELTA,
            "salinity": worst["salinity"] if worst else None, "salinity_delta": delta,
        })
    neighbors.sort(key=lambda n: n["distance_km"])
    verdict, message = _verdict(neighbors)
    return {"event_id": event["event_id"], "label": event["type"], "window_min": 60,
            "verdict": verdict, "message": message.replace("слышали", "фиксировали"),
            "neighbors": neighbors}


# ---------- контекст события: что происходило рядом по времени ----------
def context(kind: str, event: dict, buoys: dict, distance_km: Callable, minutes: float) -> dict:
    """Все звуковые и водные события в ± minutes вокруг события — общая картина для инспектора."""
    if kind == "sound":
        origin_buoy = event.get("buoy_id")
        origin_geo = buoys[origin_buoy]["geo"] if origin_buoy in buoys else None
    else:
        origin_buoy, origin_geo = event["source"]["buoy_id"], event["geo"]
    t = _ts(event["timestamp"])
    lo, hi = _iso(t - timedelta(minutes=minutes)), _iso(t + timedelta(minutes=minutes))
    _, sounds = storage.list_sound_events(None, None, lo, hi, 500, 0)
    _, water = storage.list_water_events(None, lo, hi, 500, 0)

    items = []
    for e in sounds:
        geo = buoys[e["buoy_id"]]["geo"] if e["buoy_id"] in buoys else None
        items.append({"kind": "sound", "event_id": e["event_id"], "timestamp": e["timestamp"],
                      "label": e["classification"]["label"], "confidence": e["classification"]["confidence"],
                      "buoy_id": e["buoy_id"], "geo": geo})
    for w in water:
        items.append({"kind": "water", "event_id": w["event_id"], "timestamp": w["timestamp"], "label": w["type"],
                      "confidence": w["details"]["confidence"], "buoy_id": w["source"]["buoy_id"], "geo": w["geo"],
                      "source": "спутник" if w["source"]["image_id"] else "датчик"})
    for it in items:
        it["delta_sec"] = round((_ts(it["timestamp"]) - t).total_seconds())
        it["distance_km"] = round(distance_km(origin_geo, it["geo"]), 1) if origin_geo and it["geo"] else None
        it["same_buoy"] = bool(origin_buoy) and it["buoy_id"] == origin_buoy
        it["is_origin"] = it["event_id"] == event["event_id"]
    items.sort(key=lambda i: i["delta_sec"])

    others = [i for i in items if not i["is_origin"]]
    counts: dict[str, int] = {}
    for i in others:
        counts[i["label"]] = counts.get(i["label"], 0) + 1
    near_buoys = sorted({i["buoy_id"] for i in others if i["buoy_id"]})
    return {"event_id": event["event_id"], "kind": kind, "timestamp": event["timestamp"], "window_min": minutes,
            "origin_buoy": origin_buoy, "total": len(others), "counts": counts, "buoys": near_buoys, "items": items}


# ---------- тюлени ----------
def _centroid(events: list[dict], buoys: dict) -> dict:
    """Центр вокализаций: координаты буёв, взвешенные уверенностью модели."""
    w = sum(e["classification"]["confidence"] for e in events) or 1
    lat = sum(buoys[e["buoy_id"]]["geo"]["lat"] * e["classification"]["confidence"] for e in events) / w
    lon = sum(buoys[e["buoy_id"]]["geo"]["lon"] * e["classification"]["confidence"] for e in events) / w
    return {"lat": round(lat, 4), "lon": round(lon, 4)}


def _components(ids: list[str], buoys: dict, distance_km: Callable) -> list[list[str]]:
    """Буи с детекциями, у которых пересекаются зоны слышимости, — одна группа тюленей."""
    left, groups = set(ids), []
    while left:
        stack, group = [left.pop()], []
        while stack:
            cur = stack.pop()
            group.append(cur)
            for other in list(left):
                if distance_km(buoys[cur]["geo"], buoys[other]["geo"]) <= radius(buoys[cur]) + radius(buoys[other]):
                    left.remove(other)
                    stack.append(other)
        groups.append(sorted(group))
    return groups


def seals(buoys: dict, distance_km: Callable, hours: float) -> dict:
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=hours)
    _, events = storage.list_sound_events(None, "seal", _iso(start), None, 5000, 0)
    events = [e for e in reversed(events) if e["buoy_id"] in buoys]  # от старых к новым

    per_buoy = {}
    for e in events:
        p = per_buoy.setdefault(e["buoy_id"], {"buoy_id": e["buoy_id"], "detections": 0, "last_seen": None})
        p["detections"] += 1
        p["last_seen"] = e["timestamp"]

    bin_len = timedelta(hours=hours) / SEAL_TRACK_BINS
    groups = []
    for n, ids in enumerate(_components(list(per_buoy), buoys, distance_km), 1):
        evs = [e for e in events if e["buoy_id"] in ids]
        # трек: центр вокализаций на каждом отрезке окна
        track = []
        for i in range(SEAL_TRACK_BINS):
            lo, hi = start + bin_len * i, start + bin_len * (i + 1)
            part = [e for e in evs if lo <= _ts(e["timestamp"]) < hi]
            if part:
                track.append({**_centroid(part, buoys), "t": _iso(lo + bin_len / 2), "detections": len(part)})
        drift = None
        if len(track) >= 2:
            a, b = track[0], track[-1]
            dist = distance_km(a, b)
            dt_h = (_ts(b["t"]) - _ts(a["t"])).total_seconds() / 3600
            if dist >= 1:
                deg = bearing(a, b)
                drift = {"bearing_deg": round(deg), "direction": direction(deg), "distance_km": round(dist, 1),
                         "speed_kmh": round(dist / dt_h, 2) if dt_h else None}
        # оценка численности: пик вокализаций за час (одна особь редко подаёт голос чаще)
        hourly: dict[str, int] = {}
        for e in evs:
            hourly[e["timestamp"][:13]] = hourly.get(e["timestamp"][:13], 0) + 1
        center = track[-1] if track else _centroid(evs, buoys)
        groups.append({
            "group_id": f"seal-{n}",
            "buoys": ids,
            "detections": len(evs),
            "estimate_min": max(hourly.values()) if hourly else 0,
            "center": {"lat": center["lat"], "lon": center["lon"]},
            "radius_km": max(radius(buoys[i]) for i in ids),
            "last_seen": evs[-1]["timestamp"],
            "drift": drift,
            "track": track,
        })
    groups.sort(key=lambda g: -g["detections"])
    return {
        "generated_at": _iso(now),
        "window_h": hours,
        "total_detections": len(events),
        "estimate_min": sum(g["estimate_min"] for g in groups),
        "method": "Модель звука находит вокализации тюленя на буях. Буи с пересекающимися зонами слышимости — "
                  "одна группа; центр группы — средняя точка буёв, взвешенная уверенностью; дрейф — смещение "
                  "центра от первого отрезка окна к последнему. Численность — оценка снизу по пику вокализаций за час.",
        "buoys": sorted(per_buoy.values(), key=lambda p: p["buoy_id"]),
        "groups": groups,
    }
