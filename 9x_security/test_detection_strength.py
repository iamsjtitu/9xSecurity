"""Detection strength: re-crossing (exit then entry), truck label voting, agnostic NMS,
model tiers (auto/fast/accurate) + options endpoint."""
import os
import sys
import time

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
import config  # noqa: E402
from tracker import CentroidTracker  # noqa: E402

LINE = ((100, 270), (860, 270))


def _box(bot, w=300, h=100):
    return {"bbox": (300, bot - h, 300 + w, bot), "label": "car"}


def test_same_vehicle_exit_then_entry_counts_twice():
    """Car exits (bottom goes above line), turns around while still visible, comes
    back in 10s later -> must count Exit AND Entry (old code counted once)."""
    tr = CentroidTracker(near_band=54)
    t = 1000.0
    cross = []
    for bot in (400, 360, 320, 285, 255, 230, 200):  # exit: moving up past y=270
        cross += tr.update([_box(bot)], LINE, now=t)
        t += 0.2
    assert len(cross) == 1 and cross[0]["to_side"] == tr._sign(-1) or cross[0]["to_side"] != 0
    first_to = cross[0]["to_side"]
    for _ in range(40):  # waits outside ~8s, still detected
        tr.update([_box(200)], LINE, now=t)
        t += 0.2
    for bot in (230, 255, 285, 320, 360, 400):  # comes back in
        cross += tr.update([_box(bot)], LINE, now=t)
        t += 0.2
    assert len(cross) == 2, cross
    assert cross[1]["to_side"] == -first_to and cross[1]["nth"] == 2
    assert len(tr.tracks) == 1  # same track all along


def test_jitter_at_line_does_not_double_count():
    tr = CentroidTracker(near_band=54)
    t = 0.0
    cross = []
    for bot in (330, 300, 275, 268, 272, 266, 274, 269, 300, 330):  # hovers on the line
        cross += tr.update([_box(bot)], LINE, now=t)
        t += 0.2
    assert len(cross) <= 1


def test_quick_bounce_within_gap_not_counted():
    tr = CentroidTracker(near_band=54, min_gap_s=3.0)
    t = 0.0
    cross = []
    for bot in (400, 320, 250, 200):  # out
        cross += tr.update([_box(bot)], LINE, now=t)
        t += 0.2
    for bot in (250, 320, 400):  # straight back within 1s -> hysteresis re-armed but gap too short
        cross += tr.update([_box(bot)], LINE, now=t)
        t += 0.2
    assert len(cross) == 1


def test_truck_label_voting():
    tr = CentroidTracker()
    labels = ["car", "car", "truck", "car", "truck", "car", "car"]  # truck 2/7 = 28% -> car
    for i, lab in enumerate(labels):
        tr.update([{"bbox": (300, 300 + i, 600, 400 + i), "label": lab}], ((0, 500), (960, 500)))
    t = list(tr.tracks.values())[0]
    assert t.label == "car"
    tr.update([{"bbox": (300, 310, 600, 410), "label": "truck"}], ((0, 500), (960, 500)))  # 3/8 = 37.5%
    assert t.label == "truck"
    # crossing event carries the voted label (a few more 'truck' frames while crossing)
    cross = []
    for bot in (450, 480, 520, 560):
        cross += tr.update([{"bbox": (300, bot - 100, 600, bot), "label": "truck"}], ((0, 500), (960, 500)))
    assert len(cross) == 1 and cross[0]["label"] == "truck"


def test_resolve_model_paths(tmp_path, monkeypatch):
    import detector

    n = tmp_path / "yolov8n.pt"
    n.write_bytes(b"n")
    monkeypatch.setattr(config, "MODEL_PATH", str(n))
    assert detector.resolve_model_path("auto") == (str(n), "fast")
    assert detector.resolve_model_path("accurate") == (str(n), "fast")  # s missing -> fallback
    s = tmp_path / "yolov8s.pt"
    s.write_bytes(b"s")
    assert detector.resolve_model_path("auto") == (str(s), "accurate")
    assert detector.resolve_model_path("accurate") == (str(s), "accurate")
    assert detector.resolve_model_path("fast") == (str(n), "fast")


@pytest.mark.skipif(not os.path.exists(os.path.join(os.path.dirname(__file__), "yolov8s.pt")), reason="yolov8s not present")
def test_accurate_model_detects_bus_and_is_agnostic():
    import cv2

    from detector import VehicleDetector, resolve_model_path

    path, tier = resolve_model_path("accurate")
    assert tier == "accurate"
    det = VehicleDetector(model_path=path, conf=0.35)
    img = cv2.imread("/root/.venv/lib/python3.11/site-packages/ultralytics/assets/bus.jpg")
    img = cv2.resize(img, (960, 540))
    dets = det.detect(img)
    labels = [d["label"] for d in dets]
    assert "bus" in labels
    # agnostic NMS: no two heavily-overlapping boxes for the same vehicle
    def iou(a, b):
        ax1, ay1, ax2, ay2 = a; bx1, by1, bx2, by2 = b
        iw, ih = max(0, min(ax2, bx2) - max(ax1, bx1)), max(0, min(ay2, by2) - max(ay1, by1))
        inter = iw * ih
        return inter / float((ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter or 1)
    for i in range(len(dets)):
        for j in range(i + 1, len(dets)):
            assert iou(dets[i]["bbox"], dets[j]["bbox"]) < 0.7


def test_options_endpoint_model_and_confidence(monkeypatch):
    from fastapi.testclient import TestClient

    import service

    store = dict(config.DEFAULTS)
    monkeypatch.setattr(service, "_cfg", lambda: dict(store))
    monkeypatch.setattr(service.config, "save_config", lambda c: store.update(c))
    tok = "m" * 48
    service._tokens[tok] = time.time()
    c = TestClient(service.app)
    c.headers["X-Auth-Token"] = tok
    assert c.post("/api/options", json={"detector_model": "accurate", "confidence": 0.25}).json()["ok"]
    assert store["detector_model"] == "accurate" and store["confidence"] == 0.25
    c.post("/api/options", json={"detector_model": "bogus", "confidence": 5})
    assert store["detector_model"] == "accurate" and store["confidence"] == 0.9  # clamped, bogus ignored
    st = c.get("/api/state").json()
    assert st["detector_model"] == "accurate" and st["confidence"] == 0.9 and "ai_model" in st
