"""Multi-camera AI check (container): N file-backed cameras run TOGETHER through the real service
(real YOLO per camera) for ~70 s; prints each camera's fps / AI ms / AI frames / events per gate and
the PC Load summary, then removes the test cameras + their events/snapshots.
Usage: python tests/multicam_load_check.py [n_cameras=5] [seconds=70]"""
import os
import sqlite3
import sys
import time

import cv2
import numpy as np
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402

BASE = "http://127.0.0.1:8971"
BUS = "/root/.venv/lib/python3.11/site-packages/ultralytics/assets/bus.jpg"
VIDEO = "/tmp/nx_loadcam.mp4"


def make_video(seconds=40, fps=12):
    src = cv2.imread(BUS)
    bus = cv2.resize(src[220:900, 20:800], (330, 280))
    w, h = 960, 540
    vw = cv2.VideoWriter(VIDEO, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    n = seconds * fps
    for i in range(n):
        frame = np.full((h, w, 3), 120, np.uint8)
        cv2.rectangle(frame, (0, 300), (w, h), (90, 90, 90), -1)
        period = 8 * fps  # one crossing every 8 s, alternating direction
        k, ph = divmod(i, period)
        y = int(-280 + ph / period * (h + 280)) if k % 2 == 0 else int(h - ph / period * (h + 280))
        x = 300
        y1, y2 = max(0, y), min(h, y + 280)
        if y2 > y1:
            frame[y1:y2, x:x + 330] = bus[y1 - y:y2 - y]
        vw.write(frame)
    vw.release()


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    secs = int(sys.argv[2]) if len(sys.argv) > 2 else 70
    make_video()
    tok = requests.post(f"{BASE}/api/login", json={"username": "admin", "password": "9xsecurity"}, timeout=5).json()["token"]
    H = {"X-Auth-Token": tok}
    ids = []
    for i in range(1, n + 1):
        r = requests.post(f"{BASE}/api/cameras", headers=H, json={"name": f"LoadTest {i}", "gate": f"LT-{i}", "rtsp_url": VIDEO}, timeout=10).json()
        ids.append(r["cameras"][-1]["id"])
    print(f"added {n} cameras: {ids}; sampling {secs}s")
    t0 = time.time()
    try:
        while time.time() - t0 < secs:
            time.sleep(5)
            st = requests.get(f"{BASE}/api/state", headers=H, timeout=10).json()
            ld = st["load"]
            row = " | ".join(f"{c['name'][-1]}:{c['load']} {c['fps']}fps {c['ai_ms']}ms f={c['ai_frames']}"
                             for c in st["cameras"] if c["id"] in ids)
            print(f"t+{time.time() - t0:4.0f}s cpu={ld['cpu_pct']}% ram={ld['ram_pct']}% app={ld['app_ram_mb']}MB level={ld['level']} ai_total={ld['ai_total_ms']}ms/s || {row}")
        st = requests.get(f"{BASE}/api/state", headers=H, timeout=10).json()
        today = time.strftime("%Y-%m-%d")
        evs = requests.get(f"{BASE}/api/events", headers=H, params={"date": today, "limit": 500}, timeout=10).json()
        evs = evs if isinstance(evs, list) else evs.get("events", [])
        per_gate = {}
        for ev in evs:
            if str(ev.get("gate", "")).startswith("LT-"):
                per_gate[ev["gate"]] = per_gate.get(ev["gate"], 0) + 1
        print("\nRESULT")
        ok = True
        for c in st["cameras"]:
            if c["id"] not in ids:
                continue
            good = (c["ai_frames"] or 0) > 20 and c["load"] in ("ok", "warn", "bad")
            ok &= good
            print(f"  {c['name']}: ai_frames={c['ai_frames']} fps={c['fps']} ai_ms={c['ai_ms']} skip={c['drop_pct']}% load={c['load']} "
                  f"model={c['ai_model']} events(gate {c['gate']})={per_gate.get(c['gate'], 0)} -> {'AI OK' if good else 'NO AI!'}")
        print(f"  PC: {st['load']}")
        print("ALL CAMERAS AI RUNNING" if ok else "SOME CAMERA HAD NO AI")
    finally:
        for cid in ids:
            requests.delete(f"{BASE}/api/cameras/{cid}", headers=H, timeout=10)
        con = sqlite3.connect(config.DB_PATH)
        rows = con.execute("select id, image_path from events where gate like 'LT-%'").fetchall()
        for _id, p in rows:
            if p and os.path.exists(p):
                os.remove(p)
        con.execute("delete from events where gate like 'LT-%'")
        con.commit()
        con.close()
        print(f"cleanup: removed {n} cameras, {len(rows)} test events")


if __name__ == "__main__":
    main()
