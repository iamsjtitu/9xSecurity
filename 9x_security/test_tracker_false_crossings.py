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


class _Clock:
    """Deterministic time for engine de-dup tests (process_frame uses time.time())."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _cross_frames(y_from, y_to, step=6):
    return [{"bbox": (720, y - 40, 840, y + 40), "label": "truck"} for y in range(y_from, y_to, step)]


def _drive(e, clock, dets_per_frame, fps=8.0):
    import numpy as np
    frame = np.zeros((540, 960, 3), np.uint8)
    events = []
    for dets in dets_per_frame:
        e.detector.dets = dets
        clock.t += 1.0 / fps
        events += e.process_frame(frame)[1]
    return events


class _ScriptDet:
    dets = []

    def detect(self, f):
        return list(self.dets)


def _cleanup(events):
    for ev in events:
        if os.path.exists(ev["image_path"]):
            os.remove(ev["image_path"])


def test_engine_drops_recreated_track_duplicate(tmp_path, monkeypatch):
    """Truck crosses, stops just past the line, detection drops for 3 s (track aged out),
    re-detected at the SAME spot and moves on -> appeared-at-line on a new track = same
    vehicle -> one event only."""
    import engine as eng
    clock = _Clock()
    monkeypatch.setattr(eng.time, "time", clock)
    e = _engine(tmp_path, _ScriptDet())
    script = [[d] for d in _cross_frames(60, 190)]          # crosses the line (~y 130) and stops at y=184
    script += [[{"bbox": (720, 144, 840, 224), "label": "truck"}]] * 5
    script += [[]] * 25                                      # lost (max_disappeared 20)
    script += [[{"bbox": (720, 144, 840, 224), "label": "truck"}]] * 3   # re-created right there
    script += [[d] for d in _cross_frames(190, 330)]        # drives on into the yard
    events = _drive(e, clock, script)
    assert len(events) == 1, [(ev["direction"], ev["id"]) for ev in events]
    _cleanup(events)


def test_engine_counts_following_vehicles_through_the_same_gate(tmp_path, monkeypatch):
    """USER (06-09): car Entry sent, then a JCB and another car 8-12 s later got NO alert.
    Old rule: any same-direction crossing at the same spot within 20 s = duplicate. A
    following vehicle must count when the earlier one drove away (track lost far from the
    gate) or is still tracked deeper in the yard."""
    import engine as eng
    clock = _Clock()
    monkeypatch.setattr(eng.time, "time", clock)
    e = _engine(tmp_path, _ScriptDet())
    car1 = [[d] for d in _cross_frames(60, 420)]            # crosses and drives out of view (60 frames = 7.5 s)
    gap = [[]] * 24                                          # 3 s empty gate -> car1 track aged out far away
    jcb = [[{**d, "label": "truck"}] for d in _cross_frames(60, 250)]   # JCB crosses ~11 s after car1
    jcb_parked = [[{"bbox": (720, 204, 840, 284), "label": "truck"}]]  # JCB waits in the yard, still tracked
    car2 = [[jcb_parked[0][0], d] for d in _cross_frames(60, 190)]     # car2 crosses while JCB is visible
    events = _drive(e, clock, car1 + gap + jcb + jcb_parked * 16 + car2)
    dirs = [ev["direction"] for ev in events]
    assert len(events) == 3 and len(set(dirs)) == 1, [(ev["direction"], ev["vehicle_type"]) for ev in events]
    _cleanup(events)


def test_engine_recreated_track_inside_near_band_is_one_event(tmp_path, monkeypatch):
    """Reviewer reproducer (iteration 23): vehicle stops INSIDE the near band just past the line,
    detection drops, the re-created track's 'appeared-at-line' fires with a bbox that no longer
    overlaps the on-line crossing bbox -> must still be recognised as the same vehicle."""
    import engine as eng
    clock = _Clock()
    monkeypatch.setattr(eng.time, "time", clock)
    e = _engine(tmp_path, _ScriptDet())
    script = [[d] for d in _cross_frames(60, 184)]                        # crosses, stops at bottom=178 (46px past)
    script += [[{"bbox": (720, 98, 840, 178), "label": "truck"}]] * 5
    script += [[]] * 25                                                   # lost
    script += [[{"bbox": (720, 98, 840, 178), "label": "truck"}]] * 3    # re-created at the same spot
    script += [[d] for d in _cross_frames(184, 330)]                      # moves on -> appeared-at-line would fire
    events = _drive(e, clock, script)
    assert len(events) == 1, [(ev["direction"], ev["id"]) for ev in events]
    _cleanup(events)


def test_engine_second_box_on_same_truck_is_one_event(tmp_path, monkeypatch):
    import engine as eng
    clock = _Clock()
    monkeypatch.setattr(eng.time, "time", clock)
    e = _engine(tmp_path, _ScriptDet())
    # a long truck: YOLO gives two overlapping boxes (cab + body) crossing together
    script = [[d, {"bbox": (730, d["bbox"][1] + 30, 850, d["bbox"][3] + 30), "label": "truck"}] for d in _cross_frames(60, 330)]
    events = _drive(e, clock, script)
    assert len(events) == 1, [(ev["direction"], ev["id"]) for ev in events]
    _cleanup(events)


def _engine(tmp_path, det, **over):
    import config
    import database
    import engine as eng
    db = database.EventDB(db_path=str(tmp_path / "z.db"))
    cfg = {**config.DEFAULTS, "enable_plate": False, "detect_frame_skip": 1,
           "line": {"x1": 700 / 960, "y1": 119 / 540, "x2": 850 / 960, "y2": 143 / 540}, **over}
    e = eng.SecurityEngine(cfg=cfg, db=db, detector=det, plate_reader=None)
    e.notifier.enabled = False
    return e


def test_ignore_zone_blocks_counting_and_hint_flags_parked_vehicle(tmp_path):
    import numpy as np

    class Det:
        def __init__(self):
            self.i = 0

        def detect(self, f):
            self.i += 1
            y = 60 + min(self.i, 50) * 6
            return [{"bbox": (720, y - 40, 840, y + 40), "label": "truck"}]

    frame = np.zeros((540, 960, 3), np.uint8)
    # zone covering the whole right part where this truck drives -> nothing counted
    e = _engine(tmp_path, Det(), ignore_zones=[{"x1": 0.7, "y1": 0.0, "x2": 1.0, "y2": 1.0}])
    events = []
    for _ in range(60):
        events += e.process_frame(frame)[1]
    assert events == [] and e.tracker.tracks == {}
    # without the zone the same motion counts once
    e2 = _engine(tmp_path, Det())
    events = []
    for _ in range(60):
        events += e2.process_frame(frame)[1]
    assert len(events) == 1
    for ev in events:
        if os.path.exists(ev["image_path"]):
            os.remove(ev["image_path"])


def test_line_hints_edge_and_parked_vehicle(tmp_path, monkeypatch):
    import numpy as np
    import time as _t

    class Parked:
        def detect(self, f):
            return [{"bbox": (700, 80, 900, 300), "label": "truck"}]   # sits right on the line

    frame = np.zeros((540, 960, 3), np.uint8)
    e = _engine(tmp_path, Parked(), line={"x1": 0.6, "y1": 0.2, "x2": 0.99, "y2": 0.25})  # touches right edge
    e.process_frame(frame)
    assert any("kinare" in h for h in e.line_hints), e.line_hints
    t0 = _t.time()
    monkeypatch.setattr(_t, "time", lambda: t0 + 20)     # 20 s later, still not moved
    e.process_frame(frame)
    assert any("khadi gaadi" in h for h in e.line_hints), e.line_hints


# ---- 06-09 report: one Bolero gave Entry + Exit while standing at the gate; a truck that
# ---- was only half inside the picture (right edge) gave a blank 'Entry'.
LINE_MID = ((50, 380), (900, 380))   # horizontal line across the gate, 960x540


def _run_mid(frames, frame_size=(960, 540)):
    tr = CentroidTracker()
    tr.near_band = 54
    out = []
    for i, dets in enumerate(frames):
        out += [(i, c["to_side"], c["via"]) for c in tr.update(dets, LINE_MID, now=i * 0.15, frame_size=frame_size)]
    return out


def test_vehicle_stopping_at_gate_with_jitter_counts_once():
    import random
    random.seed(7)
    frames = []
    for i in range(40):                                # drives in: bottom edge 250 -> 410 (crosses y=380)
        bot = 250 + i * 4
        frames.append([{"bbox": (350, bot - 300, 650, bot), "label": "truck"}])
    for _ in range(400):                               # stands at the gate ~60 s, box jitters +-20 px
        bot = 410 + random.randint(-20, 20)
        frames.append([{"bbox": (350, bot - 300, 650, bot), "label": "truck"}])
    ev = _run_mid(frames)
    assert len(ev) == 1, ev


def test_vehicle_entering_from_picture_edge_is_not_counted():
    frames = []
    for i in range(30):                                # appears half outside the right edge, grows into view
        x1 = 960 - 40 - i * 8
        frames.append([{"bbox": (x1, 175, 960, 520), "label": "truck"}])  # bottom already below the line
    for i in range(30):                                # fully inside now, driving left along the road
        frames.append([{"bbox": (700 - i * 6, 175, 960 - 5 - i * 6, 520), "label": "truck"}])
    assert _run_mid(frames) == []
