"""Iteration 39 (testing agent): sticky zoomed animal verdict (Track.verdict + accumulated zoom
evidence), flush_pending live-label re-check, detector.second_opinion on_object area rule."""
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import detector  # noqa: E402
import engine as eng  # noqa: E402
from test_categories import _engine  # noqa: E402

LINE = {"x1": 0.05, "y1": 0.5, "x2": 0.95, "y2": 0.5}  # y = 270 px
PERSON = {"person": 0.9, "animal": 0.0, "animal_label": ""}
DOG = {"person": 0.1, "animal": 0.7, "animal_label": "dog"}
NOTHING = {"person": 0.0, "animal": 0.0, "animal_label": ""}
MARGINAL = {"person": 0.5, "animal": 0.4, "animal_label": "dog"}


class _Zoom:
    def __init__(self, fn):
        self.fn, self.calls = fn, []

    def __call__(self, frame, bbox):
        self.calls.append(tuple(bbox))
        return self.fn(bbox, len(self.calls))


@pytest.fixture
def logs(monkeypatch):
    out = []
    monkeypatch.setattr(eng, "clog", lambda m: out.append(m))
    return out


class _Clock:
    """Persistent fake clock (8 fps) so a test can drive in phases."""

    def __init__(self, e):
        self.e, self.t, self.frame = e, eng.time.time(), np.zeros((540, 960, 3), np.uint8)
        self.real = eng.time.time

    def feed(self, dets_per_frame, fps=8.0):
        out = []
        eng.time.time = lambda: self.t
        try:
            for dets in dets_per_frame:
                self.e.detector.dets = dets
                self.t += 1.0 / fps
                out += self.e.process_frame(self.frame)[1]
        finally:
            eng.time.time = self.real
        return out

    def flush(self):
        eng.time.time = lambda: self.t
        try:
            return self.e.flush_pending(force=True)
        finally:
            eng.time.time = self.real


