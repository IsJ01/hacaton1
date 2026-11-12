"""Расследование гибели тюленей: где и когда животное погибло и что в это время происходило рядом.

Туши находят на берегу через дни после гибели — ткани разложены, причину установить нельзя.
Поэтому идём от места находки назад:

  1. давность гибели по состоянию туши → окно времени (свежая 0.5–2 сут, средняя 2–5, разложившаяся 5–12)
  2. обратный дрейф: течение вдоль берега + снос ветром (реальный ветер Open-Meteo за прошлые дни),
     ансамбль из 12 вариантов (снос 1–4 %, течение ×0.5–1.5) → коридор, где туша могла оказаться в воде
  3. улики в коридоре и окне: что слышали буи (лодки, ночные лодки, выстрелы), что показали датчики
     (метан, солёность, сбросы), землетрясения из каталога EMSC, штиль (ветер < 2 м/с)
  4. оценка версий: сети и браконьеры / выброс газа / загрязнение — с перечнем улик и что проверить

Модель упрощённая (течение задано константой, береговая линия — ломаной). Это инструмент для того,
чтобы инспектор знал, где брать пробы и чьи записи поднимать, а не заключение о причине.
"""
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import storage
from config import (ANOMALY_SALINITY_DELTA, CURRENT_SPEED_MS, CURRENT_TO_DEG, LEEWAY, LISTEN_RADIUS_KM,
                    METHANE_NORM_NMOL, METHANE_THRESHOLD_NMOL, SALINITY_NORM, SEISMIC_URL, USE_SEISMIC_API,
                    USE_WEATHER_API, WEATHER_TIMEOUT_SEC, WEATHER_URL)

CONDITIONS = {  # давность гибели по состоянию туши, сутки (допущение по данным о дрейфе туш морских животных)
    "fresh": (0.5, 2, "свежая"),
    "moderate": (2, 5, "средней давности"),
    "decomposed": (5, 12, "разложившаяся"),
}
# Береговая линия Дагестана: (широта, долгота берега). Море — восточнее. Грубая ломаная для запрета дрейфа по суше.
COAST = [(41.9, 48.33), (42.3, 48.05), (42.7, 47.75), (43.0, 47.52), (43.25, 47.57), (43.5, 47.66),
         (43.7, 47.75), (43.8, 47.78), (43.95, 47.80), (44.02, 47.70), (44.08, 47.45), (44.2, 47.25),
         (44.5, 47.10), (44.9, 47.05)]
NIGHT_HOURS = set(range(20, 24)) | set(range(0, 6))  # местное время (UTC+3)
_weather_cache: dict = {}


