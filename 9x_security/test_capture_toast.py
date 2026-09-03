"""Capture toast: engine event -> worker.last_event -> /api/state.last_event."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import service  # noqa: E402


def test_on_event_exposed_in_state():
    from fastapi.testclient import TestClient

    tok = "c" * 48
    service._tokens[tok] = time.time()
    c = TestClient(service.app)
    c.headers["X-Auth-Token"] = tok
    service.worker.last_event = None
    assert c.get("/api/state").json()["last_event"] is None
    service.worker._on_event({"id": 7, "direction": "Entry", "vehicle_type": "car", "plate": "",
                              "image_path": "/x/a.jpg", "timestamp": "2026-09-03T13:30:53"})
    le = c.get("/api/state").json()["last_event"]
    assert le["id"] == 7 and le["direction"] == "Entry" and le["vehicle_type"] == "car"
    assert le["timestamp"] == "2026-09-03T13:30:53" and le["image_path"] == "/x/a.jpg"
    service.worker.last_event = None


def test_engine_wires_on_event(monkeypatch):
    """Worker must hook engine.on_event so real crossings reach the UI."""
    import inspect

    src = inspect.getsource(service.Worker._run_impl)
    assert "self.engine.on_event = self._on_event" in src
