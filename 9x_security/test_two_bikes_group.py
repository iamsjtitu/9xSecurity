"""USER (11-10): 4 WhatsApp photos 'Exit — BICYCLE' within 3 s for TWO people pushing TWO bicycles
side by side (two identical photos in the same second, then each cycle again). USER (12-10): the
photo must be taken RIGHT at the crossing. So: a two-wheeler alert goes out immediately, and a box
that is the same cycle again (twin box, ID swap, re-born track) is merged — never a second photo."""
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import engine as eng  # noqa: E402
from test_categories import _drive, _engine  # noqa: E402
from tracker import TWIN_FRAMES, CentroidTracker  # noqa: E402
from whatsapp import WhatsAppNotifier  # noqa: E402

LINE = {"x1": 0.05, "y1": 0.45, "x2": 0.95, "y2": 0.45}


def _eng(tmp_path, **over):
    return _engine(tmp_path, line=LINE, enable_person=True, enable_two_wheeler=True, **over)


def _cleanup(ev):
    for x in ev:
        if os.path.exists(x.get("image_path", "")):
            os.remove(x["image_path"])


def _two_bikes(seed, p_det=0.75, jitter=4, dup=0.15):
    """2 bicycles 60 px apart + 2 walkers beside them, pushed slowly (12 px/s) over the line;
    YOLO flicker: a cycle missing in 25% of frames, an extra 'motorcycle' box on it in 15%."""
    rnd = random.Random(seed)
    out, y = [], 150.0
    for _ in range(140):
        y += 1.5
        dets = []
        for bx in (560, 620):
            jx = rnd.randint(-jitter, jitter)
            box = (bx - 16 + jx, int(y) - 70, bx + 16 + jx, int(y))
            if rnd.random() < p_det:
                dets.append({"bbox": box, "label": "bicycle"})
                if rnd.random() < dup:
                    dets.append({"bbox": (box[0] - 2, box[1] + 3, box[2] + 2, box[3] - 1), "label": "motorcycle"})
            dets.append({"bbox": (bx - 40, int(y) - 110, bx - 10, int(y) - 5), "label": "person"})
        out.append(dets)
    return out


def _bikes_at(xs, step=10, start_y=100, n=40, labels=None):
    """Motorcycles riding down at `step` px/frame (8 fps), one per x."""
    out = []
    for i in range(n):
        y = start_y + i * step
        out.append([{"bbox": (x - 35, y - 60, x + 35, y), "label": (labels or (lambda j: "motorcycle"))(i)} for x in xs])
    return out


