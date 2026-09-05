"""User's yard (05-09-2026): a parked truck at the right edge produced 15-20 Entry/Exit
alerts while the road was empty, and one Eicher gave 2-3 photos. Rules now:
count only when the vehicle's path crosses the DRAWN yellow segment, never on
track identity swaps/teleports, once per direction per vehicle."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from tracker import CentroidTracker, _hits_segment  # noqa: E402

A, B = (700, 119), (850, 143)          # user's line in 960x540 (top-right, nearly horizontal)
PARKED = {"bbox": (810, 90, 960, 540), "label": "truck"}


def _run(frames, near_band=54, min_gap_s=3.0):
    tr = CentroidTracker(min_gap_s=min_gap_s)
    tr.near_band = near_band
    out = []
    for i, dets in enumerate(frames):
        out += [(i, c["to_side"], c["via"]) for c in tr.update(dets, (A, B), now=i * 0.15)]
    return out


def test_far_vehicle_cannot_steal_parked_truck_track():
    # a small far-away truck appears above the line, 255px from the parked truck's centroid
    frames = []
    for i in range(120):
        dets = [PARKED]
        if 20 <= i < 60:
            dets = ([{"bbox": (870, 40, 940, 90), "label": "truck"}] + dets) if i % 2 else (dets + [{"bbox": (870, 40, 940, 90), "label": "truck"}])
        frames.append(dets)
    assert _run(frames) == []


def test_real_crossing_on_the_segment_counts_once():
    frames = []
    for i in range(60):  # comes down from the far road across the middle of the yellow line
        y = 60 + i * 6
        frames.append([PARKED, {"bbox": (720, y - 40, 840, y + 40), "label": "truck"}])
    ev = _run(frames)
    assert len(ev) == 1 and ev[0][2] == "cross", ev


def test_crossing_the_infinite_line_but_not_the_segment_is_ignored():
    frames = []
    for i in range(60):  # same motion but 300px left of the drawn segment
        y = 60 + i * 6
        frames.append([{"bbox": (380, y - 40, 500, y + 40), "label": "car"}])
    assert _run(frames) == []


def test_same_direction_twice_without_return_is_one_event():
    frames = []
    for i in range(40):                       # cross down (Entry)
        y = 60 + i * 6
        frames.append([{"bbox": (720, y - 40, 840, y + 40), "label": "truck"}])
    for i in range(40):                       # drift back up OUTSIDE the segment (left of it)
        y, x = 300 - i * 6, 720 - i * 10
        frames.append([{"bbox": (x, y - 40, x + 120, y + 40), "label": "truck"}])
    for i in range(40):                       # come back right, then down across the segment again
        y, x = 60 + i * 6, 320 + i * 10
        frames.append([{"bbox": (x, y - 40, x + 120, y + 40), "label": "truck"}])
    ev = _run(frames)
    assert len(ev) == 1, ev


def test_hits_segment_geometry():
    assert _hits_segment((775, 100), (775, 160), A, B)          # through the middle
    assert not _hits_segment((500, 80), (500, 160), A, B)       # far left of the segment
    assert not _hits_segment((775, 100), (775, 110), A, B)      # does not reach the line
    assert _hits_segment((860, 100), (860, 170), A, B)          # within the 15% end margin


def test_engine_drops_recreated_track_duplicate(tmp_path):
    import numpy as np
    import config
    import database
    import engine as eng

    class Det:
        def __init__(self):
            self.i = 0

        def detect(self, f):
            self.i += 1
            y = 60 + (self.i % 45) * 6            # the same truck "re-appears" as a new track every 45 frames
            return [{"bbox": (720, y - 40, 840, y + 40), "label": "truck"}] if (self.i % 45) else []

    db = database.EventDB(db_path=str(tmp_path / "e.db"))
    cfg = {**config.DEFAULTS, "enable_plate": False, "detect_frame_skip": 1,
           "line": {"x1": 700 / 960, "y1": 119 / 540, "x2": 850 / 960, "y2": 143 / 540}}
    e = eng.SecurityEngine(cfg=cfg, db=db, detector=Det(), plate_reader=None)
    e.notifier.enabled = False
    frame = np.zeros((540, 960, 3), np.uint8)
    events = []
    for _ in range(135):                      # three passes of the same motion within a few seconds
        events += e.process_frame(frame)[1]
    assert len(events) == 1, [(ev["direction"], ev["id"]) for ev in events]
    for ev in events:
        if os.path.exists(ev["image_path"]):
            os.remove(ev["image_path"])
