"""Real-YOLO E2E for the person/bike line: a bus (vehicle) crossing ONLY the main line = 1 event;
the same bus crossing ONLY the person/bike line region = 0 events. Temporary file camera, cleaned up.
Usage: python tests/line2_real_yolo_check.py"""
import os
import sqlite3
import sys
import time

import cv2
import numpy as np
import requests

sys.path.insert(0, os.path.dirname(__file__))
from multicam_load_check import BASE, BUS  # noqa: E402

VIDEO = "/tmp/nx_line2cam.mp4"
LEFT = {"x1": 0.05, "y1": 0.5, "x2": 0.55, "y2": 0.5}    # bus path (x 300-630, wheels at 465) crosses this
RIGHT = {"x1": 0.62, "y1": 0.5, "x2": 0.95, "y2": 0.5}   # bus never reaches this


def make_video(seconds=36, fps=12):
    """ONE downward bus crossing during 2-10 s, then an empty yard (no file replay inside the test window)."""
    src = cv2.imread(BUS)
    bus = cv2.resize(src[220:900, 20:800], (330, 280))
    w, h = 960, 540
    vw = cv2.VideoWriter(VIDEO, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for i in range(seconds * fps):
        frame = np.full((h, w, 3), 120, np.uint8)
        cv2.rectangle(frame, (0, 300), (w, h), (90, 90, 90), -1)
        t = i / fps
        if 2 <= t < 10:
            y = int(-280 + (t - 2) / 8 * (h + 280))
            y1, y2 = max(0, y), min(h, y + 280)
            if y2 > y1:
                frame[y1:y2, 300:630] = bus[y1 - y:y2 - y]
        vw.write(frame)
    vw.release()


def run(H, main_line, line2, want, label):
    r = requests.post(f"{BASE}/api/cameras", headers=H,
                      json={"name": "Line2 E2E", "gate": "L2E2E", "rtsp_url": VIDEO}, timeout=10).json()
    cid = r["cameras"][-1]["id"]
    try:
        # /api/line and /api/line2 configure the VIEWED (active) camera -> view this one first
        requests.post(f"{BASE}/api/cameras/{cid}/activate", headers=H, timeout=10).raise_for_status()
        requests.post(f"{BASE}/api/line", headers=H, json=main_line, timeout=5).raise_for_status()
        requests.post(f"{BASE}/api/line2", headers=H, json=line2, timeout=5).raise_for_status()
        t0 = time.time()
        while time.time() - t0 < 30:
            time.sleep(4)
            st = requests.get(f"{BASE}/api/state", headers=H, timeout=10).json()
            cam = next(c for c in st["cameras"] if c["id"] == cid)
        evs = requests.get(f"{BASE}/api/events?all=1", headers=H, timeout=10).json()["events"]
        mine = [e for e in evs if e.get("gate") == "L2E2E"]
        print(f"{label}: ai_frames={cam.get('ai_frames')} line2_in_state={st['line2']} "
              f"events={[(e['direction'], e['vehicle_type']) for e in mine]} want={want}")
        return len(mine) == want, mine
    finally:
        requests.delete(f"{BASE}/api/cameras/{cid}", headers=H, timeout=10)
        requests.post(f"{BASE}/api/cameras/cam1/activate", headers=H, timeout=10)


def main():
    make_video()
    tok = requests.post(f"{BASE}/api/login", json={"username": "admin", "password": "9xsecurity"}, timeout=5).json()["token"]
    H = {"X-Auth-Token": tok}
    requests.post(f"{BASE}/api/options", headers=H, json={"detector_model": "fast", "enable_two_wheeler": True,
                                                            "enable_person": True}, timeout=5)
    results = []
    all_ev = []
    for main_line, line2, want, label in ((LEFT, RIGHT, 1, "CASE A bus over MAIN line"),
                                          (RIGHT, LEFT, 1, "CASE B bus over PERSON/BIKE line only (total stays 1)")):
        ok, ev = run(H, main_line, line2, want, label)
        results.append(ok)
        all_ev += ev
    # cleanup: events + snapshots + line2 + options
    db = sqlite3.connect(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "events.db"))
    db.execute("DELETE FROM events WHERE gate='L2E2E'")
    db.commit()
    for e in all_ev:
        if e.get("image_path") and os.path.exists(e["image_path"]):
            os.remove(e["image_path"])
    requests.post(f"{BASE}/api/line2", headers=H, json={"clear": True}, timeout=5)
    requests.post(f"{BASE}/api/options", headers=H, json={"detector_model": "auto", "enable_two_wheeler": False,
                                                            "enable_person": False}, timeout=5)
    print("RESULT:", "PASS" if all(results) else "FAIL", results)
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