def _ts(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _coast_lon(lat: float) -> float:
    pts = COAST
    if lat <= pts[0][0]:
        return pts[0][1]
    for (a, la), (b, lb) in zip(pts, pts[1:]):
        if a <= lat <= b:
            return la + (lb - la) * (lat - a) / (b - a)
    return pts[-1][1]


def _move(p: dict, east_m: float, north_m: float) -> dict:
    lat = p["lat"] + north_m / 111_320
    lon = p["lon"] + east_m / (111_320 * math.cos(math.radians(p["lat"])))
    lon = max(lon, _coast_lon(lat) + 0.02)  # туша не дрейфует по суше
    return {"lat": lat, "lon": lon}


# ---------- внешние данные: ветер и землетрясения ----------
def wind_history(geo: dict, start: datetime, end: datetime) -> tuple[dict, str]:
    """Почасовой ветер {час UTC: (скорость м/с, откуда дует°)} и источник. Без интернета — климатическая норма."""
    key = (round(geo["lat"], 1), round(geo["lon"], 1), end.strftime("%Y-%m-%dT%H"))
    if key in _weather_cache:
        return _weather_cache[key]
    result = ({}, "climatology")
    if USE_WEATHER_API:
        days = min(92, max(1, math.ceil((datetime.now(timezone.utc) - start).total_seconds() / 86400) + 1))
        q = urllib.parse.urlencode({"latitude": geo["lat"], "longitude": geo["lon"], "past_days": days, "forecast_days": 1,
                                    "hourly": "wind_speed_10m,wind_direction_10m", "wind_speed_unit": "ms", "timezone": "UTC"})
        try:
            with urllib.request.urlopen(f"{WEATHER_URL}?{q}", timeout=WEATHER_TIMEOUT_SEC) as r:
                h = json.load(r)["hourly"]
            result = ({t[:13]: (s, d) for t, s, d in zip(h["time"], h["wind_speed_10m"], h["wind_direction_10m"])
                       if s is not None and d is not None}, "open-meteo")
        except (OSError, ValueError, KeyError):
            pass
    _weather_cache[key] = result
    return result


def earthquakes(center: dict, start: datetime, end: datetime) -> tuple[list[dict], str]:
    """Землетрясения M ≥ 2.5 в радиусе ~250 км из каталога EMSC."""
    if not USE_SEISMIC_API:
        return [], "отключено"
    q = urllib.parse.urlencode({"format": "json", "lat": center["lat"], "lon": center["lon"], "maxradius": 2.3,
                                "start": start.strftime("%Y-%m-%dT%H:%M:%S"), "end": end.strftime("%Y-%m-%dT%H:%M:%S"),
                                "minmag": 2.5, "limit": 50})
    try:
        with urllib.request.urlopen(f"{SEISMIC_URL}?{q}", timeout=8) as r:
            raw = r.read()
        feats = json.loads(raw).get("features", []) if raw.strip() else []
    except (OSError, ValueError):
        return [], "нет связи с EMSC"
    out = []
    for f in feats:
        p = f["properties"]
        out.append({"time": p["time"][:19] + "Z", "mag": p.get("mag"), "region": p.get("flynn_region"),
                    "geo": {"lat": p["lat"], "lon": p["lon"]}, "depth_km": p.get("depth")})
    return out, "EMSC"


# ---------- обратный дрейф ----------
def back_drift(found: dict, found_at: datetime, max_days: float) -> dict:
    hours = int(max_days * 24)
    start = found_at - timedelta(hours=hours)
    wind, wind_src = wind_history(found, start, found_at)
    cur = math.radians(CURRENT_TO_DEG)
    members = [(lw, cs) for lw in (0.01, 0.02, 0.03, 0.04) for cs in (0.5, 1.0, 1.5)]
    tracks = [[dict(found)] for _ in members]
    for h in range(1, hours + 1):
        t = found_at - timedelta(hours=h)
        speed, frm = wind.get(t.strftime("%Y-%m-%dT%H"), (5.0, 315.0))  # норма: северо-западный 5 м/с
        to = math.radians(frm + 180)
        for k, (lw, cs) in enumerate(members):
            ve = CURRENT_SPEED_MS * cs * math.sin(cur) + lw * speed * math.sin(to)
            vn = CURRENT_SPEED_MS * cs * math.cos(cur) + lw * speed * math.cos(to)
            prev = tracks[k][-1]
            tracks[k].append(_move(prev, -ve * 3600, -vn * 3600))  # назад во времени — против скорости
    points = []
    for h in range(hours + 1):
        lat = sum(tr[h]["lat"] for tr in tracks) / len(tracks)
        lon = sum(tr[h]["lon"] for tr in tracks) / len(tracks)
        c = {"lat": lat, "lon": lon}
        spread = max(_km(c, tr[h]) for tr in tracks)
        points.append({"t": _iso(found_at - timedelta(hours=h)), "lat": round(lat, 4), "lon": round(lon, 4),
                       "spread_km": round(spread + 2, 1)})
    return {"points": points, "wind_source": wind_src, "wind": wind}


def _km(a: dict, b: dict) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


# ---------- расследование ----------
def investigate(carcass: dict, buoys: dict, distance_km: Callable) -> dict:
    found_at = _ts(carcass["found_at"])
    d_min, d_max, cond_ru = CONDITIONS.get(carcass["condition"], CONDITIONS["moderate"])
    w_start, w_end = found_at - timedelta(days=d_max), found_at - timedelta(days=d_min)
    drift = back_drift(carcass["geo"], found_at, d_max)
    zone = [p for p in drift["points"] if w_start <= _ts(p["t"]) <= w_end]  # где туша могла быть в момент гибели
    ev_from, ev_to = _iso(w_start - timedelta(hours=12)), _iso(w_end + timedelta(hours=6))

    def near_zone(geo: dict, extra_km: float) -> Optional[float]:
        best = min((_km(geo, p) - p["spread_km"] for p in zone), default=None)
        return round(max(0.0, best), 1) if best is not None and best <= extra_km else None

    relevant = {}
    for b in buoys.values():
        d = near_zone(b["geo"], float(b.get("listen_radius_km") or LISTEN_RADIUS_KM) + 10)
        if d is not None:
            relevant[b["buoy_id"]] = d

    _, sounds = storage.list_sound_events(None, None, ev_from, ev_to, 5000, 0)
    sounds = [e for e in sounds if e["buoy_id"] in relevant]
    boats = [e for e in sounds if e["classification"]["label"] == "boat"]
    night = [e for e in boats if (_ts(e["timestamp"]) + timedelta(hours=3)).hour in NIGHT_HOURS]
    shots = [e for e in sounds if e["classification"]["label"] == "gunshot"]
    _, water = storage.list_water_events(None, ev_from, ev_to, 5000, 0)
    water = [w for w in water if near_zone(w["geo"], 30) is not None or w["source"]["buoy_id"] in relevant]
    gas_events = [w for w in water if w["type"] == "gas_seep"]
    discharges = [w for w in water if w["type"] == "illegal_discharge"]
    methane = []
    sal_jumps = []
    for bid in relevant:
        for t in storage.list_telemetry(bid, ev_from, 5000):
            if t["timestamp"] <= ev_to and t.get("methane_nmol") is not None:
                methane.append({**t, "buoy_id": bid})
        sal_jumps += [m for m in storage.list_measurements(bid, ev_from, ev_to, 5000)
                      if abs(m["salinity"] - SALINITY_NORM) >= ANOMALY_SALINITY_DELTA]
    methane_max = max(methane, key=lambda t: t["methane_nmol"], default=None)

    center = zone[len(zone) // 2] if zone else drift["points"][0]
    quakes, quake_src = earthquakes(center, w_start - timedelta(days=2), w_end)
    for q in quakes:
        q["distance_km"] = round(min(_km(q["geo"], p) for p in zone or drift["points"]), 1)
    quakes = [q for q in quakes if q["distance_km"] <= 200]
    quakes.sort(key=lambda q: -(q["mag"] or 0))

    # штиль: самая длинная серия часов с ветром < 2 м/с в окне гибели
    calm, run = 0, 0
    t = w_start
    while t <= w_end:
        s = drift["wind"].get(t.strftime("%Y-%m-%dT%H"), (None,))[0]
        run = run + 1 if s is not None and s < 2 else 0
        calm = max(calm, run)
        t += timedelta(hours=1)

    # покрытие: доля часов окна, когда хоть один буй рядом присылал данные
    covered_hours = {x["timestamp"][:13] for bid in relevant for x in storage.list_telemetry(bid, ev_from, 5000)}
    covered_hours |= {e["timestamp"][:13] for e in sounds}
    total_hours = max(1, int((w_end - w_start).total_seconds() // 3600))
    coverage = min(1.0, len([h for h in covered_hours if _iso(w_start)[:13] <= h <= _iso(w_end)[:13]]) / total_hours)

    hyps = []
    ev = []
    score = 0.0
    if boats:
        score += min(0.45, 0.15 * len(boats)); ev.append(f"лодки у буёв в коридоре: {len(boats)}")
    if night:
        score += min(0.3, 0.15 * len(night)); ev.append(f"из них ночью (20:00–06:00): {len(night)}")
    if shots:
        score += min(0.45, 0.3 * len(shots)); ev.append(f"выстрелы: {len(shots)}")
    hyps.append({"id": "nets", "title": "Сети и браконьеры", "score": round(min(1, score), 2), "evidence": ev,
                 "check": "Осмотреть акваторию коридора на сети (особенно мелкоячеистые); поднять записи буёв за ночные часы; "
                          "вскрытие — следы запутывания, асфиксия в сети."})
    ev, score = [], 0.0
    if methane_max and methane_max["methane_nmol"] >= METHANE_THRESHOLD_NMOL:
        score += min(0.6, 0.3 * methane_max["methane_nmol"] / METHANE_THRESHOLD_NMOL)
        ev.append(f"метан в воде до {methane_max['methane_nmol']:.0f} нмоль/л (фон ~{METHANE_NORM_NMOL:.0f}) — "
                  f"{buoys[methane_max['buoy_id']]['name']}, {methane_max['timestamp'][:16].replace('T', ' ')} UTC")
    elif gas_events:
        score += 0.4; ev.append(f"события «выброс газа»: {len(gas_events)}")
    if quakes:
        q = quakes[0]
        score += min(0.3, 0.08 * (q["mag"] or 0)); ev.append(f"землетрясение M{q['mag']} ({q['region']}), {q['distance_km']} км, {q['time'][:16].replace('T', ' ')} UTC")
    if calm >= 6:
        score += 0.15; ev.append(f"штиль {calm} ч подряд (газ не рассеивается над водой)")
    hyps.append({"id": "gas", "title": "Выброс газа со дна", "score": round(min(1, score), 2), "evidence": ev,
                 "check": "Пробы воды на растворённый метан в коридоре; сверить с сейсмикой; вскрытие — признаки асфиксии без травм."})
    ev, score = [], 0.0
    if discharges:
        score += min(0.6, 0.3 * len(discharges)); ev.append(f"сбросы (солёность / спутник): {len(discharges)}")
    if sal_jumps:
        score += min(0.3, 0.05 * len(sal_jumps)); ev.append(f"скачки солёности: {len(sal_jumps)}")
    hyps.append({"id": "pollution", "title": "Загрязнение, сброс", "score": round(min(1, score), 2), "evidence": ev,
                 "check": "Пробы воды и тканей на нефтепродукты и тяжёлые металлы; проверить источники сброса выше по течению."})
    hyps.sort(key=lambda h: -h["score"])
    top = hyps[0]
    verdict = (f"Наиболее вероятно: {top['title'].lower()}" if top["score"] >= 0.3 and top["score"] - hyps[1]["score"] >= 0.1
               else "Версии близки — нужны пробы и вскрытие" if top["score"] >= 0.3
               else "Недостаточно данных: в окне гибели рядом не было буёв или они ничего не зафиксировали")

    return {
        "carcass": carcass,
        "condition_ru": cond_ru,
        "death_window": {"from": _iso(w_start), "to": _iso(w_end)},
        "drift": {"points": drift["points"], "zone": zone, "wind_source": drift["wind_source"],
                  "assumptions": f"течение {CURRENT_SPEED_MS} м/с на {CURRENT_TO_DEG:.0f}°, снос ветром 1–4 % (база {LEEWAY:.0%}), "
                                 "береговая линия упрощена"},
        "buoys_in_range": [{"buoy_id": k, "name": buoys[k]["name"], "distance_km": v} for k, v in sorted(relevant.items(), key=lambda x: x[1])],
        "coverage": round(coverage, 2),
        "evidence": {
            "boats": len(boats), "night_boats": len(night), "gunshots": len(shots),
            "methane_max": methane_max and {"value": methane_max["methane_nmol"], "buoy_id": methane_max["buoy_id"],
                                            "timestamp": methane_max["timestamp"]},
            "gas_events": len(gas_events), "discharges": len(discharges), "salinity_jumps": len(sal_jumps),
            "calm_hours": calm, "earthquakes": quakes[:5], "earthquake_source": quake_src,
            "events": sorted([{"kind": "sound", "event_id": e["event_id"], "label": e["classification"]["label"],
                               "timestamp": e["timestamp"], "buoy_id": e["buoy_id"]} for e in boats + shots] +
                             [{"kind": "water", "event_id": w["event_id"], "label": w["type"], "timestamp": w["timestamp"],
                               "buoy_id": w["source"]["buoy_id"]} for w in water], key=lambda x: x["timestamp"]),
        },
        "hypotheses": hyps,
        "verdict": verdict,
        "note": "Оценка помогает решить, где брать пробы и чьи записи поднимать. Причину гибели устанавливает экспертиза.",
    }
