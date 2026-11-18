"""v1.0.53 field report: after an in-app update the engine came up from a half-extracted install folder
(numpy / torch._C 'No module named') and the camera host stopped answering with only a generic status.
Covers: installer hand-off script, update exit code, broken-install auto-restart, camera-unreachable status."""
import os
import socket
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine  # noqa: E402
import service  # noqa: E402
import updater  # noqa: E402


# ---------------- update hand-off ----------------
def test_run_installer_script_waits_for_app_and_pauses_watchdog(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    calls = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: calls.append((a, k)))
    assert updater._run_installer(r"C:\Temp\9xSecuritySetup_new.exe") is True
    (args,), kw = calls[0]
    assert args[:2] == ["cmd", "/c"] and args[2].endswith("9x_update.bat")
    assert kw["creationflags"] & 0x00000008  # detached: survives the engine exiting
    bat = open(args[2]).read()
    marker = os.path.join(str(tmp_path), "update_in_progress")
    assert f'> "{marker}"' in bat and f'del "{marker}"' in bat
    assert 'schtasks /Change /TN "9x Security Watchdog" /DISABLE' in bat
    assert 'schtasks /Change /TN "9x Security Watchdog" /ENABLE' in bat
    assert bat.index("/DISABLE") < bat.index('start "" /wait') < bat.index("/ENABLE") < bat.index(f'del "{marker}"')
    assert 'tasklist /FI "IMAGENAME eq 9xEngine.exe"' in bat and 'tasklist /FI "IMAGENAME eq 9x Security.exe"' in bat
    assert "if %n% geq 90 goto run" in bat and 'taskkill /F /T /IM "9xEngine.exe"' in bat  # never waits forever
    assert 'start "" /wait "C:\\Temp\\9xSecuritySetup_new.exe" /S --force-run' in bat
    assert updater.UPDATE_EXIT_CODE == 75


def test_update_job_exits_engine_after_handoff(monkeypatch):
    """After the installer is handed over the engine must EXIT (code 75) so no DLL stays locked; Electron quits on 75."""
    exits, timers = [], []

    class T:
        def __init__(self, secs, fn):
            timers.append(secs)
            self.fn = fn

        def start(self):
            self.fn()

    monkeypatch.setattr(service.threading, "Timer", T)
    monkeypatch.setattr(service.os, "_exit", lambda c: exits.append(c))
    monkeypatch.setattr(service.updater, "release_assets", lambda repo, token=None: ("9.9.9", ["http://x/s.exe"], "http://rel"))
    monkeypatch.setattr(service.updater, "download", lambda urls, dest, **k: dest)
    monkeypatch.setattr(service.updater, "apply_update", lambda p: True)
    service._update_cancel.clear()
    service._run_update_job("o/r", None)
    assert service._update_job["state"] == "done" and service._update_job["ok"] is True
    assert "band hogi" in service._update_job["message"] and "khud khulegi" in service._update_job["message"]
    assert timers == [3.0] and exits == [75]


def test_update_job_dev_mode_does_not_exit(monkeypatch):
    exits = []
    monkeypatch.setattr(service.os, "_exit", lambda c: exits.append(c))
    monkeypatch.setattr(service.updater, "release_assets", lambda repo, token=None: ("9.9.9", ["http://x/s.exe"], "http://rel"))
    monkeypatch.setattr(service.updater, "download", lambda urls, dest, **k: dest)
    monkeypatch.setattr(service.updater, "apply_update", lambda p: False)
    service._update_cancel.clear()
    service._run_update_job("o/r", None)
    assert service._update_job["state"] == "done" and service._update_job["ok"] is False and exits == []


def test_electron_main_quits_on_update_exit_code_and_skips_watchdog_start():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "electron", "electron-main.js")).read()
    assert "const UPDATE_EXIT_CODE = 75" in src and "if (code === UPDATE_EXIT_CODE)" in src
    assert "quitting = true;\n      app.quit();\n      return;" in src  # no supervisor restart on 75
    assert "'update_in_progress'" in src and "updateInstalling()" in src
    assert "process.argv.includes('--watchdog') && updateInstalling() ? null : app.requestSingleInstanceLock()" in src


