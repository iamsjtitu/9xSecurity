import sys, tempfile, time
sys.path.insert(0, "/app/9x_security")
import numpy as np
import config, database, engine as eng


class FakeDet:
    """Emits a bbox stepping across the vertical mid-line."""
    def __init__(self):
        self.x = 310
    def detect(self, f):
        self.x += 60
        return [{"bbox": (self.x, 200, self.x + 120, 320), "label": "car"}]


class SlowPlate:
    def __init__(self):
        self.reads = 0
    def warmup(self):
        return True
    def read(self, crop):
        self.reads += 1
        time.sleep(0.5)  # simulate slow CPU OCR
        return "MH12AB1234"


db = database.EventDB(db_path=tempfile.mktemp(suffix=".db"))
cfg = {**config.DEFAULTS, "enable_plate": True, "detect_frame_skip": 1,
       "line": {"x1": 0.5, "y1": 0.0, "x2": 0.5, "y2": 1.0}}
pr = SlowPlate()
e = eng.SecurityEngine(cfg=cfg, db=db, detector=FakeDet(), plate_reader=pr)
e.notifier.enabled = False

frame = np.zeros((540, 960, 3), np.uint8)
all_events = []
t0 = time.time()
for _ in range(8):
    _, evs = e.process_frame(frame)
    all_events += evs
loop_time = time.time() - t0

assert len(all_events) == 1, f"expected 1 crossing, got {len(all_events)}"
ev = all_events[0]
assert ev["plate"] == "", "event must be logged immediately with empty plate"
assert loop_time < 0.5, f"frame loop blocked by OCR! took {loop_time:.2f}s"
print(f"PASS 1: crossing logged instantly (loop {loop_time*1000:.0f}ms), OCR did not block")

time.sleep(1.5)  # let async OCR finish
rows = db.query_events(all=True) if hasattr(db, "query_events") else None
import sqlite3
r = db.conn.execute("SELECT plate FROM events WHERE id=?", (ev["id"],)).fetchone()
assert r["plate"] == "MH12AB1234", dict(r)
assert pr.reads >= 1
print("PASS 2: plate backfilled async into DB:", r["plate"])

import os
if os.path.exists(ev["image_path"]):
    os.remove(ev["image_path"])
print("ALL ASYNC OCR TESTS PASSED")
