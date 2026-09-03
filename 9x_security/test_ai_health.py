"""AI health visibility: self-test, sticky AI status, per-frame error surfacing, selftest()."""
import os
import sys
import time

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
import service  # noqa: E402
import updater  # noqa: E402


class BrokenDetector:
    def detect(self, frame):
        raise RuntimeError("operator torchvision::nms does not exist")

    def set_allowed(self, a):
        pass


class OkDetector:
    def detect(self, frame):
        return []

    def set_allowed(self, a):
        pass


class FakeEngine:
    def __init__(self, detector):
        self.detector = detector
        self.cfg = dict(service.config.DEFAULTS)
        self.notifier = type("N", (), {"update": lambda self, c: None})()

    def process_frame(self, frame, original=None):
        self.detector.detect(frame)
        return frame, []


def test_selftest_failure_disables_ai_and_marks_error():
    w = service.Worker()
    w.engine = FakeEngine(BrokenDetector())
    with pytest.raises(RuntimeError):
        w._ai_selftest()
    # mimic _run_impl's handling
    try:
        w._ai_selftest()
    except Exception:
        import traceback

        w._note_ai_error("AI self-test", traceback.format_exc())
        w.engine = None
    assert "torchvision::nms" in w.ai_error and w.engine is None
    assert "AI ERROR" in w._live_status()
    frame = np.zeros((540, 960, 3), np.uint8)
    out = w._draw_line_only(frame, w_cfg := dict(service.config.DEFAULTS))
    assert (out[530, 10] == np.array([0, 0, 160])).all() or out[520:540, :, 2].max() > 100  # red banner


def test_selftest_ok_records_ms():
    w = service.Worker()
    w.engine = FakeEngine(OkDetector())
    assert w._ai_selftest() >= 0
    assert w._live_status() == "Connected — live monitoring chalu hai"


def test_per_frame_error_is_rate_limited(tmp_path, monkeypatch):
    import engine as eng_mod

    log = tmp_path / "camera_log.txt"
    monkeypatch.setattr(eng_mod, "CAMERA_LOG", str(log))
    monkeypatch.setattr(service, "clog", eng_mod.clog)
    w = service.Worker()
    for _ in range(50):
        try:
            raise ValueError("boom per frame")
        except Exception:
            import traceback

            w._note_ai_error("process_frame", traceback.format_exc())
    assert w.ai_errors == 50 and w.ai_error.startswith("process_frame: ValueError: boom")
    assert log.read_text().count("process_frame error") == 1  # not 50 tracebacks


def test_state_and_diagnostics_expose_ai_fields():
    from fastapi.testclient import TestClient

    tok = "a" * 48
    service._tokens[tok] = time.time()
    c = TestClient(service.app)
    c.headers["X-Auth-Token"] = tok
    st = c.get("/api/state").json()
    for k in ("ai_loaded", "ai_error", "ai_ms"):
        assert k in st
    d = c.get("/api/diagnostics").json()
    assert "versions" in d and "torch" in d["versions"] and "engine_out_log" in d
    assert {"ai_error", "ai_ms", "ai_frames", "ai_errors"} <= set(d["engine"])


def test_selftest_entrypoint_returns_0_with_real_model():
    rc = service.selftest()
    assert rc == 0


def test_selftest_entrypoint_returns_1_when_yolo_broken(monkeypatch):
    import detector

    monkeypatch.setattr(detector.VehicleDetector, "detect", BrokenDetector.detect)
    assert service.selftest() == 1
