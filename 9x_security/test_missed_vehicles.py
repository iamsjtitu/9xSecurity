"""USER (2% missed vehicles: fast vehicles/bikes, two vehicles together) — slow-PC-aware tracker +
dedupe fixes + 'why was it not counted' diagnostics."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import engine as eng  # noqa: E402
from tracker import CentroidTracker  # noqa: E402
from test_tracker_false_crossings import _Clock, _ScriptDet, _cleanup, _cross_frames, _drive, _engine  # noqa: E402

LINE = ((0, 380), (960, 380))
FS = (960, 540)


def _box(cx, bottom, w=60, h=100):
    return {"bbox": (cx - w // 2, bottom - h, cx + w // 2, bottom), "label": "motorcycle"}


def test_fast_bike_at_2_detections_per_second_is_counted():
    """Slow PC: a bike moves 150 px (1.5 box sizes) between detections 0.5 s apart.
    Old rule (90 px match radius, 0.8-size teleport guard) made a NEW track every detection."""
    tr = CentroidTracker(min_gap_s=3.0)
    t, out = 1000.0, []
    for bottom in range(100, 640, 150):           # 100, 250, 400, 550 — the line (380) lies between 250 and 400
        out += tr.update([_box(480, bottom)], LINE, now=t, frame_size=FS)
        t += 0.5
    assert len(out) == 1 and out[0]["via"] == "cross"
    assert len(tr.tracks) == 1 and tr.next_id == 2  # one continuous track


def test_vehicle_lost_for_a_second_then_reappearing_past_the_line_is_counted():
    """Gate pillar hides the car for ~1 s; it re-appears on the other side further along its path."""
    tr = CentroidTracker(min_gap_s=3.0)
    t, out = 1000.0, []
    for bottom in (200, 230, 260, 290):           # approaching at 30 px / 0.15 s = 200 px/s
        out += tr.update([_box(480, bottom, w=120, h=150)], LINE, now=t, frame_size=FS)
        t += 0.15
    for _ in range(6):                             # hidden: no detections for 0.9 s
        out += tr.update([], LINE, now=t, frame_size=FS)
        t += 0.15
    out += tr.update([_box(480, 500, w=120, h=150)], LINE, now=t, frame_size=FS)  # 210 px further, past the line
    assert len(out) == 1 and out[0]["track_id"] == 1


def test_stale_track_never_grabs_a_new_vehicle_appearing_elsewhere():
    """A parked/vanished truck's track must not jump to a new vehicle entering at the top of the picture."""
    tr = CentroidTracker(min_gap_s=3.0)
    t = 1000.0
    for _ in range(10):                            # truck stands still below the line, then vanishes
        tr.update([_box(480, 460, w=120, h=150)], LINE, now=t, frame_size=FS)
        t += 0.15
    for _ in range(8):
        tr.update([], LINE, now=t, frame_size=FS)
        t += 0.15
    out = tr.update([_box(480, 150, w=120, h=150)], LINE, now=t, frame_size=FS)  # new vehicle far above the line
    assert out == [] and len(tr.tracks) == 2


def test_rejects_explain_uncounted_flips_and_possible_miss():
    tr = CentroidTracker(min_gap_s=3.0)
    short_line = ((800, 380), (960, 380))          # vehicle crosses the infinite line but NOT the drawn segment
    t, rejects = 1000.0, []
    for bottom in (300, 340, 420, 460):
        tr.update([_box(300, bottom, w=120, h=150)], short_line, now=t, frame_size=FS)
        rejects += tr.rejects
        t += 0.15
    assert rejects and "segment ke bahar" in rejects[0]["reason"] and rejects[0]["kind"] == "reject"
    for _ in range(25):                            # track ages out on the far side without a count
        tr.update([], short_line, now=t, frame_size=FS)
        rejects += tr.rejects
        t += 0.15
    miss = [r for r in rejects if r["kind"] == "possible-miss"]
    assert len(miss) == 1 and "segment ke bahar" in miss[0]["reason"]


def test_queued_cars_in_front_view_both_count_but_trolley_box_does_not(tmp_path, monkeypatch):
    """Front-view camera: the car behind overlaps the first car's box in the picture and crosses
    4 s later -> TWO events. A tractor's trolley box crossing 1 s after the cab -> ONE event."""
    clock = _Clock()
    monkeypatch.setattr(eng.time, "time", clock)
    e = _engine(tmp_path, _ScriptDet())
    car_a = _cross_frames(60, 250)                                   # crosses, keeps driving in
    frames = [[d] for d in car_a]
    # A stops just inside (box still overlapping the gate spot), B arrives 4 s later and crosses
    a_stop = {"bbox": (720, 190, 840, 270), "label": "car"}
    frames += [[a_stop]] * 32                                        # 4 s at 8 fps
    for d in _cross_frames(60, 250):
        frames.append([a_stop, d])
    events = _drive(e, clock, frames)
    assert len(events) == 2, [(ev["direction"], ev["id"]) for ev in events]
    _cleanup(events)

    e2 = _engine(tmp_path, _ScriptDet())
    frames2 = []
    for i, cab in enumerate(_cross_frames(60, 250)):
        trolley = {"bbox": (720, cab["bbox"][1] - 60, 840, cab["bbox"][3] - 30), "label": "truck"}  # YOLO's 2nd box on the same body
        frames2.append([cab, trolley])
    for i in range(16):                                              # both keep moving together
        y = 250 + i * 6
        frames2.append([{"bbox": (720, y - 40, 840, y + 40), "label": "truck"},
                        {"bbox": (720, y - 100, 840, y + 10), "label": "truck"}])
    events2 = _drive(e2, clock, frames2)
    assert len(events2) == 1, [(ev["direction"], ev["id"]) for ev in events2]
    _cleanup(events2)


def test_following_car_after_first_track_was_lost_at_the_line_counts(tmp_path, monkeypatch):
    """A crossed and its track aged out right at the gate; B is tracked from the other side and
    crosses the same spot 6 s later ('cross', not 'appeared-at-line') -> B must count."""
    clock = _Clock()
    monkeypatch.setattr(eng.time, "time", clock)
    e = _engine(tmp_path, _ScriptDet())
    frames = [[d] for d in _cross_frames(60, 160)]                   # A crosses and vanishes right there
    frames += [[]] * 48                                               # 6 s: A's track ages out (lost at the line)
    frames += [[d] for d in _cross_frames(60, 250)]                   # B tracked from above, crosses the same spot
    events = _drive(e, clock, frames)
    assert len(events) == 2, [(ev["direction"], ev["id"]) for ev in events]
    _cleanup(events)
