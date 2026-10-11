"""Iteration 33 — additional scenarios for the standing-vehicle fix.

Written by the testing agent. These tests DO NOT modify tracker.py / engine.py.
Goals (per user review request):
  * Confirm the new 'clearly past' / momentum rules kill the standing-wobble
    flip-flop under more varied conditions (slow 0.3 s cadence 'slow PC',
    random wobble, different run lengths).
  * Confirm genuine-crossing MISSES do not creep in (the user's #1 fear):
    - fast single-step crossing (1 detection past the line)
    - two-detection crossing
    - creeping entry
    - exit + re-entry
  * Confirm 'resumed_from' memory works at 1 min, 4 min, and expires at 6 min.
  * Confirm twin YOLO box (CAB+FULL) on a counted truck never doubles.
  * Confirm a vehicle wobbling BEFORE the line still counts once it enters.
  * Engine-level: user's real geometry -> exactly one Entry; jitter line logged.
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import engine as eng  # noqa: E402
from tracker import CentroidTracker, RESUME_S  # noqa: E402
from test_tracker_false_crossings import _Clock, _ScriptDet, _cleanup, _drive, _engine  # noqa: E402

W, H = 960, 540
LINE_N = {"x1": 0.3284315839406615, "y1": 0.48575919040134935,
          "x2": 0.9839147871835784, "y2": 0.9911450147583087}
LINE = ((int(LINE_N["x1"] * W), int(LINE_N["y1"] * H)),
        (int(LINE_N["x2"] * W), int(LINE_N["y2"] * H)))
FULL = (510, 25, 870, 440)      # +14 px gate side
CAB = (555, 105, 885, 415)      # -21 px road side
FS = (W, H)


def _shift(b, sx, sy):
    return (b[0] + sx, b[1] + sy, b[2] + sx, b[3] + sy)


def _truck(b):
    return {"bbox": b, "label": "truck"}


class Sim:
    def __init__(self, dt=0.15):
        self.tr = CentroidTracker(min_gap_s=3.0)
        self.tr.near_band = 0.10 * H
        self.t = 1000.0
        self.dt_default = dt
        self.events = []
        self.kinds = []

    def step(self, dets, dt=None):
        dt = dt if dt is not None else self.dt_default
        out = self.tr.update(dets, LINE, now=self.t, frame_size=FS)
        self.kinds += [r["kind"] for r in self.tr.rejects]
        self.events += [(round(self.t - 1000, 2), c["to_side"], c["via"], c["track_id"]) for c in out]
        self.t += dt
        return out

    def approach(self, target=FULL, steps=20):
        for i in range(steps, -1, -1):
            self.step([_truck(_shift(target, -5 * i, -10 * i))])

    def drive_in(self, start=FULL, steps=20):
        for i in range(1, steps + 1):
            self.step([_truck(_shift(start, -10 * i, 5 * i))])
        return _shift(start, -10 * steps, 5 * steps)

    def gap(self, seconds, dt=0.15):
        for _ in range(max(1, int(seconds / dt))):
            self.step([], dt=dt)


# ---------------- Standing-wobble variants ----------------

def test_slow_pc_cadence_wobble_no_false_crossing():
    """Scenario (b): a 'slow PC' at 0.3 s per frame (3 fps). Even with longer
    gaps between frames the jitter must not count."""
    s = Sim(dt=0.30)
    s.approach()
    assert [e[1] for e in s.events] == [1]
    rnd = random.Random(42)
    for _ in range(200):  # 60 s of wobble at 0.3 s
        s.step([_truck(FULL if rnd.random() < 0.5 else CAB)])
    assert [e[1] for e in s.events] == [1], s.events
    assert "jitter" in s.kinds


def test_fully_random_wobble_patterns_no_false_crossing():
    """Random run lengths from 1 to 20 frames of each shape for 90 s."""
    s = Sim()
    s.approach()
    assert [e[1] for e in s.events] == [1]
    rnd = random.Random(7)
    end = s.t + 90.0
    while s.t < end:
        box = FULL if rnd.random() < 0.5 else CAB
        for _ in range(rnd.randint(1, 20)):
            if s.t >= end:
                break
            s.step([_truck(box)])
    assert [e[1] for e in s.events] == [1], s.events


def test_wobble_before_line_only_then_real_entry():
    """Scenario (h): wobbles entirely on the road side (CAB only, tiny jitter),
    never crosses, then finally enters -> 1 Entry."""
    s = Sim()
    # Approach to CAB first so birth-side is established on road side.
    s.approach(target=CAB)
    assert s.events == []
    rnd = random.Random(3)
    # tiny shape jitter around CAB — always keeps bottom-centre on the road side.
    for _ in range(300):
        jx, jy = rnd.randint(-3, 3), rnd.randint(-3, 3)
        s.step([_truck(_shift(CAB, jx, jy))])
    assert s.events == [], s.events
    s.drive_in(start=CAB, steps=25)
    assert [e[1] for e in s.events] == [1], s.events


# ---------------- Memory / resume ----------------

def test_resume_at_one_minute():
    s = Sim()
    s.approach()
    s.gap(4)
    s.t += 60                               # 1 min later
    for _ in range(20):
        s.step([_truck(FULL)])
    assert "resumed" in s.kinds
    tr = next(iter(s.tr.tracks.values()))
    assert tr.resumed_from == 1 and tr.crossings == 1
    s.drive_in()
    assert [e[1] for e in s.events] == [1], s.events


def test_resume_at_four_minutes_still_inside_window():
    s = Sim()
    s.approach()
    s.gap(4)
    s.t += 240                               # 4 min < RESUME_S=300
    for _ in range(20):
        s.step([_truck(FULL)])
    tr = next(iter(s.tr.tracks.values()))
    assert tr.resumed_from == 1 and tr.crossings == 1
    s.drive_in()
    assert [e[1] for e in s.events] == [1], s.events


def test_resume_memory_expires_exactly_after_RESUME_S():
    """Documented behaviour: >5 min gap re-arms as a new vehicle."""
    s = Sim()
    s.approach()
    s.gap(4)
    s.t += RESUME_S + 30     # 5.5 min > 300 s
    for _ in range(5):
        s.step([_truck(FULL)])
    s.drive_in()
    # second genuine Entry after memory expiry
    assert [e[1] for e in s.events] == [1, 1], s.events


# ---------------- Genuine crossings must still count ----------------

def test_two_detection_crossing_counts():
    """A fast vehicle visible only two frames: one on road side, one past the line."""
    s = Sim()
    car = (560, 290, 760, 440)      # 200x150 car
    s.step([{"bbox": _shift(car, 0, -140), "label": "car"}])
    s.step([{"bbox": _shift(car, 0, 0), "label": "car"}], dt=0.4)
    assert [e[1] for e in s.events] == [1], s.events


def test_creeping_entry_counts_once_when_clearly_past():
    """Vehicle slowly creeps across in ~3 px per frame — counts eventually, exactly once."""
    s = Sim()
    s.approach(target=CAB)                      # stop 21 px on road side
    for _ in range(10):                         # 1.5 s stationary -> momentum gone
        s.step([_truck(CAB)])
    assert s.events == []
    pos = CAB
    for i in range(1, 40):
        pos = _shift(CAB, -3 * i, 3 * i)
        s.step([_truck(pos)])
    assert [e[1] for e in s.events] == [1], s.events


def test_real_exit_reentry_counts_entry_exit_entry():
    """Scenario (c): deep in -> drive out past the road -> come back -> [Entry, Exit, Entry]."""
    s = Sim()
    s.approach()
    deep = s.drive_in(steps=25)
    # stand deep inside for 5 s
    for _ in range(int(5 / 0.15)):
        s.step([_truck(deep)])
    # drive out ~200 px back onto the road side
    out = deep
    for i in range(1, 40):
        out = _shift(deep, 10 * i, -5 * i)
        s.step([_truck(out)])
    # stand on the road for 5 s
    for _ in range(int(5 / 0.15)):
        s.step([_truck(out)])
    # drive back in deep
    for i in range(1, 50):
        s.step([_truck(_shift(out, -10 * i, 5 * i))])
    sides = [e[1] for e in s.events]
    assert sides == [1, -1, 1], s.events


def test_two_following_trucks_each_count():
    """Scenario (d) extended: Truck A counts and parks deep inside; Truck B arrives
    on the road while A is still tracked -> 2 Entries."""
    s = Sim()
    s.approach()
    deep = s.drive_in(steps=15)
    # Hold A deep, add B from far away road
    for i in range(25, -1, -1):
        s.step([_truck(deep), _truck(_shift(FULL, -5 * i, -10 * i))])
    assert [e[1] for e in s.events] == [1, 1], s.events


# ---------------- Engine-level user geometry ----------------

def test_engine_user_scenario_slow_cadence(tmp_path, monkeypatch):
    """Same as the existing engine test but at 4 fps (slow PC) -> still exactly one Entry."""
    clock = _Clock()
    monkeypatch.setattr(eng.time, "time", clock)
    logs = []
    monkeypatch.setattr(eng, "clog", lambda m: logs.append(m))
    e = _engine(tmp_path, _ScriptDet(), line=LINE_N, entry_direction="pos")
    script = [[_truck(_shift(FULL, -5 * i, -10 * i))] for i in range(20, -1, -1)]
    script += [[_truck(FULL if (i // 4) % 2 == 0 else CAB)] for i in range(240)]      # 60 s @ 4 fps
    script += [[_truck(_shift(FULL, -10 * i, 5 * i))] for i in range(1, 21)]
    events = _drive(e, clock, script, fps=4.0)
    assert [ev["direction"] for ev in events] == ["Entry"], [(ev["direction"], ev["id"]) for ev in events]
    assert any("jitter" in m for m in logs)
    _cleanup(events)


def test_engine_tailgating_still_counts_both_with_new_rules(tmp_path, monkeypatch):
    """Regression: new 'clearly past' / momentum rule MUST NOT suppress a genuine
    following vehicle's crossing — two tailgating cars on the user geometry each count.
    Mirrors the existing tailgating test but on the user's line + 'pos' entry direction."""
    clock = _Clock()
    monkeypatch.setattr(eng.time, "time", clock)
    e = _engine(tmp_path, _ScriptDet(), line=LINE_N, entry_direction="pos")
    # Two 160x120 cars travelling along the same path, car B trails A by ~24 frames (~3 s).
    car = (560, 290, 760, 440)
    script = []
    for i in range(60):
        a = _shift(car, -3 * i, -200 + 10 * i)   # moves from road onto gate side
        frame = [{"bbox": a, "label": "car"}]
        j = i - 24
        if 0 <= j < 60:
            b = _shift(car, -3 * j, -200 + 10 * j)
            # separate column slightly so association doesn't glue them
            b = _shift(b, 180, 0)
            frame.append({"bbox": b, "label": "car"})
        script.append(frame)
    events = _drive(e, clock, script)
    entries = [ev for ev in events if ev["direction"] == "Entry"]
    assert len(entries) == 2, [(ev["direction"], ev["id"]) for ev in events]
    _cleanup(events)
