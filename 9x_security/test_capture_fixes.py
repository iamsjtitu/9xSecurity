"""Capture-reliability fixes: bottom-center crossing, persistent data dir migration,
snapshot save failure tolerance, diagnostics endpoint."""
import os
import sys
import tempfile
import time

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
import config  # noqa: E402
from database import EventDB  # noqa: E402
from engine import SecurityEngine  # noqa: E402
from tracker import CentroidTracker  # noqa: E402


class FakeDet:
    def __init__(self):
        self.box = None

    def detect(self, frame):
        return [] if self.box is None else [{"bbox": self.box, "label": "car", "conf": 0.9}]


def test_gate_entry_counted_with_bottom_center():
    """Camera looks down at gate; ground line at y=270. Car crosses moving toward
    camera: bottom edge (wheels) goes from above the line to below it."""
    tr = CentroidTracker()
    line = ((100, 270), (860, 270))
    ys = [(150, 240), (170, 258), (200, 290), (240, 340), (280, 390)]  # (top, bottom)
    crossings = []
    for top, bot in ys:
        crossings += tr.update([{"bbox": (300, top, 600, bot), "label": "car"}], line)
    assert len(crossings) == 1 and crossings[0]["via"] == "cross"
    assert crossings[0]["to_side"] == tr._sign((860 - 100) * (390 - 270))  # inside sign


def test_occluded_gate_vehicle_appears_past_line_still_counted():
    """Wall hides the outside: car is FIRST seen with wheels already 10px inside
    the line, then drives 170px further in. Must count as a crossing (Entry)."""
    tr = CentroidTracker(near_band=54)  # engine sets 10% of 540
    line = ((100, 270), (860, 270))
    ys = [(200, 280), (215, 300), (240, 330), (270, 370), (300, 410), (330, 450)]
    crossings = []
    for top, bot in ys:
        crossings += tr.update([{"bbox": (300, top, 600, bot), "label": "car"}], line)
    assert len(crossings) == 1 and crossings[0]["via"] == "appeared-at-line"
    assert crossings[0]["to_side"] == -crossings[0]["from_side"] != 0
    # keeps driving: no second count
    crossings += tr.update([{"bbox": (300, 360, 600, 480), "label": "car"}], line)
    assert len(crossings) == 1


def test_vehicle_far_from_line_moving_is_not_counted():
    tr = CentroidTracker(near_band=54)
    line = ((100, 270), (860, 270))
    crossings = []
    for bot in range(400, 540, 20):  # parked deep inside, drives around inside
        crossings += tr.update([{"bbox": (300, bot - 100, 600, bot), "label": "car"}], line)
    assert crossings == []


def test_big_fast_vehicle_keeps_track_id():
    tr = CentroidTracker()
    line = ((0, 500), (960, 500))
    tr.update([{"bbox": (100, 100, 500, 400), "label": "truck"}], line)
    tr.update([{"bbox": (250, 130, 650, 430), "label": "truck"}], line)  # moved 150px (> 90)
    assert len(tr.tracks) == 1, "size-adaptive matching should keep one track"


def test_migrate_legacy_data_copies_once():
    src, dst = tempfile.mkdtemp(), tempfile.mkdtemp()
    open(os.path.join(src, "config.json"), "w").write('{"a":1}')
    open(os.path.join(src, "events.db"), "wb").write(b"x")
    os.makedirs(os.path.join(src, "snapshots", "2026-01-01"))
    open(os.path.join(src, "snapshots", "2026-01-01", "a.jpg"), "wb").write(b"j")
    moved = config.migrate_legacy_data(src, dst)
    assert set(moved) == {"config.json", "events.db", "snapshots"}
    assert os.path.isfile(os.path.join(dst, "snapshots", "2026-01-01", "a.jpg"))
    open(os.path.join(dst, "config.json"), "w").write('{"a":2}')
    assert config.migrate_legacy_data(src, dst) == []  # never overwrites
    assert open(os.path.join(dst, "config.json")).read() == '{"a":2}'
    assert config.migrate_legacy_data(src, src) == []


def test_snapshot_save_failure_still_logs_event_and_notifies(monkeypatch):
    tmp = tempfile.mkdtemp()
    blocker = os.path.join(tmp, "snaps")
    open(blocker, "w").write("not a dir")  # makedirs/open will fail
    monkeypatch.setattr(config, "SNAPSHOT_DIR", blocker)
    db = EventDB(db_path=os.path.join(tmp, "t.db"))
    cfg = {**config.DEFAULTS, "enable_plate": False, "detect_frame_skip": 1,
           "line": {"x1": 0.5, "y1": 0.0, "x2": 0.5, "y2": 1.0}}
    fake = FakeDet()
    eng = SecurityEngine(cfg=cfg, db=db, detector=fake, plate_reader=None)
    sent = []
    eng.notifier.notify = lambda ev: sent.append(ev)
    W, H = config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT
    evs = []
    for cx in range(100, 820, 40):
        fake.box = (cx, 200, cx + 100, 300)
        _, ev = eng.process_frame(np.zeros((H, W, 3), dtype=np.uint8))
        evs += ev
    assert len(evs) == 1 and evs[0]["image_path"] == ""
    assert db.get_events()[0]["image_path"] == ""
    assert len(sent) == 1


def test_diagnostics_endpoint():
    from fastapi.testclient import TestClient

    import service

    tok = "d" * 48
    service._tokens[tok] = time.time()
    c = TestClient(service.app)
    r = c.get("/api/diagnostics", headers={"X-Auth-Token": tok})
    assert r.status_code == 200
    d = r.json()
    for k in ("version", "data_dir", "snapshot_write_ok", "events_total", "events_today",
              "engine", "whatsapp", "camera_log", "wa_log", "outbox_pending"):
        assert k in d, k
    assert d["snapshot_write_ok"] is True
    assert "ai_loaded" in d["engine"]
    assert c.get("/api/diagnostics").status_code == 401