def _box(y, x=480, w=40, h=100, label="person", conf=0.6):
    return {"bbox": (x - w // 2, y - h, x + w // 2, y), "label": label, "conf": conf}


def _cleanup(ev):
    for x in ev:
        p = x.get("image_path", "")
        if p and os.path.exists(p):
            os.remove(p)


# ---- verdict persistence ---------------------------------------------------------------
def test_dog_once_then_nothing_lingering_in_and_out(tmp_path, logs):
    """DOG on look 1, NOTHING afterwards; crosses in, lingers ~12 s at the line, crosses out."""
    e = _engine(tmp_path, line=LINE, enable_person=True)
    z = e.detector.second_opinion = _Zoom(lambda b, n: DOG if n == 1 else NOTHING)
    c = _Clock(e)
    ev = c.feed([[_box(y)] for y in range(200, 300, 4)])
    ev += c.feed([[_box(290 + (i % 3) * 4)] for i in range(100)])  # ~12.5 s jitter just past the line
    ev += c.feed([[_box(y)] for y in range(300, 200, -4)])
    ev += c.feed([[_box(y)] for y in range(200, 310, 4)])  # crosses in again
    ev += c.flush()
    labels = [t.label for t in e.tracker2.tracks.values()]
    assert [x for x in ev if x["category"] == "person"] == [], (ev, labels)
    assert labels and all(lbl == "dog" for lbl in labels), labels
    assert len(z.calls) >= 8, len(z.calls)  # the patrol keeps looking at verdict tracks
    assert sum("zoom karke dekha" in m for m in logs) == 1, [m for m in logs if "zoom" in m]
    _cleanup(ev)


def test_marginal_then_clear_person_alerts_and_logs_correction(tmp_path, logs):
    e = _engine(tmp_path, line=LINE, enable_person=True)
    e.detector.second_opinion = _Zoom(lambda b, n: MARGINAL if n == 1 else PERSON)
    c = _Clock(e)
    ev = c.feed([[_box(y)] for y in range(200, 340, 3)]) + c.flush()
    assert [x["category"] for x in ev] == ["person"], ev
    assert any("saaf insaan" in m for m in logs), [m for m in logs if "zoom" in m]
    _cleanup(ev)


def test_dog_every_look_never_alerts_one_log_per_track(tmp_path, logs):
    e = _engine(tmp_path, line=LINE, enable_person=True)
    z = e.detector.second_opinion = _Zoom(lambda b, n: DOG)
    c = _Clock(e)
    frames = [[_box(y, x=250), _box(y, x=700)] for y in range(200, 340, 3)]
    frames += [[_box(330, x=250), _box(330, x=700)]] * 40
    frames += [[_box(y, x=250), _box(y, x=700)] for y in range(330, 200, -3)]
    ev = c.feed(frames) + c.flush()
    assert [x for x in ev if x["category"] == "person"] == [], ev
    zl = [m for m in logs if "zoom karke dekha" in m]
    assert len(zl) == 2, zl  # exactly one per track, no per-second spam
    assert len(z.calls) >= 6
    _cleanup(ev)


# ---- flush_pending live-label re-check -------------------------------------------------
def test_flush_drops_member_that_patrol_turned_dog(tmp_path, logs):
    e = _engine(tmp_path, line=LINE, enable_person=True)
    state = {"dog": False}
    e.detector.second_opinion = _Zoom(lambda b, n: DOG if state["dog"] else NOTHING)
    c = _Clock(e)
    ev = c.feed([[_box(y)] for y in range(230, 290, 4)])
    assert ev == [] and e._groups, ("member must be queued", ev, e._groups)
    state["dog"] = True
    ev += c.feed([[_box(285 + (i % 2) * 3)] for i in range(18)])  # 2.25 s hold (< PERSON_GROUP_S)
    ev += c.feed([[_box(287)] for _ in range(6)])  # quiet time over -> flush
    ev += c.flush()
    assert [x for x in ev if x["category"] == "person"] == [], ev
    assert any("zoom ne dog bataya" in m for m in logs), logs[-10:]
    _cleanup(ev)


def test_flush_after_clear_person_looks_then_one_dog_look_info(tmp_path, logs):
    """Informational: with clear PERSON looks before the crossing, a single later DOG look does
    not outweigh them (accumulation), so the member is still sent."""
    e = _engine(tmp_path, line=LINE, enable_person=True)
    state = {"dog": False}
    e.detector.second_opinion = _Zoom(lambda b, n: DOG if state["dog"] else PERSON)
    c = _Clock(e)
    ev = c.feed([[_box(y)] for y in range(200, 290, 4)])
    state["dog"] = True
    ev += c.feed([[_box(287)] for _ in range(30)]) + c.flush()
    print("events", [x["category"] for x in ev])
    _cleanup(ev)


# ---- detector.second_opinion on_object rule (synthetic model output) -------------------
class _T:
    def __init__(self, v):
        self.v = v

    def tolist(self):
        return list(self.v)

    def __float__(self):
        return float(self.v)

    def __int__(self):
        return int(self.v)


class _B:
    def __init__(self, xyxy, cls, conf):
        self.xyxy, self.cls, self.conf = [_T(xyxy)], [cls], [conf]


class _R:
    def __init__(self, boxes):
        self.boxes = boxes


COW, DOGC, PERS = 19, 16, 0
SUBJ = (480, 270, 520, 350)  # crop (380,190)-(620,430): subject in crop = (100,80,140,160), area 3200


def _det_with(boxes):
    d = object.__new__(detector.VehicleDetector)
    d.model = lambda crop, **kw: [_R([_B(*b) for b in boxes])]
    return d


def _op(boxes):
    return _det_with(boxes).second_opinion(np.zeros((540, 960, 3), np.uint8), SUBJ)


def test_big_cow_containing_centre_ignored_person_counted():
    op = _op([((40, 60, 200, 160), COW, 0.9),      # 5x area, contains centre, IoU 0.2
              ((100, 80, 140, 150), PERS, 0.6)])   # IoU 0.875
    assert op["animal"] == 0.0 and op["person"] == pytest.approx(0.6), op
    assert not eng.SecurityEngine._zoom_says_animal(op)


def test_dog_iou_half_counted():
    op = _op([((100, 106, 140, 186), DOGC, 0.55)])  # IoU ~0.51
    assert op["animal"] == pytest.approx(0.55) and op["animal_label"] == "dog", op


def test_cow_2_5x_containing_centre_counted():
    op = _op([((110, 60, 210, 140), COW, 0.5)])  # area 2.5x, IoU 0.19, contains centre
    assert op["animal"] == pytest.approx(0.5) and op["animal_label"] == "cow", op


def test_cow_3_1x_containing_centre_not_counted_and_far_box_ignored():
    op = _op([((60, 60, 184, 160), COW, 0.8),       # 124x100 = 3.875x
              ((0, 0, 30, 30), DOGC, 0.9)])         # far away
    assert op["animal"] == 0.0, op


def test_big_cow_with_iou_over_0_3_still_counts():
    op = _op([((90, 70, 170, 170), COW, 0.7)])  # 8000 area (2.5x) IoU 0.4
    op2 = _op([((80, 60, 180, 180), COW, 0.7)])  # 12000 = 3.75x, IoU 3200/12000 = 0.27 -> ignored
    assert op["animal"] == pytest.approx(0.7) and op2["animal"] == 0.0, (op, op2)
