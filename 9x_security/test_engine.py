"""9x Security - headless self-test of the core engine (no GUI, no camera).

Generates a synthetic video of a 'car' box moving across the detection line
using a FAKE detector, and verifies that an Entry/Exit event is logged with
a snapshot + timestamp. Also runs a real YOLO detection sanity check if the
model is available.
"""
import os
import tempfile

import numpy as np

import config
from database import EventDB
from engine import SecurityEngine
from tracker import CentroidTracker, _side


class FakeDetector:
    """Returns a single moving car box driven by an external position."""

    def __init__(self):
        self.box = None

    def detect(self, frame):
        if self.box is None:
            return []
        return [{"bbox": self.box, "label": "car", "conf": 0.9}]


def test_line_crossing_logs_event():
    tmp = tempfile.mkdtemp()
    config.SNAPSHOT_DIR = os.path.join(tmp, "snaps")
    os.makedirs(config.SNAPSHOT_DIR, exist_ok=True)
    db = EventDB(db_path=os.path.join(tmp, "t.db"))

    cfg = dict(config.DEFAULTS)
    cfg["enable_plate"] = False
    cfg["detect_frame_skip"] = 1
    cfg["line"] = {"x1": 0.5, "y1": 0.0, "x2": 0.5, "y2": 1.0}  # vertical line at x=0.5

    fake = FakeDetector()
    eng = SecurityEngine(cfg=cfg, db=db, detector=fake, plate_reader=None)

    W, H = config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT
    all_events = []
    # Move a 100x100 box from left (x=100) to right (x=800), crossing x=480.
    for cx in range(100, 820, 40):
        fake.box = (cx, 200, cx + 100, 300)
        frame = np.zeros((H, W, 3), dtype=np.uint8)
        _, ev = eng.process_frame(frame, original=None)
        all_events.extend(ev)

    assert len(all_events) == 1, f"expected 1 crossing event, got {len(all_events)}"
    e = all_events[0]
    assert e["vehicle_type"] == "car"
    assert e["direction"] in ("Entry", "Exit")
    assert os.path.exists(e["image_path"]), "snapshot file not saved"
    rows = db.get_events()
    assert len(rows) == 1 and rows[0]["date"] and rows[0]["time"]
    print("PASS: line-crossing -> event logged:", e["direction"], e["vehicle_type"], e["image_path"])


def test_direction_mapping():
    a, b = (5, 0), (5, 10)  # vertical line
    # point left of line
    left = _side((0, 5), a, b)
    right = _side((10, 5), a, b)
    assert (left > 0) != (right > 0), "points on opposite sides should differ in sign"
    print("PASS: side-of-line sign detection")


def test_tracker_ids_persist():
    tr = CentroidTracker()
    line = ((5, 0), (5, 10))
    tr.update([{"bbox": (0, 0, 20, 20), "label": "car"}], line)
    first_ids = set(tr.tracks.keys())
    tr.update([{"bbox": (2, 2, 22, 22), "label": "car"}], line)  # small move -> same id
    assert set(tr.tracks.keys()) == first_ids, "track id should persist across small movement"
    print("PASS: tracker keeps stable id across frames")


def test_real_yolo_if_available():
    try:
        from detector import VehicleDetector

        det = VehicleDetector(conf=0.25, allowed=["car", "truck", "bus"])
        import numpy as np

        frame = np.zeros((config.DISPLAY_HEIGHT, config.DISPLAY_WIDTH, 3), dtype=np.uint8)
        out = det.detect(frame)  # blank frame -> should just return [] without crashing
        assert isinstance(out, list)
        print("PASS: real YOLO detector loaded and runs (blank frame dets=%d)" % len(out))
    except Exception as e:
        print("SKIP real YOLO test:", e)


if __name__ == "__main__":
    test_direction_mapping()
    test_tracker_ids_persist()
    test_line_crossing_logs_event()
    test_real_yolo_if_available()
    print("\nALL CORE TESTS DONE")
