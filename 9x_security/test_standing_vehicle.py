"""USER (11-10-2026, v1.0.39): 'ek gaadi ka bar bar msg kyu aa raha hai'. Diagnostics: track 1238
gave Entry ~10:49:40, Exit 10:50:41, Entry 10:59:52 — the truck stood ON the yellow line waiting
for the gate; its YOLO box wobbled between 'cab + load' and 'cab only' (user's two snapshots),
moving the bottom-centre ~35 px across the line. Rules now: a crossing needs the vehicle CLEARLY
on the new side (>= 20% of its box, or one real run of that length); a counted vehicle lost
standing at the gate (or a 2nd YOLO box on it) is the same vehicle for 5 minutes."""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(__file__))
import engine as eng  # noqa: E402
from tracker import CentroidTracker, RESUME_S  # noqa: E402
from test_tracker_false_crossings import _Clock, _ScriptDet, _cleanup, _drive, _engine  # noqa: E402

W, H = 960, 540
LINE_N = {"x1": 0.3284315839406615, "y1": 0.48575919040134935, "x2": 0.9839147871835784, "y2": 0.9911450147583087}
LINE = ((int(LINE_N["x1"] * W), int(LINE_N["y1"] * H)), (int(LINE_N["x2"] * W), int(LINE_N["y2"] * H)))  # (315,262)-(944,535)
FULL = (510, 25, 870, 440)   # user's 'Entry' snapshot box: cab + tarpaulin load  -> bottom-centre +14 px (gate side)
CAB = (555, 105, 885, 415)   # user's 'Exit' snapshot box 16 s later: cab only    -> bottom-centre -21 px (road side)
FS = (W, H)


def _shift(box, sx, sy):
    return (box[0] + sx, box[1] + sy, box[2] + sx, box[3] + sy)


def _truck(box):
    return {"bbox": box, "label": "truck"}


def _dist(tr, box):
    import math
    from tracker import _side
    a, b = LINE
    return _side(tr.ref_point(box), a, b) / math.hypot(b[0] - a[0], b[1] - a[1])


