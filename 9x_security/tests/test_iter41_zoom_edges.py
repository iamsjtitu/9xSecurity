"""Iteration 41 (testing agent): edge cases around the shared yolov8s zoom model, multi-scale
early stop, Track.person_conf maths and the fail-closed person rule (engine._animal_on_zoom)."""
import os
import sys
import threading
import time
from types import SimpleNamespace

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
import tracker as trk  # noqa: E402
from test_categories import _engine  # noqa: E402

ASSETS = os.path.join(HERE, "assets")
LINE = {"x1": 0.05, "y1": 0.5, "x2": 0.95, "y2": 0.5}
FRAME = np.zeros((540, 960, 3), np.uint8)


def _bare_det(model_name, model=None):
    d = detector.VehicleDetector.__new__(detector.VehicleDetector)
    d.model_name, d.model = model_name, model
    return d


# ---- second_opinion: multi-scale, cheapest first, early stop ------------------------------
@pytest.fixture
def scripted(monkeypatch):
    """second_opinion with a scripted _zoom_once (list of per-scale results)."""
    calls = []

    def make(results, model_name="yolov8s.pt"):
        seq = list(results)

        def fake(model, lock, frame, bbox, grow, imgsz):
            calls.append((grow, imgsz))
            return seq.pop(0)

        monkeypatch.setattr(detector.VehicleDetector, "_zoom_once", staticmethod(fake))
        return _bare_det(model_name, model=object())

    return make, calls


def _r(p, a, lbl=""):
    return {"person": p, "animal": a, "animal_label": lbl}


def test_clear_animal_on_first_scale_stops(scripted):
    make, calls = scripted
    op = make([_r(0.1, 0.6, "dog")]).second_opinion(FRAME, (100, 100, 120, 150))
    assert calls == [(1.5, 320)] and op["looks"] == 1 and op["animal_label"] == "dog"


def test_clear_person_on_first_scale_stops(scripted):
    make, calls = scripted
    op = make([_r(0.8, 0.1, "cat")]).second_opinion(FRAME, (100, 100, 120, 150))
    assert len(calls) == 1 and op["person"] == 0.8 and op["looks"] == 1


def test_person_with_strong_animal_keeps_looking(scripted):
    # person 0.6 but animal 0.25 (>= 0.35*0.6) and animal < 0.30 -> not clear either way
    make, calls = scripted
    op = make([_r(0.6, 0.25, "dog"), _r(0.2, 0.1, "cat"), _r(0.3, 0.2, "cow")]).second_opinion(FRAME, (1, 1, 20, 50))
    assert calls == list(detector.ZOOM_SCALES) and op["looks"] == 3
    assert op["person"] == 0.6 and op["animal"] == 0.25 and op["animal_label"] == "dog"  # best per class


def test_animal_label_follows_best_look(scripted):
    make, calls = scripted
    op = make([_r(0.0, 0.10, "cat"), _r(0.0, 0.20, "dog"), _r(0.0, 0.15, "cow")]).second_opinion(FRAME, (1, 1, 20, 50))
    assert op["animal_label"] == "dog" and op["animal"] == 0.20 and op["looks"] == 3


def test_stops_on_second_scale(scripted):
    make, calls = scripted
    op = make([_r(0.0, 0.0), _r(0.1, 0.45, "dog"), _r(0.9, 0.0)]).second_opinion(FRAME, (1, 1, 20, 50))
    assert len(calls) == 2 and op["looks"] == 2 and op["person"] == 0.1


def test_all_scales_none_returns_none_and_skipped_scale_not_counted(scripted):
    make, calls = scripted
    assert make([None, None, None]).second_opinion(FRAME, (1, 1, 20, 50)) is None
    calls.clear()
    op = make([None, _r(0.0, 0.0), None]).second_opinion(FRAME, (1, 1, 20, 50))
    assert op["looks"] == 1 and len(calls) == 3


def test_off_frame_box_returns_none_without_inference():
    def boom(*a, **k):
        raise AssertionError("model must not run on an empty crop")
    d = _bare_det("yolov8s.pt", model=boom)
    assert d.second_opinion(FRAME, (3000, 3000, 3020, 3050)) is None


