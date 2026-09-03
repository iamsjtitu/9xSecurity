"""Worker restart must never leave two capture loops alive (double events / camera
session limits). Regression for the start()/stop() race found while testing sub-stream switch."""
import os
import sys
import threading
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import service  # noqa: E402

ALIVE = {}


class Cam:
    def __init__(self, tag):
        self.tag, self.rel = tag, False
        ALIVE[tag] = True

    def read(self):
        time.sleep(0.02)
        f = np.zeros((540, 960, 3), np.uint8)
        f[:] = 50 if self.tag == "A" else 200
        return True, f

    def isOpened(self):
        return not self.rel

    def get(self, p):
        return 0.0

    def release(self):
        self.rel = True
        ALIVE[self.tag] = False


def test_restart_stops_old_loop_and_releases_old_camera(monkeypatch):
    calls = []

    def fake_open(src, running):
        tag = "A" if not calls else "B"
        calls.append(src)
        return Cam(tag)

    monkeypatch.setattr(service, "open_stream", fake_open)
    monkeypatch.setattr(service, "_cfg", lambda: {**service.config.DEFAULTS, "rtsp_url": "rtsp://cam/x"})
    monkeypatch.setattr(service, "SecurityEngine", lambda **kw: (_ for _ in ()).throw(RuntimeError("no AI in test")))
    w = service.Worker()
    w.start()
    time.sleep(1.5)
    assert w.connected and ALIVE.get("A") is True
    t_old = w.thread
    w.start()  # reconnect (what /api/camera/connect, substream, mainstream do)
    time.sleep(1.5)
    assert w.connected and w.thread is not t_old
    assert not t_old.is_alive(), "old capture loop still running"
    assert ALIVE["A"] is False and ALIVE["B"] is True, "old camera session not released"
    # frames come from the NEW loop only
    jpg = w.latest()
    img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
    assert int(img[0, 0, 0]) > 150
    assert threading.active_count() < 50
    w.stop()
    time.sleep(0.5)
    assert ALIVE["B"] is False
