"""PC Load meter (dashboard card + /api/state.load + diagnostics): per-camera fps / AI ms / skip %
with ok|warn|bad levels, PC CPU/RAM, and a Hinglish overload hint naming the slow cameras."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import service  # noqa: E402


class _Eng:
    class detector:
        model_name = "yolov8s.pt"
    possible_misses = 0


class _W:
    def __init__(self, connected=True, standby=False, engine=_Eng(), paused=False, fps=12.0, ai_ms=60,
                 frame_age=0.5):
        self.connected, self.standby, self.engine, self.capture_paused = connected, standby, engine, paused
        self.fps, self.ai_ms, self.drop_pct = fps, ai_ms, 30
        self.last_frame_ts = time.time() - frame_age
        self.ai_frames, self.ai_errors, self.codec, self.frames_dropped = 100, 0, "h264", 40
        self.status = "Connected"


def test_camera_load_levels():
    assert service._camera_load(None) is None
    assert service._camera_load(_W(connected=False)) is None
    assert service._camera_load(_W(standby=True)) is None
    assert service._camera_load(_W(engine=None)) == "idle"
    assert service._camera_load(_W(paused=True)) == "idle"
    assert service._camera_load(_W(frame_age=30)) == "connecting"
    assert service._camera_load(_W(fps=None)) == "warming"
    assert service._camera_load(_W()) == "ok"
    assert service._camera_load(_W(fps=6)) == "warn"
    assert service._camera_load(_W(ai_ms=300)) == "warn"
    assert service._camera_load(_W(fps=2)) == "bad"
    assert service._camera_load(_W(ai_ms=600)) == "bad"


def test_load_summary_levels_and_hint(monkeypatch):
    cfg = {"cameras": [{"id": "a", "name": "Gate A"}, {"id": "b", "name": "Gate B"}]}
    monkeypatch.setattr(service.pool, "workers", {"a": _W(), "b": _W(fps=2)})
    monkeypatch.setitem(service._sys_load, "cpu_pct", 40)
    s = service._load_summary(cfg)
    assert s["level"] == "bad" and "Gate B" in s["hint"] and "Gate A" not in s["hint"] and s["running"] == 2
    assert s["ai_total_ms"] == round(12 * 60 + 2 * 60)
    monkeypatch.setattr(service.pool, "workers", {"a": _W(), "b": _W()})
    assert service._load_summary(cfg)["level"] == "ok" and service._load_summary(cfg)["hint"] == ""
    monkeypatch.setitem(service._sys_load, "cpu_pct", 75)
    assert service._load_summary(cfg)["level"] == "warn"
    monkeypatch.setitem(service._sys_load, "cpu_pct", 90)
    s = service._load_summary(cfg)
    assert s["level"] == "bad" and s["hint"].startswith("PC par load zyada hai")
    monkeypatch.setitem(service._sys_load, "cpu_pct", None)  # no psutil: camera levels alone decide
    assert service._load_summary(cfg)["level"] == "ok"


def test_camera_status_carries_load_fields(monkeypatch):
    monkeypatch.setattr(service.pool, "workers", {"a": _W(fps=9.5, ai_ms=120.4)})
    st = service._camera_status({"id": "a", "name": "Gate A"})
    assert st["fps"] == 9.5 and st["ai_ms"] == 120 and st["drop_pct"] == 30 and st["load"] == "ok"
    assert st["codec"] == "h264" and st["frames_dropped"] == 40 and st["ai_frames"] == 100
    st2 = service._camera_status({"id": "zzz", "name": "none"})
    assert st2["fps"] is None and st2["load"] is None and st2["ai_ms"] is None
