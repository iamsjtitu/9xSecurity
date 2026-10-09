"""USER: 'dono camera ek saath chalein, har ek apne time me' — every saved camera runs in its own
worker; a camera outside its own time window is in STANDBY (stream closed) unless it is being viewed."""
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import config  # noqa: E402
import service  # noqa: E402


def _cfg_two(viewed="cam1", sched2=None, monitor2=True):
    cfg = {**config.DEFAULTS, "line": dict(config.DEFAULTS["line"]), "ignore_zones": [],
           "rtsp_url": "rtsp://a/1", "cameras": [], "active_camera_id": ""}
    config.sync_cameras(cfg)                       # -> cam1 from the top-level URL
    cfg["cameras"][0]["gate"] = "Main Gate"
    cfg["cameras"].append({"id": "cam2", "name": "Back", "gate": "Back Gate", "rtsp_url": "rtsp://b/2",
                           "rtsp_url_main": "", "line": {"x1": 0.1, "y1": 0.7, "x2": 0.9, "y2": 0.7},
                           "entry_direction": "neg", "ignore_zones": [], "monitor": monitor2,
                           "schedule": sched2 or {"enabled": True, "start": "20:00", "end": "08:00"}})
    cfg["active_camera_id"] = viewed
    config.sync_cameras(cfg)
    return cfg


def test_camera_cfg_view_and_gate():
    cfg = _cfg_two()
    c2 = config.camera_cfg(cfg, config.camera_by_id(cfg, "cam2"))
    assert c2["rtsp_url"] == "rtsp://b/2" and c2["line"]["y1"] == 0.7 and c2["entry_direction"] == "neg"
    assert config.gate_name(c2) == "Back Gate" and c2["_camera_id"] == "cam2"
    assert c2["vehicle_classes"] == cfg["vehicle_classes"]  # shared settings come along
    assert config.gate_name(cfg) == "Main Gate"              # plain cfg -> viewed camera's gate


def test_camera_window_open_and_fmt12():
    cam = {"schedule": {"enabled": True, "start": "20:00", "end": "08:00"}}
    assert config.camera_window_open(cam, now=datetime(2026, 6, 1, 23, 0))
    assert config.camera_window_open(cam, now=datetime(2026, 6, 1, 7, 59))
    assert not config.camera_window_open(cam, now=datetime(2026, 6, 1, 13, 0))
    assert config.camera_window_open({"schedule": {"enabled": False, "start": "20:00", "end": "08:00"}}, now=datetime(2026, 6, 1, 13, 0))
    assert config.camera_window_open({}, now=datetime(2026, 6, 1, 13, 0))   # no schedule at all = 24h
    assert config.camera_schedule({})["start"] == "20:00"
    assert config.fmt12("20:00") == "8:00 PM" and config.fmt12("00:30") == "12:30 AM" and config.fmt12("12:00") == "12:00 PM"


def test_worker_should_stream_window_or_viewed(monkeypatch):
    cfg = _cfg_two(viewed="cam1")
    monkeypatch.setattr(service, "_cfg", lambda: cfg)
    monkeypatch.setattr(config, "camera_window_open", lambda cam, now=None: cam.get("id") != "cam2")  # cam2 outside
    w1, w2 = service.Worker("cam1"), service.Worker("cam2")
    assert w1.should_stream() and not w2.should_stream()
    assert w2.cam_cfg()["rtsp_url"] == "rtsp://b/2" and w2.name == "Back"
    cfg["active_camera_id"] = "cam2"                   # user looks at camera 2 -> it streams (live view)
    assert w2.should_stream() and w2.is_viewed()


def test_pool_sync_starts_monitored_cameras_and_stops_the_rest(monkeypatch):
    calls = []
    monkeypatch.setattr(service.Worker, "start", lambda self: calls.append(("start", self.cam_id)))
    monkeypatch.setattr(service.Worker, "stop", lambda self: calls.append(("stop", self.cam_id)))
    pool = service.WorkerPool()
    cfg = _cfg_two(monitor2=True)
    assert sorted(pool.sync(cfg, auto=True)) == ["Back", "Camera 1"]
    assert ("start", "cam1") in calls and ("start", "cam2") in calls
    # camera 2 monitoring OFF -> its worker is stopped; camera removed -> worker dropped
    calls.clear()
    pool.workers["cam2"]._running = True
    cfg["cameras"][1]["monitor"] = False
    assert pool.sync(cfg, auto=True) == ["Camera 1"]  # cam1 still not connected (mocked start) -> retried
    assert ("stop", "cam2") in calls and ("start", "cam2") not in calls
    cfg["cameras"] = cfg["cameras"][:1]
    pool.sync(cfg)
    assert "cam2" not in pool.workers
    # a Disconnected camera (user_stopped) is left alone by auto-connect
    pool.workers["cam1"].user_stopped = True
    calls.clear()
    pool.sync(cfg, auto=True)
    assert ("start", "cam1") not in calls