def test_two_people_two_bicycles_is_one_photo_per_cycle(tmp_path):
    for seed in range(6):
        e = _eng(tmp_path)
        ev = _drive(e, _two_bikes(seed))
        tw = [x for x in ev if x["category"] == "two_wheeler"]
        assert len(tw) == 2 and all(x["count"] == 1 for x in tw), (seed, [(x["category"], x["count"]) for x in ev])
        xs = sorted((x["bbox"][0] + x["bbox"][2]) // 2 for x in tw)
        assert xs[1] - xs[0] > 30, ("the two photos are the two different cycles", xs)
        assert not [x for x in ev if x["category"] == "person"], "walkers beside their cycles are not lone persons"
        _cleanup(ev)


def test_twin_boxes_on_one_bicycle_is_one_alert_count_one(tmp_path):
    e = _eng(tmp_path)
    frames = []
    for f in _bikes_at([480], step=3, n=70):
        b = f[0]["bbox"]
        f[0]["label"] = "bicycle"
        frames.append(f + [{"bbox": (b[0] + 3, b[1] - 2, b[2] - 3, b[3] + 2), "label": "motorcycle"}])
    ev = _drive(e, frames)
    assert [(x["category"], x["count"]) for x in ev] == [("two_wheeler", 1)], ev


def test_same_cycle_reborn_after_crossing_is_merged(tmp_path):
    """Track lost right after the crossing; a NEW track is born on the same slow cycle just past the
    line and 'appears at the line' later -> merged with the alerted one, not a second photo."""
    e = _eng(tmp_path)
    frames = _bikes_at([480], step=2, start_y=150, n=60)  # crosses y=243 around frame 46
    for i in range(47, 52):
        frames[i] = []  # YOLO loses it for 5 frames right after the crossing
    frames += _bikes_at([480], step=2, start_y=150 + 60 * 2, n=60)
    ev = _drive(e, frames)
    assert [(x["category"], x["count"]) for x in ev] == [("two_wheeler", 1)], [(x["category"], x["count"]) for x in ev]
    _cleanup(ev)


def test_two_bikes_in_single_file_two_seconds_apart_are_two_alerts(tmp_path):
    e = _eng(tmp_path)
    lead = _bikes_at([480], step=10, n=30)
    follow = [[]] * 16 + _bikes_at([480], step=10, n=30)  # same path, 2 s behind
    frames = [a + b for a, b in zip(lead + [[]] * 16, follow)]
    ev = _drive(e, frames)
    assert [(x["category"], x["count"]) for x in ev] == [("two_wheeler", 1), ("two_wheeler", 1)], \
        [(x["category"], x["count"]) for x in ev]
    _cleanup(ev)


def test_two_wheeler_photo_is_taken_on_the_crossing_frame(tmp_path):
    """USER 12-10: 'bike door chala gaya tab Exit ka capture hua' — the event comes out of
    process_frame on the crossing frame itself, box == the bike's box in that frame."""
    e = _eng(tmp_path)
    frame = np.zeros((540, 960, 3), np.uint8)
    got = []
    for dets in _bikes_at([480], step=10, n=30):
        e.detector.dets = dets
        _, evs = e.process_frame(frame)
        for ev in evs:
            got.append((ev, dets[0]["bbox"]))
    assert len(got) == 1, got
    ev, box = got[0]
    assert ev["category"] == "two_wheeler" and tuple(ev["bbox"]) == tuple(box)
    _cleanup([ev])


def test_bikes_far_apart_in_time_are_separate_alerts(tmp_path):
    e = _eng(tmp_path)
    frames = _bikes_at([480], step=10, n=30) + [[]] * 56 + _bikes_at([480], step=10, n=30)  # 7 s gap
    ev = _drive(e, frames)
    assert [(x["category"], x["count"]) for x in ev] == [("two_wheeler", 1), ("two_wheeler", 1)]
    _cleanup(ev)


def test_steady_bike_traffic_every_bike_alerted(tmp_path):
    """A bike every 3 s on the same path: each is the NEXT bike (the previous one has moved on)."""
    e = _eng(tmp_path)
    frames = []
    for k in range(8):
        frames += _bikes_at([480], step=20, n=14) + [[]] * 10  # one crossing every 3 s
    ev = _drive(e, frames)
    assert len(ev) == 8 and all(x["count"] == 1 for x in ev), [(x["category"], x["count"]) for x in ev]
    _cleanup(ev)


def test_caption_shows_bike_count():
    n = WhatsAppNotifier({"wa_enabled": False})
    ev = {"timestamp": "2026-10-11T17:44:08", "direction": "Exit", "category": "two_wheeler", "vehicle_type": "bicycle"}
    assert "🏍️ Two-wheeler Exit — 2 bikes" in n._caption({**ev, "count": 2})
    one = n._caption({**ev, "count": 1})
    assert "🏍️ Two-wheeler Exit" in one and "bikes" not in one


def test_tracker_merge_twins_only_when_enabled():
    """A twin box born on the cycle BEFORE it crosses (so birth-inheritance cannot apply) and glued
    to it afterwards: the merge_twins tracker lets it inherit the counted state, the plain
    (car) tracker keeps it an independent, countable track."""
    line = ((0, 100), (400, 100))

    def dets(y):
        return [{"bbox": (90, y - 40, 150, y), "label": "bicycle"},
                {"bbox": (90, y - 40, 150, y - 12), "label": "motorcycle"}]  # IoU 0.7, ref 12 px behind

    ys = [40 + i * 10 for i in range(7)] + [108] * (TWIN_FRAMES + 2) + [140, 150, 160]

    def run(trk):
        crossings = []
        for i, y in enumerate(ys):
            crossings += trk.update(dets(y), line, now=1000 + i * 0.125)
        return crossings

    merged = run(CentroidTracker(merge_twins=True))
    plain = run(CentroidTracker())
    assert len(merged) == 1, merged   # twin inherited -> the cycle counts once
    assert len(plain) == 2, plain     # cars: an overlapping box is the next vehicle, still countable


def test_person_group_still_counts_single_file_walkers(tmp_path):
    """Regression: two people 1.5 s apart on the same path = '2 persons', not merged."""
    e = _eng(tmp_path)
    def walker(n, start):
        return [[]] * start + [[{"bbox": (470, 100 + i * 8 - 90, 510, 100 + i * 8), "label": "person"}] for i in range(n)]
    a, b = walker(40, 0), walker(40, 12)
    frames = [x + y for x, y in zip(a + [[]] * 12, b)]
    ev = _drive(e, frames)
    assert [(x["category"], x["count"]) for x in ev] == [("person", 2)], [(x["category"], x["count"]) for x in ev]
    _cleanup(ev)


def test_identical_twin_crossings_same_second_are_one(tmp_path):
    """The user's pair: two tracks fire on the same cycle box in the same second -> one alert."""
    e = _eng(tmp_path)
    now = 1000.0
    cr1 = {"track_id": 1, "label": "bicycle", "bbox": (575, 195, 605, 265), "to_side": 1, "via": "cross", "which": 2}
    cr2 = {"track_id": 2, "label": "bicycle", "bbox": (575, 195, 605, 265), "to_side": 1, "via": "cross", "which": 2}
    assert e._seen_again("two_wheeler", cr1, "Exit", now) == ""
    assert "same spot" in e._seen_again("two_wheeler", cr2, "Exit", now + 0.4)
    # a different cycle 60 px to the right 2 s later is NOT the same one
    cr3 = {"track_id": 3, "label": "bicycle", "bbox": (635, 190, 667, 262), "to_side": 1, "via": "cross", "which": 2}
    assert e._seen_again("two_wheeler", cr3, "Exit", now + 2.0) == ""
    # the Entry direction has its own memory
    cr4 = {"track_id": 4, "label": "bicycle", "bbox": (575, 195, 605, 265), "to_side": -1, "via": "cross", "which": 2}
    assert e._seen_again("two_wheeler", cr4, "Entry", now + 0.5) == ""
    assert eng.GROUP_RECENT_S >= 8
