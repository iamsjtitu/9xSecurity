"""Full Worker loop with a REAL-TIME (paced) source through LatestFrameReader + real YOLO:
bus exits, waits, re-enters at 12 fps while the AI runs slower — both crossings must log."""
import os
import sys
import tempfile
import time

import cv2
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
import config  # noqa: E402
import service  # noqa: E402
from database import EventDB  # noqa: E402

BUS = "/root/.venv/lib/python3.11/site-packages/ultralytics/assets/bus.jpg"


class PacedFileCam:
    """Plays a list of frames at a fixed fps like a live camera (blocks in read())."""

    def __init__(self, frames, fps=12):
        self.frames, self.dt, self.i = frames, 1.0 / fps, 0
        self.t0 = None
        self.released = False

    def read(self):
        if self.t0 is None:
            self.t0 = time.time()
        if self.i >= len(self.frames):
            time.sleep(self.dt)
            return False, None
        due = self.t0 + self.i * self.dt
        time.sleep(max(0, due - time.time()))
        f = self.frames[self.i]
        self.i += 1
        return True, f

    def isOpened(self):
        return not self.released

    def get(self, p):
        return float(int.from_bytes(b"hevc", "little")) if p == cv2.CAP_PROP_FOURCC else 0.0

    def release(self):
        self.released = True


def _frames():
    src = cv2.imread(BUS)
    bus = cv2.resize(src[220:900, 20:800], (330, 280))
    W, H = 960, 540
    bg = np.full((H, W, 3), (90, 110, 120), np.uint8)

    def put(y):
        f = bg.copy()
        yt = y - 280
        fy0, fy1 = max(0, yt), min(H, y)
        s0 = fy0 - yt
        if fy1 > fy0:
            f[fy0:fy1, 300:630] = bus[s0:s0 + (fy1 - fy0)]
        return f

    out = [bg] * 12
    out += [put(int(500 - i * 6)) for i in range(50)]     # exit (up past y=270)
    out += [put(205)] * 48                                 # waits outside 4 s
    out += [put(int(205 + i * 6)) for i in range(50)]     # comes back in
    out += [bg] * 24
    return out


@pytest.mark.skipif(not os.path.exists(BUS), reason="ultralytics assets missing")
def test_worker_live_loop_logs_both_crossings(monkeypatch):
    tmp = tempfile.mkdtemp()
    db = EventDB(db_path=os.path.join(tmp, "e.db"))
    cfg = {**config.DEFAULTS, "rtsp_url": "rtsp://fake-cam/live", "enable_plate": False,
           "line": {"x1": 0.05, "y1": 0.5, "x2": 0.95, "y2": 0.5}, "wa_enabled": False}
    monkeypatch.setattr(service, "_cfg", lambda: dict(cfg))
    monkeypatch.setattr(service, "_db", db)
    monkeypatch.setattr(config, "SNAPSHOT_DIR", os.path.join(tmp, "snaps"))
    cam = PacedFileCam(_frames(), fps=12)
    monkeypatch.setattr(service, "open_stream", lambda s, r: cam)

    w = service.Worker()
    w.start()
    deadline = time.time() + 45
    while time.time() < deadline:
        evs = db.get_events()
        if len(evs) >= 2:
            break
        time.sleep(1)
    w.stop()
    time.sleep(0.5)
    evs = db.get_events()
    assert len(evs) >= 2, f"expected 2 crossings, got {evs}; status={w.status} err={w.ai_error}"
    assert {e["direction"] for e in evs[:2]} == {"Entry", "Exit"}
    assert all(e["vehicle_type"] == "bus" for e in evs[:2])
    assert w.codec == "hevc" and w.frames_dropped >= 0 and w.ai_error == ""
    assert w.ai_frames > 10
