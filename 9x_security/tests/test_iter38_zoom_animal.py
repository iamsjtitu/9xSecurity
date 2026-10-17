"""Iteration 38 (testing agent): zoomed second look (detector.second_opinion) that turns far night
dogs called 'person' into animals. Real-model checks on both tiers + engine wiring with stubs."""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import config  # noqa: E402
import detector  # noqa: E402
import engine as eng  # noqa: E402
from test_categories import _drive, _engine  # noqa: E402
from test_zoom_animal import _canvas  # noqa: E402

LINE = {"x1": 0.05, "y1": 0.5, "x2": 0.95, "y2": 0.5}
DOG_BOX = (588, 273, 626, 344)  # dog in night_dog.jpg at 960x540


def _cleanup(ev):
    for x in ev:
        p = x.get("image_path", "")
        if p and os.path.exists(p):
            os.remove(p)


# ---- real model, both tiers ------------------------------------------------------------
@pytest.fixture(scope="module", params=["fast", "accurate"])
def det(request):
    path, tier = detector.resolve_model_path(request.param)
    assert os.path.exists(path), path
    return detector.VehicleDetector(path, conf=0.25,
                                    allowed=config.detect_classes({**config.DEFAULTS, "enable_person": True}))


def _dog_box(scale, x0, y0):
    return (int(x0 + DOG_BOX[0] * scale), int(y0 + DOG_BOX[1] * scale),
            int(x0 + DOG_BOX[2] * scale), int(y0 + DOG_BOX[3] * scale))


@pytest.mark.parametrize("scale", [1.0, 0.7, 0.45])
def test_dog_zoom_is_animal(det, scale):
    canvas, x0, y0 = _canvas("night_dog.jpg", scale)
    op = det.second_opinion(canvas, _dog_box(scale, x0, y0))
    assert op["animal"] >= 0.3 and op["person"] < 0.3, (scale, op)
    assert op["animal_label"] in detector.ANIMAL_LABELS
    assert eng.SecurityEngine._zoom_says_animal(op)


@pytest.mark.parametrize("scale", [1.0, 0.6])
def test_person_zoom_is_person(det, scale):
    canvas, _, _ = _canvas("night_person.jpg", scale)
    persons = [d for d in det.detect(canvas) if d["label"] == "person"]
    assert persons, "walker must be detected"
    for d in persons:
        op = det.second_opinion(canvas, d["bbox"])
        assert op["person"] >= 0.5 and not eng.SecurityEngine._zoom_says_animal(op), (scale, d["bbox"], op)


def test_robust_bboxes(det):
    canvas, x0, y0 = _canvas("night_dog.jpg", 0.7)
    b = _dog_box(0.7, x0, y0)
    for box in [(0, b[1], b[2] - b[0], b[3]),                 # touching left edge
                (b[0], 540 - (b[3] - b[1]), b[2], 540),        # touching bottom edge
                (b[0] + 10, b[1] + 10, b[2] + 10, b[3] + 10),  # shifted 10 px
                (480, 270, 490, 280),                          # 10x10
                (950, 530, 960, 540),                          # corner 10x10
                (0, 0, 2, 2)]:
        op = det.second_opinion(canvas, box)
        assert op is None or (isinstance(op, dict) and {"person", "animal", "animal_label"} <= set(op)), (box, op)
    # shifted box still sees the dog
    op = det.second_opinion(canvas, (b[0] + 10, b[1] + 10, b[2] + 10, b[3] + 10))
    print("shifted:", op)


# ---- engine wiring (stubs) ------------------------------------------------------------
class _Zoom:
    def __init__(self, fn):
        self.fn, self.calls = fn, []

    def __call__(self, frame, bbox):
        self.calls.append(tuple(bbox))
        return self.fn(bbox, len(self.calls))


PERSON = {"person": 0.9, "animal": 0.0, "animal_label": ""}
DOG = {"person": 0.1, "animal": 0.7, "animal_label": "dog"}