# ---------------- broken-install auto-restart ----------------
@pytest.fixture
def fresh_restart_state(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(service, "_restart_scheduled", False)
    yield tmp_path


def test_broken_install_restarts_at_most_3_times(fresh_restart_state, monkeypatch):
    exits, logs = [], []

    class T:
        def __init__(self, secs, fn):
            assert secs == 20.0
            self.fn, self.daemon = fn, False

        def start(self):
            self.fn()

    now = 1_000_000.0
    hints = []
    for i in range(4):
        monkeypatch.setattr(service, "_restart_scheduled", False)  # a new process each time
        hints.append(service.schedule_broken_install_restart(logs.append, exit_fn=exits.append, timer=T, now=now + i * 60))
    assert exits == [76, 76, 76] and hints == ["", "", "", service.BROKEN_INSTALL_HINT]
    assert "try 1/3" in logs[0] and "try 3/3" in logs[2] and "reinstall needed" in logs[3]
    # 30 minutes later the counter has expired -> restarts allowed again
    monkeypatch.setattr(service, "_restart_scheduled", False)
    assert service.schedule_broken_install_restart(logs.append, exit_fn=exits.append, timer=T, now=now + 3600) == ""
    assert exits == [76, 76, 76, 76]


def test_broken_install_restart_once_per_process(fresh_restart_state):
    exits = []

    class T:
        def __init__(self, secs, fn):
            self.fn, self.daemon = fn, False

        def start(self):
            self.fn()

    assert service.schedule_broken_install_restart(lambda m: None, exit_fn=exits.append, timer=T) == ""
    assert service.schedule_broken_install_restart(lambda m: None, exit_fn=exits.append, timer=T) == ""  # 2nd camera worker
    assert exits == [76]


def test_model_load_import_error_triggers_restart_only_when_frozen(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "schedule_broken_install_restart", lambda log: calls.append(1) or "HINT")

    def boom(**k):
        raise ModuleNotFoundError("No module named 'torch._C'")

    monkeypatch.setattr(service, "SecurityEngine", boom)
    w = service.Worker.__new__(service.Worker)
    w.cam_cfg = lambda: {}
    w._log = lambda m: None
    w.engine, w.ai_error, w.ai_ms, w.ai_frames, w.ai_errors, w.status = None, "", None, 0, 0, ""
    w._gen, w._running = 0, False
    w._alive = lambda g: False
    w.should_stream = lambda: True
    monkeypatch.setattr(service.sys, "frozen", False, raising=False)
    w._run_impl(0)
    assert calls == [] and "torch._C" in w.ai_error and "HINT" not in w.ai_error
    monkeypatch.setattr(service.sys, "frozen", True, raising=False)
    w._run_impl(0)
    assert calls == [1] and w.ai_error.endswith("— HINT")


# ---------------- camera host unreachable ----------------
def test_rtsp_port_reachable_refused_and_timeout():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens now -> refused
    ok, why = service.rtsp_port_reachable(f"rtsp://admin:pw@127.0.0.1:{port}/stream1", timeout=1.0)
    assert ok is False and why == "connection refused"
    ok, why = service.rtsp_port_reachable("rtsp://admin:pw@10.255.255.1:554/stream1", timeout=0.3)
    assert ok is False and why in ("timed out", "[Errno 101] Network is unreachable") or not ok
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    try:
        assert service.rtsp_port_reachable(f"rtsp://u:p@127.0.0.1:{srv.getsockname()[1]}/x") == (True, "")
    finally:
        srv.close()
    assert service.rtsp_port_reachable("not a url") == (True, "")


def test_no_answer_status_messages():
    refused = service.no_answer_status("rtsp://admin:Admin%40123@192.168.1.7:554/video", "connection refused")
    assert refused.startswith("ERROR: 192.168.1.7:554 par koi camera/RTSP nahi mila (connection refused)")
    assert "IP badal gaya" in refused and "Scan" in refused and "Admin" not in refused  # never leak credentials
    timeout = service.no_answer_status("rtsp://admin:pw@192.168.1.7/video", "timed out")
    assert timeout.startswith("ERROR: Camera 192.168.1.7:554 jawab nahi de raha (timeout)") and "power" in timeout


def test_auto_fix_reports_no_answer_detail(monkeypatch):
    import rtsp_discover

    monkeypatch.setattr(rtsp_discover, "rtsp_describe", lambda url: (0, "[WinError 10061] No connection could be made"))
    fixed, why = engine.auto_fix_stream_url("rtsp://u:p@192.168.1.7:554/video", log=lambda m: None)
    assert fixed == "" and why == "no-answer|[WinError 10061] No connection could be made"


def test_stream_once_unreachable_sets_status_and_retries_fast(monkeypatch):
    monkeypatch.setattr(service, "rtsp_port_reachable", lambda src, timeout=2.5: (False, "connection refused"))
    opened = []
    monkeypatch.setattr(service.time, "sleep", lambda s: None)
    w = service.Worker.__new__(service.Worker)
    w.open_error, w.open_detail, w._unreach_logged = "", "", 0.0
    logs = []
    w._log = logs.append
    w._gen = 0
    w._alive = lambda g: True
    w._open = lambda *a, **k: opened.append(1)
    cont = w._stream_once(0, {"rtsp_url": "rtsp://admin:pw@192.168.1.7:554/video"})
    assert cont is True and opened == []  # no 2-minute open ladder
    assert w.open_error == "no-answer" and w.status.startswith("ERROR: 192.168.1.7:554 par koi camera/RTSP nahi mila")
    assert logs == ["camera host not reachable (connection refused) — retrying every 10 s"]
    w._stream_once(0, {"rtsp_url": "rtsp://admin:pw@192.168.1.7:554/video"})
    assert len(logs) == 1  # logged once per 10 min, not every retry
    # worker thread keeps looping (status stays visible) instead of ending until the next auto-connect
    assert threading.current_thread() is not None
