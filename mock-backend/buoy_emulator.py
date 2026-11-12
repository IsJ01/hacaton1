#!/usr/bin/env python3
"""Эмулятор буёв и спутника: шлёт данные на бэкенд (мок или настоящий) по контракту v2.

Каждый тик (--interval секунд):
  * звук   → POST /api/v1/events    (случайный буй)
  * солёность → POST /api/v1/salinity (все буи; иногда «сброс» — провал солёности на несколько тиков)
  * спутник → POST /api/v1/satellite (раз в --sat-every тиков чистый снимок; при сбросе — снимок с пятном)

  python3 buoy_emulator.py                           # всё сразу, тик 5 с
  python3 buoy_emulator.py --audio --interval 2      # звук с реальным wav в base64
  python3 buoy_emulator.py --url http://host:8000 --count 20
  python3 buoy_emulator.py --no-sound --no-satellite # только солёность
  python3 buoy_emulator.py --discharge-now           # сразу начать со сброса (для демо)
"""
import argparse, base64, json, random, time, urllib.error, urllib.request, uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def call(url, payload=None, data=None, headers=None):
    if payload is not None:
        data, headers = json.dumps(payload).encode(), {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def multipart(fields, file_field, file_name, file_bytes, mime):
    b = uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    body += (f'--{b}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n'
             f"Content-Type: {mime}\r\n\r\n").encode() + file_bytes + f"\r\n--{b}--\r\n".encode()
    return body, {"Content-Type": f"multipart/form-data; boundary={b}"}


def safe(fn):
    try:
        return fn()
    except urllib.error.HTTPError as e:
        print("  HTTP", e.code, e.read().decode()[:200])
    except OSError as e:
        print("  нет связи с бэкендом:", e)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--interval", type=float, default=5.0, help="пауза между тиками, сек")
    p.add_argument("--count", type=int, default=0, help="сколько тиков (0 = бесконечно)")
    p.add_argument("--audio", action="store_true", help="прикладывать wav из ./library в base64")
    p.add_argument("--salinity-norm", type=float, default=12.5, help="нормальная солёность, ‰")
    p.add_argument("--discharge-chance", type=float, default=0.05, help="вероятность начать сброс на тике")
    p.add_argument("--discharge-now", action="store_true", help="начать сброс на первом же тике")
    p.add_argument("--sat-every", type=int, default=10, help="чистый снимок раз в N тиков")
    p.add_argument("--no-sound", action="store_true")
    p.add_argument("--no-salinity", action="store_true")
    p.add_argument("--no-satellite", action="store_true")
    a = p.parse_args()

    buoys = None
    while buoys is None:  # ждём, пока бэкенд поднимется (удобно в docker compose)
        buoys = safe(lambda: call(f"{a.url}/api/v1/buoys")["buoys"])
        if buoys is None:
            time.sleep(2)
    wavs = sorted((HERE / "library").glob("*.wav"))
    if a.audio and not wavs:
        from audio_gen import generate_library
        generate_library(HERE / "library")
        wavs = sorted((HERE / "library").glob("*.wav"))
    sat = sorted((HERE / "sat_library").glob("*.jpg"))
    sat_clean = [f for f in sat if f.name.startswith("clean")]
    sat_bad = [f for f in sat if f.name.startswith("discharge")]

    def send_image(f, buoy):
        g = buoy["geo"]
        fields = {"geo[lat]": round(g["lat"] + random.uniform(-0.03, 0.03), 4),
                  "geo[lon]": round(g["lon"] + random.uniform(0.0, 0.05), 4), "timestamp": now()}
        body, headers = multipart(fields, "file", f.name, f.read_bytes(), "image/jpeg")
        r = safe(lambda: call(f"{a.url}/api/v1/satellite", data=body, headers=headers))
        if r:
            verdict = f"СБРОС → {r['event']['event_id']}" if r["anomaly_detected"] else "чисто"
            print(f"  спутник  {r['image_id']}  у {buoy['buoy_id']}  ({f.name})  → {verdict}")

    discharge = None  # (buoy, осталось тиков)
    tick = 0
    while not a.count or tick < a.count:
        tick += 1
        print(f"[{now()}] тик {tick}")

        if not a.no_sound:
            body = {"buoy_id": random.choice(buoys)["buoy_id"], "timestamp": now()}
            note = ""
            if a.audio:
                f = random.choice(wavs)
                body["audio"] = {"format": "wav", "data_base64": base64.b64encode(f.read_bytes()).decode()}
                note = f" ({f.name})"
            r = safe(lambda: call(f"{a.url}/api/v1/events", body))
            if r:
                c = r["event"]["classification"]
                print(f"  звук     {r['event_id']}  {body['buoy_id']}  → {c['label']} {c['confidence']}{note}")

        start = discharge is None and (random.random() < a.discharge_chance or (a.discharge_now and tick == 1))
        if start:
            discharge = [random.choice(buoys), 5]
            print(f"  !!! эмулируем сброс у {discharge[0]['buoy_id']}")

        if not a.no_salinity:
            line = []
            for b in buoys:
                value = a.salinity_norm + random.gauss(0, 0.2)
                if discharge and b is discharge[0]:
                    value -= random.uniform(3.5, 6.0)
                value = round(value, 2)
                r = safe(lambda: call(f"{a.url}/api/v1/salinity",
                                      {"buoy_id": b["buoy_id"], "salinity": value, "timestamp": now()}))
                if r:
                    mark = f" СБРОС→{r['event']['event_id']}" if r["anomaly_detected"] else ""
                    line.append(f"{b['buoy_id']}={value}{mark}")
            print("  солёность " + "  ".join(line))

        if not a.no_satellite:
            if start and sat_bad:
                send_image(random.choice(sat_bad), discharge[0])
            elif a.sat_every and tick % a.sat_every == 0 and sat_clean:
                send_image(random.choice(sat_clean), random.choice(buoys))

        if discharge:
            discharge[1] -= 1
            if discharge[1] <= 0:
                discharge = None
        if not a.count or tick < a.count:
            time.sleep(a.interval)


if __name__ == "__main__":
    main()
