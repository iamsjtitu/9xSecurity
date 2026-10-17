"""USER (17-10, 3 screenshots, very upset): stray dogs at night still sent as 'Entry — PERSON'. Seen
head-on a dog is tall and narrow (28x64, 30x80 px) so the shape test cannot catch it, and at
full-frame scale YOLO calls the small blob 'person'. Fix: a ZOOMED second look (crop around the
box, person + all animal classes) — several times while the track approaches the line and once
more right before the alert. An animal on the zoom makes the whole track an animal."""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import config  # noqa: E402
import detector  # noqa: E402
import engine as eng  # noqa: E402
from test_categories import _drive, _engine  # noqa: E402

ASSETS = os.path.join(os.path.dirname(__file__), "tests", "assets")
LINE = {"x1": 0.05, "y1": 0.5, "x2": 0.95, "y2": 0.5}


def _canvas(name, scale):
    """The generated night frame shrunk so the subject becomes a small far blob on a 960x540 frame."""
    img = cv2.resize(cv2.imread(os.path.join(ASSETS, name)), (960, 540))
    small = cv2.resize(img, None, fx=scale, fy=scale)
    canvas = np.full((540, 960, 3), 30, np.uint8)
    y0, x0 = (540 - small.shape[0]) // 2, (960 - small.shape[1]) // 2
    canvas[y0:y0 + small.shape[0], x0:x0 + small.shape[1]] = small
    return canvas, x0, y0


def _det():
    path, _ = detector.resolve_model_path("fast")
    return detector.VehicleDetector(path, conf=0.25, allowed=config.detect_classes({**config.DEFAULTS, "enable_person": True}))


# ---- real model ------------------------------------------------------------------
def test_real_yolo_far_night_dog_called_person_is_an_animal_on_zoom():
    det = _det()
    canvas, x0, y0 = _canvas("night_dog.jpg", 0.45)
    dets = det.detect(canvas)
    persons = [d for d in dets if d["label"] == "person"]
    # the failure mode itself: at full-frame scale the 31 px dog is a 'person'
    assert persons, dets
    for d in persons:
        op = det.second_opinion(canvas, d["bbox"])
        assert op["animal"] >= eng.ZOOM_ANIMAL_CONF and op["animal"] >= eng.ZOOM_ANIMAL_RATIO * op["person"], op
        assert eng.SecurityEngine._zoom_says_animal(op)


def test_real_yolo_dog_zoom_at_every_scale():
    det = _det()
    for scale in (1.0, 0.7, 0.45):
        canvas, x0, y0 = _canvas("night_dog.jpg", scale)
        box = (int(x0 + 588 * scale), int(y0 + 273 * scale), int(x0 + 626 * scale), int(y0 + 344 * scale))
        op = det.second_opinion(canvas, box)
        assert op["animal"] >= 0.3 and op["person"] < 0.3, (scale, op)


def test_real_yolo_night_person_is_confirmed_not_animal():
    det = _det()
    for scale in (1.0, 0.6):
        canvas, x0, y0 = _canvas("night_person.jpg", scale)
        dets = [d for d in det.detect(canvas) if d["label"] == "person"]
        assert dets, "the walker must still be detected"
        for d in dets:
            op = det.second_opinion(canvas, d["bbox"])
            assert op["person"] >= 0.5 and not eng.SecurityEngine._zoom_says_animal(op), (scale, op)


# ---- engine wiring (stubbed second opinion) ------------------------------------------
class _Zoom:
    def __init__(self, op):
        self.op, self.calls = op, 0

    def __call__(self, frame, bbox):
        self.calls += 1
        if isinstance(self.op, Exception):
            raise self.op
        return self.op


def _walker(n=40, x=480, step=6):
    return [[{"bbox": (x - 20, 60 + i * step - 100, x + 20, 60 + i * step), "label": "person", "conf": 0.6}] for i in range(n)]


def _cleanup(ev):
    for x in ev:
        if os.path.exists(x.get("image_path", "")):
            os.remove(x["image_path"])


def test_zoom_animal_blocks_the_person_alert_and_marks_the_track(tmp_path):
    e = _engine(tmp_path, line=LINE, enable_person=True)
    e.detector.second_opinion = _Zoom({"person": 0.0, "animal": 0.6, "animal_label": "dog"})
    assert _drive(e, _walker()) == []
    assert e.detector.second_opinion.calls >= 1
    assert all(detector.category_of(tr.label) == "animal" for tr in e.tracker2.tracks.values())


def test_zoom_person_confirmed_alerts_normally(tmp_path):
    e = _engine(tmp_path, line=LINE, enable_person=True)
    e.detector.second_opinion = _Zoom({"person": 0.85, "animal": 0.0, "animal_label": ""})
    ev = _drive(e, _walker())
    assert [x["category"] for x in ev] == ["person"], ev
    _cleanup(ev)


def test_zoom_failure_or_missing_never_blocks_a_person(tmp_path):
    e = _engine(tmp_path, line=LINE, enable_person=True)
    e.detector.second_opinion = _Zoom(RuntimeError("model busy"))
    ev = _drive(e, _walker())
    assert [x["category"] for x in ev] == ["person"], ev
    _cleanup(ev)
    e2 = _engine(tmp_path, line=LINE, enable_person=True)  # fake detector without second_opinion
    assert not hasattr(e2.detector, "second_opinion")
    ev2 = _drive(e2, _walker())
    assert [x["category"] for x in ev2] == ["person"], ev2
    _cleanup(ev2)


def test_zoom_hesitant_animal_below_threshold_does_not_block(tmp_path):
    e = _engine(tmp_path, line=LINE, enable_person=True)
    e.detector.second_opinion = _Zoom({"person": 0.7, "animal": 0.35, "animal_label": "dog"})  # 0.35 < 0.7*0.7
    ev = _drive(e, _walker())
    assert [x["category"] for x in ev] == ["person"], ev
    _cleanup(ev)


def test_zoom_patrol_looks_while_approaching_not_every_frame(tmp_path):
    e = _engine(tmp_path, line=LINE, enable_person=True)
    e.detector.second_opinion = _Zoom({"person": 0.9, "animal": 0.0, "animal_label": ""})
    frames = _walker(n=48, step=4)  # 6 s at 8 fps, in the band for ~3 s
    _cleanup(_drive(e, frames))
    calls = e.detector.second_opinion.calls
    assert 2 <= calls <= 8, calls  # ~1 look/s while near the line + the final one, never 48


def test_zoom_only_runs_for_persons(tmp_path):
    e = _engine(tmp_path, line=LINE, enable_person=True, enable_two_wheeler=True)
    e.detector.second_opinion = _Zoom({"person": 0.0, "animal": 0.9, "animal_label": "dog"})
    frames = [[{"bbox": (430, 60 + i * 10 - 60, 530, 60 + i * 10), "label": "motorcycle"}] for i in range(40)]
    ev = _drive(e, frames)
    assert [x["category"] for x in ev] == ["two_wheeler"] and e.detector.second_opinion.calls == 0, ev
    _cleanup(ev)