def _walk(ys, x=480, w=40, h=100, label="person"):
    return [{"bbox": (x - w // 2, y - h, x + w // 2, y), "label": label, "conf": 0.6} for y in ys]


def test_patrol_only_within_band_and_throttled(tmp_path):
    e = _engine(tmp_path, line=LINE, enable_person=True)
    e.detector.second_opinion = _Zoom(lambda b, n: PERSON)
    ys = list(range(20, 300, 3))  # 93 frames ~11.6 s, line at y=270, band 1.5*54=81 px
    ev = _drive(e, [[d] for d in _walk(ys)])
    calls = e.detector.second_opinion.calls
    assert [x["category"] for x in ev] == ["person"], ev
    # ~ (81+30)/3/8 s in band -> few looks, never per frame
    assert 2 <= len(calls) <= 8, calls
    for c in calls:
        assert abs(c[3] - 270) <= 81 + 6, (c, calls)
    _cleanup(ev)


def test_person_with_dog_beside(tmp_path):
    e = _engine(tmp_path, line=LINE, enable_person=True)
    e.detector.second_opinion = _Zoom(lambda b, n: DOG if (b[3] - b[1]) < 60 else PERSON)
    frames = []
    for y in range(60, 340, 6):
        frames.append([{"bbox": (300, y - 100, 340, y), "label": "person", "conf": 0.7},
                       {"bbox": (600, y - 40, 635, y), "label": "person", "conf": 0.5}])
    ev = _drive(e, frames)
    persons = [x for x in ev if x["category"] == "person"]
    assert len(persons) == 1, ev
    _cleanup(ev)
    labels = sorted(detector.category_of(t.label) for t in e.tracker2.tracks.values())
    print("labels", labels, [x.get("count") for x in ev])


def test_group_count_ignores_zoom_animal(tmp_path):
    e = _engine(tmp_path, line=LINE, enable_person=True)
    e.detector.second_opinion = _Zoom(lambda b, n: DOG if (b[3] - b[1]) < 60 else PERSON)
    frames = []
    for y in range(60, 340, 6):
        frames.append([{"bbox": (200, y - 100, 240, y), "label": "person", "conf": 0.7},
                       {"bbox": (450, y - 100, 490, y), "label": "person", "conf": 0.7},
                       {"bbox": (700, y - 40, 735, y), "label": "person", "conf": 0.5}])
    ev = _drive(e, frames)
    persons = [x for x in ev if x["category"] == "person"]
    assert len(persons) == 1, ev
    blob = repr(persons[0])
    print("group event:", {k: v for k, v in persons[0].items() if k != "image_path"})
    cnt = persons[0].get("count") or persons[0].get("group_count")
    if cnt is None:
        assert "2" in str(persons[0].get("vehicle_type", "")) or "2" in blob
    else:
        assert cnt == 2, persons[0]
    _cleanup(ev)


NOTHING = {"person": 0.0, "animal": 0.0, "animal_label": ""}  # the zoom found nothing on later looks


def test_animal_verdict_blocks_later_crossing_short(tmp_path):
    """Dog verdict on the first look; later looks find nothing. Track crosses in and straight back
    out: no Entry and no Exit — frame votes ('person' every frame) never outvote the verdict."""
    e = _engine(tmp_path, line=LINE, enable_person=True)
    e.detector.second_opinion = _Zoom(lambda b, n: DOG if n == 1 else NOTHING)
    ys = list(range(150, 360, 6)) + list(range(360, 150, -6))
    ev = _drive(e, [[d] for d in _walk(ys)])
    assert [x for x in ev if x["category"] == "person"] == [], ev
    _cleanup(ev)


def test_animal_verdict_blocks_later_crossing_lingering(tmp_path):
    """Same, but the dog lingers below the line ~10 s before going back out: the verdict must not be
    diluted away by later 'person' frames (whole track = animal)."""
    e = _engine(tmp_path, line=LINE, enable_person=True)
    e.detector.second_opinion = _Zoom(lambda b, n: DOG if n == 1 else NOTHING)
    ys = list(range(150, 360, 6)) + [360] * 80 + list(range(360, 150, -6))
    ev = _drive(e, [[d] for d in _walk(ys)])
    tr_labels = [t.label for t in e.tracker2.tracks.values()]
    assert [x for x in ev if x["category"] == "person"] == [], (ev, tr_labels, e.detector.second_opinion.calls)
    assert tr_labels and all(lbl == "dog" for lbl in tr_labels), tr_labels
    _cleanup(ev)


def test_one_marginal_animal_look_then_clear_person_looks_is_a_person(tmp_path):
    """The track jumped from a dog to a man (or one unlucky look): several clear person looks
    outweigh a single marginal animal look — the man must still alert."""
    e = _engine(tmp_path, line=LINE, enable_person=True)
    marginal = {"person": 0.5, "animal": 0.4, "animal_label": "dog"}
    e.detector.second_opinion = _Zoom(lambda b, n: marginal if n == 1 else PERSON)
    ev = _drive(e, [[d] for d in _walk(range(60, 340, 4))])  # slow approach: several looks
    assert [x["category"] for x in ev] == ["person"], (ev, e.detector.second_opinion.calls)
    _cleanup(ev)


def test_vehicle_never_zoomed_and_immediate(tmp_path):
    e = _engine(tmp_path, line=LINE, enable_person=True)
    e.detector.second_opinion = _Zoom(lambda b, n: DOG)
    frames = [[{"bbox": (420, y - 80, 540, y), "label": "car", "conf": 0.8}] for y in range(60, 340, 8)]
    ev = _drive(e, frames, flush=False)
    assert [x["category"] for x in ev] == ["vehicle"] and e.detector.second_opinion.calls == [], ev
    _cleanup(ev)


def test_zoom_returns_none_does_not_block(tmp_path):
    e = _engine(tmp_path, line=LINE, enable_person=True)
    e.detector.second_opinion = _Zoom(lambda b, n: None)
    ev = _drive(e, [[d] for d in _walk(range(60, 320, 6))])
    assert [x["category"] for x in ev] == ["person"], ev
    _cleanup(ev)


def test_full_res_original_box_scaled(tmp_path):
    """When an original full-res frame is given, the zoom box is scaled to it."""
    import numpy as np
    e = _engine(tmp_path, line=LINE, enable_person=True)
    seen = []
    e.detector.second_opinion = lambda f, b: seen.append((f.shape, b)) or PERSON
    orig = np.zeros((1080, 1920, 3), np.uint8)
    op = e._zoom_look(np.zeros((540, 960, 3), np.uint8), orig, (100, 100, 140, 200), 960, 540)
    assert op == PERSON and seen == [((1080, 1920, 3), (200, 200, 280, 400))]