class Sim:
    """Drives a CentroidTracker with the engine's near band (10% of H) and a 0.15 s cadence."""

    def __init__(self):
        self.tr = CentroidTracker(min_gap_s=3.0)
        self.tr.near_band = 0.10 * H
        self.t = 1000.0
        self.events = []
        self.kinds = []

    def step(self, dets, dt=0.15):
        out = self.tr.update(dets, LINE, now=self.t, frame_size=FS)
        self.kinds += [r["kind"] for r in self.tr.rejects]
        self.events += [(round(self.t - 1000, 2), c["to_side"], c["via"], c["track_id"]) for c in out]
        self.t += dt
        return out

    def approach(self, target=FULL, steps=20):
        """Down the road from far away (road side, -130 px) to `target` on the line."""
        for i in range(steps, -1, -1):
            self.step([_truck(_shift(target, -5 * i, -10 * i))])

    def stand(self, seconds, boxes=(FULL, CAB), seed=1):
        """Waits at the gate: YOLO box wobbles between the two shapes in runs of 1-7 frames."""
        rnd = random.Random(seed)
        n, i = int(seconds / 0.15), 0
        while i < n:
            box = boxes[(i // 4) % len(boxes)] if len(boxes) > 1 else boxes[0]
            for _ in range(rnd.randint(1, 7)):
                self.step([_truck(box)])
                i += 1

    def drive_in(self, start=FULL, steps=20):
        """Turns into the gate: left/down, clearly onto the gate side (+186 px)."""
        for i in range(1, steps + 1):
            self.step([_truck(_shift(start, -10 * i, 5 * i))])
        return _shift(start, -10 * steps, 5 * steps)

    def drive_out(self, start, steps=40):
        for i in range(1, steps + 1):
            self.step([_truck(_shift(start, 10 * i, -5 * i))])
        return _shift(start, 10 * steps, -5 * steps)

    def gap(self, seconds, dt=0.15):
        n = max(1, int(seconds / dt))
        for _ in range(n):
            self.step([], dt=dt)


def test_geometry_matches_user_snapshots():
    s = Sim()
    assert 10 < _dist(s.tr, FULL) < 20 and -25 < _dist(s.tr, CAB) < -15   # +14 / -21: both inside the jitter band
    assert s.tr.clear_px(FULL) > 60 and s.tr.clear_px(CAB) > 60             # 'clearly past' for this truck ~66-83 px


def test_truck_arrives_counts_once_then_wobbles_on_the_line_for_a_minute():
    s = Sim()
    s.approach()                       # comes down the road and stops with its wheels on the line
    assert [e[1] for e in s.events] == [1], s.events          # exactly one Entry (gate side = +)
    s.stand(60)                        # user's case: 60 s of cab/cab+load wobble (+14 <-> -21 px)
    assert [e[1] for e in s.events] == [1], s.events          # no Exit, no second Entry
    assert "jitter" in s.kinds         # diagnostics explain why the wobble was not counted
    s.drive_in()                       # finally turns into the gate
    assert [e[1] for e in s.events] == [1], s.events
    assert len(s.tr.tracks) == 1 and s.tr.next_id == 2        # one continuous track all along


def test_truck_stops_short_of_the_line_wobbles_then_enters_counts_once():
    s = Sim()
    s.approach(target=CAB)             # stops with the bottom-centre 21 px on the road side
    assert s.events == [], s.events
    s.stand(60, boxes=(CAB, FULL))     # wobble pushes it +14 px over the line now and then -> still nothing
    assert s.events == [], s.events
    s.drive_in()
    assert [e[1] for e in s.events] == [1], s.events          # the real entry, once


def test_counted_truck_lost_standing_on_the_line_is_the_same_vehicle_for_5_minutes():
    s = Sim()
    s.approach()
    s.stand(20)
    s.gap(4)                                                   # YOLO loses it (> 20 frames and > 2.5 s)
    assert s.tr.tracks == {} and s.tr.lost[1][2] is not None   # remembered WITH its crossing state
    s.t += 240                                                 # found again 4 minutes later, same spot
    s.stand(10)
    assert "resumed" in s.kinds
    (tid, tr), = s.tr.tracks.items()
    assert tr.resumed_from == 1 and tr.crossings == 1
    s.drive_in()
    assert [e[1] for e in s.events] == [1], s.events          # it was already counted: no new Entry


def test_memory_expires_after_5_minutes():
    s = Sim()
    s.approach()
    s.gap(4)
    s.t += RESUME_S + 60                                        # 6 minutes later: a vehicle at the line is NEW
    s.stand(3)
    s.drive_in()
    assert [e[1] for e in s.events] == [1, 1], s.events


def test_second_yolo_box_on_a_standing_counted_truck_never_counts():
    s = Sim()
    s.approach()
    for _ in range(200):                                       # 30 s with BOTH boxes (cab + whole truck, IoU 0.63)
        s.step([_truck(FULL), _truck(CAB)])
    assert "resumed" in s.kinds and len(s.tr.tracks) == 2
    twin = [t for t in s.tr.tracks.values() if t.resumed_from == 1][0]
    assert twin.crossings == 1
    for i in range(1, 21):                                     # drives in with both boxes
        s.step([_truck(_shift(FULL, -10 * i, 5 * i)), _truck(_shift(CAB, -10 * i, 5 * i))])
    assert [e[1] for e in s.events] == [1], s.events


def test_real_exit_and_re_entry_still_count():
    s = Sim()
    s.approach()
    deep = s.drive_in()
    s.stand(5, boxes=(deep,))
    out = s.drive_out(deep)                                    # really drives back out to the road
    s.stand(5, boxes=(out,))
    s.drive_in(start=out, steps=60)                            # and comes back in
    assert [e[1] for e in s.events] == [1, -1, 1], s.events


def test_following_truck_behind_a_standing_counted_truck_counts():
    s = Sim()
    s.approach()
    inside = s.drive_in(steps=15)                              # truck A waits inside the gate
    s.stand(5, boxes=(inside,))
    for i in range(20, -1, -1):                                # truck B comes down the road while A stands
        s.step([_truck(inside), _truck(_shift(FULL, -5 * i, -10 * i))])
    assert [e[1] for e in s.events] == [1, 1], s.events


def test_fast_vehicle_in_one_step_and_slow_creep_after_a_stop():
    s = Sim()
    car = (560, 290, 760, 440)                                 # 200x150 car -> clear = 40 px
    s.step([{"bbox": _shift(car, 50, -100), "label": "car"}])  # far on the road side
    s.step([{"bbox": car, "label": "car"}], dt=0.5)            # one 0.5 s step lands it +14 px past the line
    assert [e[1] for e in s.events] == [1], s.events          # momentum of the run makes it 'sure' at once

    s2 = Sim()
    s2.approach(target=CAB)
    s2.stand(3, boxes=(CAB,))                                  # stopped 21 px short: momentum gone
    for i in range(1, 30):                                     # creeps in ~4 px per frame (0.15 s)
        s2.step([_truck(_shift(CAB, -3 * i, 3 * i))])
    assert [e[1] for e in s2.events] == [1], s2.events        # counted once it is clearly past (not at +4 px)
    assert s2.events[0][0] > 3.0 + 20 * 0.15                   # i.e. after the stop, not during the wobble


def test_engine_user_line_standing_truck_is_one_event_and_logs_jitter(tmp_path, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(eng.time, "time", clock)
    logs = []
    monkeypatch.setattr(eng, "clog", lambda m: logs.append(m))
    e = _engine(tmp_path, _ScriptDet(), line=LINE_N, entry_direction="pos")
    script = [[_truck(_shift(FULL, -5 * i, -10 * i))] for i in range(20, -1, -1)]
    script += [[_truck(FULL if (i // 5) % 2 == 0 else CAB)] for i in range(400)]       # 50 s wobble at 8 fps
    script += [[_truck(_shift(FULL, -10 * i, 5 * i))] for i in range(1, 21)]
    events = _drive(e, clock, script)
    assert [ev["direction"] for ev in events] == ["Entry"], [(ev["direction"], ev["id"]) for ev in events]
    assert any("jitter" in m for m in logs), logs[-5:]
    _cleanup(events)
