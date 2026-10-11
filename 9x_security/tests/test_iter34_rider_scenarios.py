"""Iter 34 — extra tracker/engine scenarios for the rider/person 'seated + fast' rule and
tracker two-wheeler-vote relabel. Uses _engine/_drive from test_categories.py (fake 8-fps clock)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))
# ensure test_categories helpers importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from test_categories import _engine, _drive  # noqa: E402


def _frames_person(x0, y0, w, h, dy_per_frame, n=30):
    """Scripted 'person' detections moving vertically through the test line (y ~ 130)."""
    frames = []
    for i in range(n):
        y = y0 + i * dy_per_frame
        frames.append([{"bbox": (x0, y, x0 + w, y + h), "label": "person"}])
    return frames


# (1) seated 60x95 'person' box moving 20 px/frame across the line -> NO event (rider, suppressed)
def test_seated_fast_person_box_is_rider_no_event(tmp_path):
    e = _engine(tmp_path, enable_person=True, enable_two_wheeler=False)
    # 60 wide, 95 tall  -> h/w ~ 1.58 < 1.8 -> seated.  20 px/frame @ 8 fps = 160 px/s -> fast (>1.5 h).
    frames = _frames_person(x0=740, y0=20, w=60, h=95, dy_per_frame=20, n=20)
    ev = _drive(e, frames)
    assert ev == [], ev


# (2) same seated box at 6 px/frame -> walker (slow) -> person event
def test_seated_slow_person_counts_as_walker(tmp_path):
    e = _engine(tmp_path, enable_person=True)
    frames = _frames_person(x0=740, y0=20, w=60, h=95, dy_per_frame=6, n=40)
    ev = _drive(e, frames)
    assert len(ev) == 1 and ev[0]["category"] == "person", ev
    for x in ev:
        if os.path.exists(x["image_path"]):
            os.remove(x["image_path"])


# (3) tall 40x100 box moving fast -> running tall person -> person event
def test_tall_fast_person_counts_as_running_walker(tmp_path):
    e = _engine(tmp_path, enable_person=True)
    # 40 wide, 100 tall -> h/w = 2.5 > 1.8 (standing) -> NOT rider, even if fast.
    frames = _frames_person(x0=760, y0=20, w=40, h=100, dy_per_frame=24, n=16)
    ev = _drive(e, frames)
    assert len(ev) == 1 and ev[0]["category"] == "person", ev
    for x in ev:
        if os.path.exists(x["image_path"]):
            os.remove(x["image_path"])


# (4) tracker vote: person 70% + motorcycle 30% -> label becomes 'motorcycle' (two-wheeler)
def test_tracker_vote_relabels_two_wheeler(tmp_path):
    # Two-wheeler ON -> expect a two-wheeler event (relabel kicks in).
    e = _engine(tmp_path, enable_person=True, enable_two_wheeler=True)
    frames = []
    # Fast rider shape: 60x90 box crossing the line. ~30% frames motorcycle, 70% person.
    for i in range(20):
        y = 20 + i * 18
        label = "motorcycle" if (i % 10) < 3 else "person"
        frames.append([{"bbox": (740, y, 800, y + 90), "label": label}])
    ev = _drive(e, frames)
    assert len(ev) == 1 and ev[0]["category"] == "two_wheeler", ev
    for x in ev:
        if os.path.exists(x["image_path"]):
            os.remove(x["image_path"])


# (4b) same scenario, two-wheeler OFF + person ON -> rider suppression -> no event
def test_tracker_vote_two_wheeler_off_no_event(tmp_path):
    e = _engine(tmp_path, enable_person=True, enable_two_wheeler=False)
    frames = []
    for i in range(20):
        y = 20 + i * 18
        label = "motorcycle" if (i % 10) < 3 else "person"
        frames.append([{"bbox": (740, y, 800, y + 90), "label": label}])
    ev = _drive(e, frames)
    assert ev == [], ev


# (5) existing group behaviour: two tall walkers crossing together -> ONE person event, count 2
def test_two_tall_walkers_one_event_count_two(tmp_path):
    e = _engine(tmp_path, enable_person=True)
    frames = []
    for i in range(40):
        y = 20 + i * 6
        frames.append([
            {"bbox": (700, y, 740, y + 100), "label": "person"},
            {"bbox": (820, y, 860, y + 100), "label": "person"},
        ])
    ev = _drive(e, frames)
    assert len(ev) == 1 and ev[0]["category"] == "person" and ev[0]["count"] == 2, ev
    for x in ev:
        if os.path.exists(x["image_path"]):
            os.remove(x["image_path"])


# (6) person schedule outside window -> no person event
def test_person_schedule_outside_window_blocks(tmp_path):
    from datetime import datetime
    now = datetime.now()
    far_start = f"{(now.hour + 2) % 24:02d}:00"
    far_end = f"{(now.hour + 3) % 24:02d}:00"
    e = _engine(
        tmp_path,
        enable_person=True,
        cat_schedules={**config.DEFAULTS["cat_schedules"],
                       "person": {"enabled": True, "start": far_start, "end": far_end}},
    )
    frames = _frames_person(x0=760, y0=20, w=40, h=100, dy_per_frame=6, n=40)
    ev = _drive(e, frames)
    assert ev == [], ev
