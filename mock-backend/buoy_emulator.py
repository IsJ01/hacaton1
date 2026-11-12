#!/usr/bin/env python3
"""Эмулятор буёв: раз в N секунд шлёт POST /api/v1/events на бэкенд (мок или настоящий).

  python3 buoy_emulator.py                          # событие каждые 5 с, без аудио
  python3 buoy_emulator.py --audio --interval 2     # с реальным wav из ./library (base64)
  python3 buoy_emulator.py --url http://192.168.1.10:8000 --count 10
"""
import argparse, base64, json, random, time, urllib.error, urllib.request
from datetime import datetime, timezone
from pathlib import Path


def call(url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--interval", type=float, default=5.0, help="пауза между событиями, сек")
    p.add_argument("--count", type=int, default=0, help="сколько событий отправить (0 = бесконечно)")
    p.add_argument("--audio", action="store_true", help="прикладывать wav из ./library в base64")
    a = p.parse_args()

    buoys = [b["buoy_id"] for b in call(f"{a.url}/api/v1/buoys")["buoys"]]
    files = sorted((Path(__file__).parent / "library").glob("*.wav"))
    if a.audio and not files:
        from audio_gen import generate_library
        generate_library(Path(__file__).parent / "library")
        files = sorted((Path(__file__).parent / "library").glob("*.wav"))

    sent = 0
    while not a.count or sent < a.count:
        body = {
            "buoy_id": random.choice(buoys),
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        note = ""
        if a.audio:
            f = random.choice(files)
            body["audio"] = {"format": "wav", "data_base64": base64.b64encode(f.read_bytes()).decode()}
            note = f" (отправлен {f.name})"
        try:
            ev = call(f"{a.url}/api/v1/events", body)["event"]
            c = ev["classification"]
            print(f"{ev['timestamp']}  {ev['buoy_id']}  {ev['event_id']}  → {c['label']} {c['confidence']}{note}")
        except urllib.error.HTTPError as e:
            print("HTTP", e.code, e.read().decode())
        except OSError as e:
            print("нет связи с бэкендом:", e)
        sent += 1
        if not a.count or sent < a.count:
            time.sleep(a.interval)


if __name__ == "__main__":
    main()
