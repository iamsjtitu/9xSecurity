"""Iteration 40: the five dogs the user received as PERSON on 17-10 (WhatsApp frames, 960x540, dogs
17-52 px wide). The fast model's zoomed look saw NOTHING on them (0.0 / 0.0) and the engine let the
alert through. Now (a) zoomed looks run on the shared yolov8s whatever the camera's main model and
(b) a person alert fails closed when the zoom never saw a person either."""
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import config  # noqa: E402
import cv2  # noqa: E402
import detector  # noqa: E402
import engine as eng  # noqa: E402
from test_categories import _engine  # noqa: E402
from test_iter39_zoom_verdict import _Clock, _Zoom, _box, _cleanup  # noqa: E402

LINE = {"x1": 0.05, "y1": 0.5, "x2": 0.95, "y2": 0.5}
ASSETS = os.path.join(HERE, "assets")
# user_dog_N.jpg = 400x400 cut of the frame around the dog; box = the PERSON box the app drew
USER_DOGS = {1: (192, 178, 209, 222), 2: (188, 177, 212, 224), 3: (187, 161, 213, 239),
             4: (189, 171, 211, 230), 5: (174, 170, 226, 231)}
NOTHING = {"person": 0.0, "animal": 0.0, "animal_label": ""}


# ---- real model ------------------------------------------------------------------------
@pytest.fixture(scope="module", params=["fast", "accurate"])
def det(request):
    path, _ = detector.resolve_model_path(request.param)
    return detector.VehicleDetector(path, conf=0.25, allowed=config.detect_classes({**config.DEFAULTS, "enable_person": True}))


@pytest.mark.parametrize("n", sorted(USER_DOGS))
def test_user_dogs_are_animals_on_zoom(det, n):
    frame = cv2.imread(os.path.join(ASSETS, f"user_dog_{n}.jpg"))
    op = det.second_opinion(frame, USER_DOGS[n])
    assert op["model"] == "yolov8s.pt", op  # the sharp model, even for a fast-model camera
    assert op["animal_label"] == "dog" and eng.SecurityEngine._zoom_says_animal(op), (n, op)


def test_zoom_model_shared_once_for_fast_detector():
    path, _ = detector.resolve_model_path("fast")
    a = detector.VehicleDetector(path, conf=0.25, allowed=["person"])
    b = detector.VehicleDetector(path, conf=0.25, allowed=["person"])
    ma, name_a, lock_a = detector.zoom_model_for(a.model, a.model_name)
    mb, name_b, lock_b = detector.zoom_model_for(b.model, b.model_name)
    assert ma is mb and name_a == name_b == "yolov8s.pt" and lock_a is lock_b is not None
    s_path, _ = detector.resolve_model_path("accurate")
    s = detector.VehicleDetector(s_path, conf=0.25, allowed=["person"])
    ms, name_s, lock_s = detector.zoom_model_for(s.model, s.model_name)
    assert ms is s.model and lock_s is None  # an accurate camera zooms with its own model


def test_walker_still_clear_person(det):
    img = cv2.resize(cv2.imread(os.path.join(ASSETS, "night_person.jpg")), (960, 540))
    for scale in (1.0, 0.6):
        small = cv2.resize(img, None, fx=scale, fy=scale)
        canvas = np.full((540, 960, 3), 30, np.uint8)
        y0, x0 = (540 - small.shape[0]) // 2, (960 - small.shape[1]) // 2
        canvas[y0:y0 + small.shape[0], x0:x0 + small.shape[1]] = small
        persons = [d for d in det.detect(canvas) if d["label"] == "person"]
        assert persons
        for d in persons:
            op = det.second_opinion(canvas, d["bbox"])
            assert op["person"] >= 0.5 and not eng.SecurityEngine._zoom_says_animal(op), (scale, op)
            assert op["looks"] == 1  # a clear person needs just the cheap look


# ---- engine: fail closed when the zoom saw nothing ------------------------------------
@pytest.fixture
def logs(monkeypatch):
    out = []
    monkeypatch.setattr(eng, "clog", lambda m: out.append(m))
    return out


def test_hesitant_person_never_seen_on_zoom_is_no_alert(tmp_path, logs):
    e = _engine(tmp_path, line=LINE, enable_person=True)
    z = e.detector.second_opinion = _Zoom(lambda b, n: dict(NOTHING))
    c = _Clock(e)
    ev = c.feed([[_box(y, conf=0.5)] for y in range(200, 340, 3)]) + c.flush()
    assert [x for x in ev if x["category"] == "person"] == [], ev
    assert z.calls
    assert any("insaan kahin nahi dikha" in m for m in logs), [m for m in logs if "zoom" in m]
    _cleanup(ev)


def test_confident_full_frame_person_passes_even_if_zoom_blind(tmp_path, logs):
    e = _engine(tmp_path, line=LINE, enable_person=True)
    e.detector.second_opinion = _Zoom(lambda b, n: dict(NOTHING))
    c = _Clock(e)
    ev = c.feed([[_box(y, conf=0.85)] for y in range(200, 340, 3)]) + c.flush()
    assert [x["category"] for x in ev] == ["person"], ev
    _cleanup(ev)


def test_zoom_confirmed_person_alerts_at_low_frame_conf(tmp_path, logs):
    e = _engine(tmp_path, line=LINE, enable_person=True)
    e.detector.second_opinion = _Zoom(lambda b, n: {"person": 0.8, "animal": 0.0, "animal_label": ""})
    c = _Clock(e)
    ev = c.feed([[_box(y, conf=0.5)] for y in range(200, 340, 3)]) + c.flush()
    assert [x["category"] for x in ev] == ["person"], ev
    _cleanup(ev)


def test_zoom_returning_none_stays_fail_open(tmp_path, logs):
    e = _engine(tmp_path, line=LINE, enable_person=True)
    e.detector.second_opinion = _Zoom(lambda b, n: None)
    c = _Clock(e)
    ev = c.feed([[_box(y, conf=0.5)] for y in range(200, 340, 3)]) + c.flush()
    assert [x["category"] for x in ev] == ["person"], ev
    _cleanup(ev)


def test_zoom_model_logged_once(tmp_path, logs):
    e = _engine(tmp_path, line=LINE, enable_person=True)
    e.detector.second_opinion = _Zoom(lambda b, n: {"person": 0.9, "animal": 0.0, "animal_label": "", "model": "yolov8s.pt"})
    c = _Clock(e)
    ev = c.feed([[_box(y, conf=0.5)] for y in range(200, 340, 3)]) + c.flush()
    assert sum("zoom (doosri nazar) model: yolov8s.pt" in m for m in logs) == 1, logs
    _cleanup(ev)
