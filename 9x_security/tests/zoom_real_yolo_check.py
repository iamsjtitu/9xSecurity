"""Real-YOLO E2E through the running service: a far night DOG (full-frame YOLO says 'person') walking
over the line must give 0 person events (zoom check), a night PERSON must still give 1.
Usage: python tests/zoom_real_yolo_check.py"""
import os
import sqlite3
import sys
import time

import cv2
import numpy as np
import requests

sys.path.insert(0, os.path.dirname(__file__))
from multicam_load_check import BASE  # noqa: E402

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
LINE = {"x1": 0.05, "y1": 0.5, "x2": 0.95, "y2": 0.5}


def make_video(name, scale, path, seconds=36, fps=12):
    img = cv2.resize(cv2.imread(os.path.join(ASSETS, name)), (960, 540))
    small = cv2.resize(img, None, fx=scale, fy=scale)
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (960, 540))
    for i in range(seconds * fps):
        t = i / fps
        canvas = np.full((540, 960, 3), 30, np.uint8)
        # the whole scene slides down during 2-14 s so the subject walks over the line at y=270
        y0 = int(-small.shape[0] * 0.6 + min(max(t - 2, 0), 12) / 12 * 540) if t >= 2 else int(-small.shape[0] * 0.6)
        x0 = (960 - small.shape[1]) // 2
        ys, ye = max(0, y0), min(540, y0 + small.shape[0])
        if ye > ys:
            canvas[ys:ye, x0:x0 + small.shape[1]] = small[ys - y0:ye - y0]
        vw.write(canvas)
    vw.release()


def run(H, video, want, label):
    r = requests.post(f"{BASE}/api/cameras", headers=H,
                      json={"name": "Zoom E2E", "gate": "ZOOME2E", "rtsp_url": video}, timeout=10).json()
    cid = r["cameras"][-1]["id"]
    try:
        requests.post(f"{BASE}/api/cameras/{cid}/activate", headers=H, timeout=10).raise_for_status()
        requests.post(f"{BASE}/api/line", headers=H, json=LINE, timeout=5).raise_for_status()
        requests.post(f"{BASE}/api/line2", headers=H, json={"clear": True}, timeout=5)
        t0 = time.time()
        cam = {}
        while time.time() - t0 < 100:  # the file ladder (tcp/udp first) can take ~20 s to open
            time.sleep(3)
            st = requests.get(f"{BASE}/api/state", headers=H, timeout=10).json()
            cam = next(c for c in st["cameras"] if c["id"] == cid)
            if cam.get("ai_frames", 0) >= 120:  # ~20 s of video processed: the crossing (t~8-12 s) is behind us
                break
        time.sleep(4)  # person group quiet time
        evs = requests.get(f"{BASE}/api/events?all=1", headers=H, timeout=10).json()["events"]
        mine = [e for e in evs if e.get("gate") == "ZOOME2E"]
        print(f"{label}: ai_frames={cam.get('ai_frames')} events={[(e['direction'], e['vehicle_type']) for e in mine]} want={want}")
        return len(mine) == want, mine
    finally:
        requests.delete(f"{BASE}/api/cameras/{cid}", headers=H, timeout=10)
        requests.post(f"{BASE}/api/cameras/cam1/activate", headers=H, timeout=10)


def main():
    make_video("night_dog.jpg", 0.45, "/tmp/nx_zoom_dog.mp4")
    make_video("night_person.jpg", 0.6, "/tmp/nx_zoom_person.mp4")
    tok = requests.post(f"{BASE}/api/login", json={"username": "admin", "password": "9xsecurity"}, timeout=5).json()["token"]
    H = {"X-Auth-Token": tok}
    requests.post(f"{BASE}/api/options", headers=H, json={"detector_model": "fast", "enable_person": True,
                                                            "confidence": 0.3}, timeout=5)
    results, all_ev = [], []
    for video, want, label in (("/tmp/nx_zoom_dog.mp4", 0, "DOG (full-frame 'person') over the line"),
                               ("/tmp/nx_zoom_person.mp4", 1, "PERSON over the line")):
        ok, ev = run(H, video, want, label)
        results.append(ok)
        all_ev += ev
    db = sqlite3.connect(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "events.db"))
    db.execute("DELETE FROM events WHERE gate='ZOOME2E'")
    db.commit()
    for e in all_ev:
        if e.get("image_path") and os.path.exists(e["image_path"]):
            os.remove(e["image_path"])
    requests.post(f"{BASE}/api/options", headers=H, json={"detector_model": "auto", "enable_person": False,
                                                            "confidence": 0.35}, timeout=5)
    requests.post(f"{BASE}/api/line", headers=H, json={"x1": 0.1, "y1": 0.2, "x2": 0.9, "y2": 0.8}, timeout=5)
    print("RESULT:", "PASS" if all(results) else "FAIL", results)
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
