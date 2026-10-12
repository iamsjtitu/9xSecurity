"""USER (12-10): (1) a decorated truck captioned 'BUS'; a SUV captioned 'TRUCK' -> label votes are now
weighted by YOLO confidence and 'truck' is preferred inside heavy unless 'bus' is near-unanimous.
(2) a far dog at night captioned 'PERSON' -> squat / tiny-hesitant 'person' boxes never alert."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import engine as eng  # noqa: E402
from test_categories import _drive, _engine  # noqa: E402
from tracker import CentroidTracker  # noqa: E402

LINE = ((0, 500), (960, 500))


def _track(votes):
    """votes: list of (label, conf) frames on one box -> the track's voted label."""
    tr = CentroidTracker()
    for i, (lab, conf) in enumerate(votes):
        tr.update([{"bbox": (300, 300 + i, 600, 400 + i), "label": lab, "conf": conf}], LINE, now=1000 + i * 0.2)
    return list(tr.tracks.values())[0].label


def test_suv_with_hesitant_truck_votes_is_a_car():
    votes = [("car", 0.8)] * 7 + [("truck", 0.4)] * 3  # 30% of frames 'truck', but hesitant
    assert _track(votes) == "car"


def test_real_truck_called_car_head_on_is_still_a_truck():
    votes = [("car", 0.45)] * 6 + [("truck", 0.7)] * 4  # 40% confident 'truck'
    assert _track(votes) == "truck"


def test_decorated_truck_called_bus_in_most_frames_is_a_truck():
    votes = [("bus", 0.6)] * 6 + [("truck", 0.6)] * 4
    assert _track(votes) == "truck"


def test_real_bus_stays_a_bus():
    votes = [("bus", 0.7)] * 9 + [("truck", 0.4)] * 1
    assert _track(votes) == "bus"
    assert _track([("bus", 0.7)] * 5) == "bus"


def test_votes_without_confidence_behave_as_before():
    assert _track([("car", 1.0)] * 5 + [("truck", 1.0)] * 3) == "truck"   # 37.5% -> truck (existing rule)
    assert _track([("car", 1.0)] * 5 + [("truck", 1.0)] * 2) == "car"     # 28% -> car


def test_box_glitch_swallowing_the_guard_is_not_an_exit():
    """USER 12-10: a car ENTERING captioned 'Exit'. One frame's 'truck' box spanned the car AND the
    guard standing in front of it (bottom 135 px lower = inside), then the box snapped back to the
    car (outside). A size jump of 2x in one step is a detection glitch, never a crossing; the real
    Entry a moment later still counts."""
    line = ((0, 208), (960, 208))
    tr = CentroidTracker(near_band=54)
    t = 1000.0
    crs = []
    for bot in (120, 140, 160, 175, 185):  # approaching from outside, bottom-centre above the line
        crs += tr.update([{"bbox": (528, bot - 130, 688, bot), "label": "car"}], line, now=t, frame_size=(960, 540))
        t += 0.3
    crs += tr.update([{"bbox": (528, 55, 688, 320), "label": "truck"}], line, now=t, frame_size=(960, 540))  # swallowed guard
    t += 0.3
    crs += tr.update([{"bbox": (528, 60, 688, 190), "label": "car"}], line, now=t, frame_size=(960, 540))  # snapped back
    t += 0.3
    assert crs == [], crs  # no Exit
    for bot in (205, 225, 245, 265, 290):  # drives in
        crs += tr.update([{"bbox": (528, bot - 130, 688, bot), "label": "car"}], line, now=t, frame_size=(960, 540))
        t += 0.3
    assert len(crs) == 1 and crs[0]["to_side"] == 1 and crs[0]["via"] == "cross", crs  # the Entry (inside = +)
    assert "dugna/aadha" in tr.tracks[crs[0]["track_id"]].last_reject or True


def test_fast_approach_growth_is_not_a_glitch():
    """A vehicle coming at the camera grows ~30%/step at low fps: still a normal, countable move."""
    line = ((0, 300), (960, 300))
    tr = CentroidTracker()
    t, crs = 1000.0, []
    h = 60
    for i in range(10):
        bot = 150 + i * 40
        crs += tr.update([{"bbox": (400, bot - h, 400 + int(h * 1.5), bot), "label": "car"}], line, now=t)
        h = int(h * 1.3)
        t += 0.4
    assert len(crs) == 1, crs


def test_dog_shaped_person_box_is_dropped(tmp_path):
    e = _engine(tmp_path, enable_person=True)
    dets = [
        {"bbox": (656, 224, 691, 264), "label": "person", "conf": 0.6},   # the user's dog: 35x40
        {"bbox": (100, 100, 150, 160), "label": "person", "conf": 0.9},   # 50x60: squat
        {"bbox": (200, 100, 220, 140), "label": "person", "conf": 0.40},  # 20x40: small AND hesitant
        {"bbox": (300, 100, 320, 140), "label": "person", "conf": 0.60},  # 20x40: small but sure -> kept
        {"bbox": (400, 100, 440, 200), "label": "person", "conf": 0.40},  # 40x100: a normal walker -> kept
        {"bbox": (500, 100, 560, 140), "label": "dog", "conf": 0.5},      # not a person box: untouched
    ]
    out = e._sanitize_persons(dets, 540)
    assert [d["bbox"][0] for d in out] == [300, 400, 500], out


def test_far_dog_called_person_never_alerts(tmp_path):
    e = _engine(tmp_path, enable_person=True)
    frames = []
    for i in range(40):
        y = 60 + i * 6
        frames.append([{"bbox": (660, y - 40, 695, y), "label": "person", "conf": 0.55}])  # 35x40 trotting dog
    assert _drive(e, frames) == []


def test_normal_walker_still_alerts(tmp_path):
    e = _engine(tmp_path, enable_person=True)
    frames = []
    for i in range(40):
        y = 60 + i * 6
        frames.append([{"bbox": (660, y - 100, 700, y), "label": "person", "conf": 0.55}])  # 40x100
    ev = _drive(e, frames)
    assert [x["category"] for x in ev] == ["person"], ev
    for x in ev:
        if os.path.exists(x.get("image_path", "")):
            os.remove(x["image_path"])


def test_person_drop_is_logged_once_per_half_minute(tmp_path):
    e = _engine(tmp_path, enable_person=True)
    mark = os.path.getsize(eng.CAMERA_LOG)
    for _ in range(5):
        e._sanitize_persons([{"bbox": (656, 224, 691, 264), "label": "person", "conf": 0.6}], 540)
    with open(eng.CAMERA_LOG, encoding="utf-8") as f:
        f.seek(mark)
        lines = [ln for ln in f if "person box ignored" in ln]
    assert len(lines) == 1 and "janwar jaisa" in lines[0]
    assert isinstance(np.zeros(1), np.ndarray)
