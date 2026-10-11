"""USER (11-10): 'Two wheeler aur person ke liye alag detection line' — car/truck/bus keep the
MAIN (yellow) line, person + two-wheeler are judged ONLY on the person/bike line (magenta).
Every object meets exactly one line, so nothing is ever counted twice."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import config  # noqa: E402
import engine as eng  # noqa: E402
from test_categories import _drive, _engine  # noqa: E402

LINE_V = {"x1": 0.05, "y1": 0.5, "x2": 0.45, "y2": 0.5}   # left half  -> vehicles
LINE_P = {"x1": 0.55, "y1": 0.5, "x2": 0.95, "y2": 0.5}   # right half -> person / bike
XV, XP = 240, 720  # x positions that cross exactly one of the two lines


def _eng(tmp_path, **over):
    base = dict(line=LINE_V, line2=LINE_P, entry_direction2="pos", enable_person=True, enable_two_wheeler=True)
    return _engine(tmp_path, **{**base, **over})


def _down(label, x, w, h, step=6, labels=None):
    """A w x h box moving DOWN `step` px per frame (8 fps) through y=270 (both lines)."""
    out = []
    i = 0
    for y in range(60, 480, step):
        lab = labels(i) if labels else label
        out.append([{"bbox": (x - w // 2, y - h, x + w // 2, y), "label": lab}])
        i += 1
    return out


def _cleanup(ev):
    for x in ev:
        if os.path.exists(x.get("image_path", "")):
            os.remove(x["image_path"])


def _cats(ev):
    return [(x["category"], x["direction"]) for x in ev]


# ---- which line counts what -------------------------------------------------
def test_car_counts_on_main_line_only(tmp_path):
    e = _eng(tmp_path)
    ev = _drive(e, _down("car", XV, 120, 80))
    assert _cats(ev) == [("vehicle", "Entry")], ev
    _cleanup(ev)
    e2 = _eng(tmp_path)
    assert _drive(e2, _down("car", XP, 120, 80)) == []  # drove over the person/bike line: not a vehicle crossing


def test_person_counts_on_person_line_only(tmp_path):
    e = _eng(tmp_path)
    ev = _drive(e, _down("person", XP, 40, 100))
    assert _cats(ev) == [("person", "Entry")], ev
    assert ev[0]["count"] == 1
    _cleanup(ev)
    e2 = _eng(tmp_path)
    assert _drive(e2, _down("person", XV, 40, 100)) == []  # walked over the vehicle line: ignored


def test_two_wheeler_counts_on_person_line_only(tmp_path):
    e = _eng(tmp_path)
    ev = _drive(e, _down("motorcycle", XP, 70, 60))
    assert _cats(ev) == [("two_wheeler", "Entry")], ev
    _cleanup(ev)
    e2 = _eng(tmp_path)
    assert _drive(e2, _down("motorcycle", XV, 70, 60)) == []


def test_without_line2_person_and_bike_use_the_main_line(tmp_path):
    """Backward compatible: no person/bike line drawn -> everything on the yellow line (as before)."""
    e = _engine(tmp_path, line=LINE_V, line2=None, enable_person=True, enable_two_wheeler=True)
    assert not e.has_line2()
    ev = _drive(e, _down("person", XV, 40, 100))
    assert _cats(ev) == [("person", "Entry")], ev
    _cleanup(ev)
    e2 = _engine(tmp_path, line=LINE_V, line2=None, enable_person=True, enable_two_wheeler=True)
    ev2 = _drive(e2, _down("motorcycle", XV, 70, 60))
    assert _cats(ev2) == [("two_wheeler", "Entry")], ev2
    _cleanup(ev2)


def test_each_object_counted_once_both_lines_busy(tmp_path):
    """A car on the vehicle line and a walker on the person line at the same time: 1 + 1, never more."""
    e = _eng(tmp_path)
    car, man = _down("car", XV, 120, 80), _down("person", XP, 40, 100)
    ev = _drive(e, [c + m for c, m in zip(car, man)])
    assert sorted(x["category"] for x in ev) == ["person", "vehicle"], ev
    _cleanup(ev)


# ---- Entry/Exit direction of the second line ---------------------------------
def test_entry_direction2_inferred_from_main_line():
    base = {"line": LINE_V, "entry_direction": "pos"}
    same_way = {**base, "line2": LINE_P}
    assert config.infer_entry_direction2(same_way) == "pos"
    opposite = {**base, "line2": {"x1": 0.95, "y1": 0.5, "x2": 0.55, "y2": 0.5}}  # drawn right -> left
    assert config.infer_entry_direction2(opposite) == "neg"
    assert config.infer_entry_direction2({**opposite, "entry_direction": "neg"}) == "pos"
    assert config.entry_direction2({"entry_direction": "neg", "entry_direction2": ""}) == "neg"
    assert config.entry_direction2({"entry_direction": "neg", "entry_direction2": "pos"}) == "pos"


def test_person_line_drawn_backwards_still_gives_same_entry_as_car(tmp_path):
    """Line2 drawn right->left: the inferred sign keeps 'walking in' = Entry like 'driving in'."""
    l2 = {"x1": 0.95, "y1": 0.5, "x2": 0.55, "y2": 0.5}
    cfg = {"line": LINE_V, "entry_direction": "pos", "line2": l2}
    e = _engine(tmp_path, line=LINE_V, line2=l2, entry_direction2=config.infer_entry_direction2(cfg),
                enable_person=True, enable_two_wheeler=True)
    ev = _drive(e, _down("person", XP, 40, 100) + _down("car", XV, 120, 80))
    assert sorted(_cats(ev)) == [("person", "Entry"), ("vehicle", "Entry")], ev
    _cleanup(ev)


def test_swap2_only_flips_the_person_line(tmp_path):
    e = _eng(tmp_path, entry_direction2="neg")
    ev = _drive(e, _down("person", XP, 40, 100) + _down("car", XV, 120, 80))
    assert sorted(_cats(ev)) == [("person", "Exit"), ("vehicle", "Entry")], ev
    _cleanup(ev)


# ---- never double: label flips, riders, drivers --------------------------------
def test_label_flip_car_motorcycle_is_one_alert_when_lines_coincide(tmp_path):
    """YOLO calls an auto 'car' in 2 of 3 frames and 'motorcycle' in the 3rd -> both trackers follow
    it (one per line). Lines on the same spot: exactly ONE alert, never car + two-wheeler."""
    e = _engine(tmp_path, line=LINE_V, line2=LINE_V, entry_direction2="pos",
                enable_person=False, enable_two_wheeler=True)
    ev = _drive(e, _down("car", XV, 110, 80, step=4, labels=lambda i: "motorcycle" if i % 3 == 2 else "car"))
    assert len(ev) == 1, _cats(ev)
    _cleanup(ev)


def test_rider_on_bike_crossing_person_line_is_one_two_wheeler(tmp_path):
    e = _eng(tmp_path)
    frames = []
    for f in _down("motorcycle", XP, 90, 70, step=20):
        b = f[0]["bbox"]
        rider = {"bbox": (b[0] + 20, b[1] - 60, b[0] + 70, b[3] - 10), "label": "person"}
        frames.append([f[0], rider])
    ev = _drive(e, frames)
    assert _cats(ev) == [("two_wheeler", "Entry")], _cats(ev)
    _cleanup(ev)


def test_driver_box_on_moving_car_is_not_a_person_across_trackers(tmp_path):
    """Lines on the same spot: the car lives in the vehicle tracker, the driver's 'person' box in the
    person tracker — the rider test must still see the moving car under him."""
    e = _engine(tmp_path, line=LINE_V, line2=LINE_V, entry_direction2="pos",
                enable_person=True, enable_two_wheeler=True)
    frames = []
    for f in _down("car", XV, 160, 90, step=8):
        b = f[0]["bbox"]
        driver = {"bbox": (b[0] + 90, b[1] - 20, b[0] + 125, b[1] + 50), "label": "person"}
        frames.append([f[0], driver])
    ev = _drive(e, frames)
    assert _cats(ev) == [("vehicle", "Entry")], _cats(ev)
    _cleanup(ev)


# ---- config / drawing helpers -------------------------------------------------
def test_line2_is_a_per_camera_setting():
    assert "line2" in config.CAMERA_KEYS and "entry_direction2" in config.CAMERA_KEYS
    cfg = {**config.DEFAULTS, "cameras": [
        {"id": "cam1", "name": "A", "gate": "Main", "rtsp_url": "rtsp://a", "line": LINE_V, "line2": LINE_P,
         "entry_direction2": "neg"},
        {"id": "cam2", "name": "B", "gate": "Back", "rtsp_url": "rtsp://b", "line": LINE_V},
    ], "active_camera_id": "cam2"}  # top-level values = the ACTIVE camera (cam2: no line2)
    config.sync_cameras(cfg)
    c1 = config.camera_cfg(cfg, cfg["cameras"][0])
    c2 = config.camera_cfg(cfg, cfg["cameras"][1])
    assert c1["line2"] == LINE_P and c1["entry_direction2"] == "neg"
    assert c2["line2"] is None and c2["entry_direction2"] == ""


def test_line2_px_and_draw():
    assert eng.line2_px({"line2": None}, 960, 540) is None
    assert eng.line2_px({}, 960, 540) is None
    a, b = eng.line2_px({"line2": LINE_P}, 960, 540)
    assert a == (528, 270) and b == (912, 270)
    img = np.zeros((540, 960, 3), np.uint8)
    eng.draw_line2(img, a, b)
    assert tuple(img[270, 700]) == eng.LINE2_COLOR  # magenta, not the yellow of the main line


def test_annotated_frame_shows_both_lines(tmp_path):
    e = _eng(tmp_path)
    e.detector.dets = []
    frame = np.zeros((540, 960, 3), np.uint8)
    out, _ = e.process_frame(frame)
    assert tuple(out[270, 240]) == (0, 255, 255)   # main line yellow (left half)
    assert tuple(out[270, 700]) == eng.LINE2_COLOR  # person/bike line magenta (right half)