def test_pool_sync_drops_legacy_worker_once_a_camera_exists(monkeypatch):
    monkeypatch.setattr(service.Worker, "stop", lambda self: setattr(self, "_running", False))
    monkeypatch.setattr(service.Worker, "start", lambda self: None)
    pool = service.WorkerPool()
    legacy = pool.get("")
    legacy._running = True
    pool.sync(_cfg_two())
    assert "" not in pool.workers and legacy._running is False


def test_pool_last_event_is_the_newest_across_cameras():
    pool = service.WorkerPool()
    pool.get("cam1").last_event = {"id": 1, "timestamp": "2026-06-01T10:00:00", "gate": "Main Gate"}
    pool.get("cam2").last_event = {"id": 2, "timestamp": "2026-06-01T10:05:00", "gate": "Back Gate"}
    assert pool.last_event()["gate"] == "Back Gate"


def test_standby_wait_holds_until_window_opens_or_viewed(monkeypatch):
    cfg = _cfg_two(viewed="cam1")
    monkeypatch.setattr(service, "_cfg", lambda: cfg)
    closed = {"cam2": True}
    monkeypatch.setattr(config, "camera_window_open", lambda cam, now=None: not closed.get(cam.get("id"), False))
    monkeypatch.setattr(service.time, "sleep", lambda s: None)
    w = service.Worker("cam2")
    w._running, w._gen = True, 1
    # open the window after a few polls
    polls = {"n": 0}
    orig = w.should_stream

    def should_stream(cfg_=None):
        polls["n"] += 1
        if polls["n"] >= 3:
            closed["cam2"] = False
        return orig(cfg_)
    monkeypatch.setattr(w, "should_stream", should_stream)
    assert w._wait_standby(1) is True
    assert "Standby" in w.status and "8:00 PM" in w.status and "8:00 AM" in w.status
    assert w.standby is False and polls["n"] >= 3
    # stopped while in standby -> returns False
    closed["cam2"] = True
    w2 = service.Worker("cam2")
    w2._running, w2._gen = True, 1
    monkeypatch.setattr(service.time, "sleep", lambda s: setattr(w2, "_running", False))
    assert w2._wait_standby(1) is False


def test_stream_once_goes_to_standby_when_window_closes(monkeypatch):
    """Running camera leaves its window -> loop exits with True (standby), stream released, frame cleared."""
    cfg = _cfg_two(viewed="cam1")
    monkeypatch.setattr(service, "_cfg", lambda: cfg)
    import numpy as np

    class Cap:
        released = False

        def isOpened(self):
            return True

        def read(self):
            return True, np.zeros((540, 960, 3), np.uint8)

        def release(self):
            Cap.released = True
    w = service.Worker("cam2")
    w._running, w._gen, w.engine = True, 1, None
    monkeypatch.setattr(w, "_open", lambda source, live, gen=None: Cap())
    state = {"open": True, "t": 1000.0}
    monkeypatch.setattr(config, "camera_window_open", lambda cam, now=None: cam.get("id") != "cam2" or state["open"])
    monkeypatch.setattr(service.time, "time", lambda: state["t"])
    monkeypatch.setattr(service.time, "sleep", lambda s: None)
    reads = {"n": 0}
    orig_read = Cap.read

    def read(self):
        reads["n"] += 1
        if reads["n"] == 5:
            state["open"] = False     # window closes
            state["t"] += 3           # > 2 s since the last schedule check
        return orig_read(self)
    monkeypatch.setattr(Cap, "read", read)
    assert w._stream_once(1, w.cam_cfg()) is True
    assert Cap.released and w.latest() is None
    time.sleep(0)