# ---- zoom_model_for: sharing, lock, fallback ----------------------------------------------
class _CountingModel:
    def __init__(self):
        self.active = self.peak = self.calls = 0
        self.mu = threading.Lock()

    def __call__(self, crop, **kw):
        with self.mu:
            self.active += 1
            self.calls += 1
            self.peak = max(self.peak, self.active)
        time.sleep(0.03)
        with self.mu:
            self.active -= 1
        return [SimpleNamespace(boxes=None)]


def test_shared_zoom_inference_is_serialised(monkeypatch):
    fake = _CountingModel()
    monkeypatch.setattr(detector, "_ZOOM_MODEL", fake)
    dets = [_bare_det("yolov8n.pt", model=object()) for _ in range(2)]
    ths = [threading.Thread(target=lambda d=d: [d.second_opinion(FRAME, (400, 200, 440, 300)) for _ in range(3)])
           for d in dets for _ in range(3)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    assert fake.calls == 6 * 3 * len(detector.ZOOM_SCALES)  # nothing found -> every scale runs
    assert fake.peak == 1


def test_accurate_camera_zoom_has_no_lock(monkeypatch):
    fake = _CountingModel()
    d = _bare_det("yolov8s.pt", model=fake)
    m, name, lock = detector.zoom_model_for(d.model, d.model_name)
    assert m is fake and name == "yolov8s.pt" and lock is None


def test_fast_camera_without_yolov8s_zooms_with_own_model(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MODEL_PATH", str(tmp_path / "yolov8n.pt"))
    own = object()
    m, name, lock = detector.zoom_model_for(own, "yolov8n.pt")
    assert m is own and name == "yolov8n.pt" and lock is None


# ---- Track.person_conf ------------------------------------------------------------------
def test_track_person_conf_maths():
    t = trk.Track(1, (0, 0), (0, 0, 10, 30), "person", 1, conf=0.5)
    assert t.person_conf == pytest.approx(0.5)
    t.labels["person"] += 1
    t.label_w["person"] += 0.9
    t.labels["dog"] += 1
    t.label_w["dog"] += 0.99  # animal frames do not enter the person average
    assert t.person_conf == pytest.approx(0.7)
    assert trk.Track(2, (0, 0), (0, 0, 10, 30), "dog", 1, conf=0.8).person_conf == 0.0


def test_tracker_update_accumulates_person_conf():
    ct = trk.CentroidTracker()
    line = ((0, 270), (960, 270))
    for i, c in enumerate((0.4, 0.6, 0.8)):
        ct.update([{"bbox": (460, 100 + i * 5, 500, 200 + i * 5), "label": "person", "conf": c}], line, now=i * 0.125)
    (t,) = ct.tracks.values()
    assert t.labels["person"] == 3 and t.person_conf == pytest.approx(0.6)


# ---- engine._animal_on_zoom: fail-closed boundaries ----------------------------------------
@pytest.fixture
def eng_track(tmp_path, monkeypatch):
    monkeypatch.setattr(eng, "clog", lambda m: None)
    e = _engine(tmp_path, line=LINE, enable_person=True)

    def make(op, frame_conf=0.5, prior_looks=0, prior_person=0.0, n_frames=4):
        t = trk.Track(7, (480, 250), (460, 150, 500, 250), "person", 1, conf=frame_conf)
        t.labels["person"] = n_frames
        t.label_w["person"] = frame_conf * n_frames
        t.zoom_looks, t.zoom_person = prior_looks, prior_person
        e.tracker2.tracks[7] = t
        e.detector.second_opinion = lambda f, b: (dict(op) if op else op)
        return e, t

    return make


CR = {"bbox": (460, 150, 500, 250), "track_id": 7}
BLIND = {"person": 0.0, "animal": 0.0, "animal_label": ""}


def _why(e):
    return e._animal_on_zoom(FRAME, None, dict(CR), 960, 540)


def test_blind_zoom_hesitant_frame_drops(eng_track):
    e, t = eng_track(BLIND, frame_conf=0.5)
    assert "insaan kahin nahi dikha" in _why(e) and t.zoom_looks == 1


def test_person_conf_exactly_sure_passes(eng_track):
    e, _ = eng_track(BLIND, frame_conf=eng.PERSON_SURE_CONF)
    assert _why(e) == ""


def test_zoom_person_exactly_min_passes(eng_track):
    e, _ = eng_track({"person": eng.ZOOM_PERSON_MIN, "animal": 0.0, "animal_label": ""}, frame_conf=0.4)
    assert _why(e) == ""


def test_zoom_person_just_below_min_drops(eng_track):
    e, _ = eng_track({"person": eng.ZOOM_PERSON_MIN - 0.01, "animal": 0.0, "animal_label": ""}, frame_conf=0.4)
    assert "insaan kahin nahi dikha" in _why(e)


def test_noise_person_scores_sum_over_looks(eng_track):
    """zoom_person is a SUM: two patrol looks of 0.12 + one final 0.02 = 0.26 >= 0.25 -> alert passes.
    Documents current behaviour (several weak looks add up to 'saw a person')."""
    e, _ = eng_track({"person": 0.02, "animal": 0.0, "animal_label": ""}, frame_conf=0.4, prior_looks=2, prior_person=0.24)
    assert _why(e) == ""


def test_final_look_none_with_no_prior_looks_fail_open(eng_track):
    e, t = eng_track(None, frame_conf=0.3)
    assert _why(e) == "" and t.zoom_looks == 0


def test_final_look_none_but_prior_blind_looks_still_drops(eng_track):
    e, _ = eng_track(None, frame_conf=0.4, prior_looks=2, prior_person=0.0)
    assert "insaan kahin nahi dikha" in _why(e)


def test_zoom_exception_fail_open(eng_track):
    e, t = eng_track(BLIND, frame_conf=0.3)
    e.detector.second_opinion = lambda f, b: (_ for _ in ()).throw(RuntimeError("cuda gone"))
    assert _why(e) == "" and t.zoom_looks == 0


def test_unknown_track_fail_open(eng_track):
    e, _ = eng_track(BLIND, frame_conf=0.3)
    e.tracker2.tracks.clear()
    assert _why(e) == ""


def test_full_res_original_box_is_scaled(eng_track):
    e, _ = eng_track(BLIND)
    seen = {}

    def look(f, b):
        seen["shape"], seen["box"] = f.shape[:2], b
        return dict(BLIND)
    e.detector.second_opinion = look
    big = np.zeros((1080, 1920, 3), np.uint8)
    e._animal_on_zoom(FRAME, big, dict(CR), 960, 540)
    assert seen["shape"] == (1080, 1920) and seen["box"] == (920, 300, 1000, 500)


# ---- real models: two fast engines share ONE yolov8s, concurrently ---------------------
def test_two_fast_detectors_concurrent_real_dog():
    path, _ = detector.resolve_model_path("fast")
    allowed = config.detect_classes({**config.DEFAULTS, "enable_person": True})
    a = detector.VehicleDetector(path, conf=0.25, allowed=allowed)
    b = detector.VehicleDetector(path, conf=0.25, allowed=allowed)
    frames = {n: cv2.imread(os.path.join(ASSETS, f"user_dog_{n}.jpg")) for n in (1, 3, 5)}
    boxes = {1: (192, 178, 209, 222), 3: (187, 161, 213, 239), 5: (174, 170, 226, 231)}
    out, errs = [], []

    def run(d, n):
        try:
            out.append((n, d.second_opinion(frames[n], boxes[n])))
        except Exception as ex:  # pragma: no cover
            errs.append(ex)

    ths = [threading.Thread(target=run, args=(d, n)) for d in (a, b) for n in frames]
    [t.start() for t in ths]
    [t.join(120) for t in ths]
    assert not errs and len(out) == 6
    for n, op in out:
        assert op["model"] == "yolov8s.pt" and op["animal_label"] == "dog" and eng.SecurityEngine._zoom_says_animal(op), (n, op)
    assert detector.zoom_model_for(a.model, a.model_name)[0] is detector.zoom_model_for(b.model, b.model_name)[0]


def test_user_dog_upscaled_full_res_still_dog():
    """Engine zooms on the full-res original when present: a 2x frame must give the same verdict."""
    path, _ = detector.resolve_model_path("fast")
    d = detector.VehicleDetector(path, conf=0.25, allowed=["person"])
    f = cv2.imread(os.path.join(ASSETS, "user_dog_2.jpg"))
    big = cv2.resize(f, None, fx=2, fy=2)
    op = d.second_opinion(big, (376, 354, 424, 448))
    assert op["animal_label"] == "dog" and eng.SecurityEngine._zoom_says_animal(op), op
