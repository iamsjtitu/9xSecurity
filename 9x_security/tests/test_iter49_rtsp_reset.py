"""Field report (DELL, v1.0.54): camera 192.168.31.180:554 reachable but every RTSP request gets
'[WinError 10054] connection forcibly closed' -> status wrongly said 'timeout / power check'; the worker kept
retrying (which keeps a camera-side IP lock alive). Covers: reset explanation, alternate RTSP port search,
Test-step wording, lock-aware auto-connect backoff cleared by a manual Connect."""
import os
import socket
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine  # noqa: E402
import rtsp_discover as rd  # noqa: E402
import service  # noqa: E402

RST = "[WinError 10054] An existing connection was forcibly closed by the remote host"


def test_is_reset_and_explanations():
    assert rd.is_reset(RST) and rd.is_reset("[Errno 104] Connection reset by peer") and not rd.is_reset("timed out")
    t = rd.explain_no_rtsp("192.168.31.180", 554, RST)
    assert "port khula" in t and "IP LOCK" in t and "RTSP service OFF" in t and "NVR/phone app" in t
    assert "jawab nahi diya" in rd.explain_no_rtsp("1.2.3.4", 554, "timed out")


def test_no_answer_status_reset_branch_not_timeout():
    s = service.no_answer_status("rtsp://admin:Admin%40123@192.168.31.180:554/cam/realmonitor?channel=1&subtype=0", RST)
    assert s.startswith("ERROR: Camera 192.168.31.180:554 network par hai par RTSP request par connection turant kaat deta hai")
    assert "IP LOCK" in s and "RTSP service OFF" in s and "timeout" not in s and "power" not in s and "Admin" not in s


def _rtsp_server(reply):
    """Tiny TCP server answering every request with `reply` (bytes); returns (port, close_fn)."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    stop = threading.Event()

    def run():
        srv.settimeout(0.3)
        while not stop.is_set():
            try:
                c, _ = srv.accept()
            except socket.timeout:
                continue
            try:
                c.settimeout(1.0)
                c.recv(4096)
                c.sendall(reply)
            except OSError:
                pass
            finally:
                c.close()

    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1], lambda: (stop.set(), srv.close())


def test_find_rtsp_port_skips_closed_and_reset_ports_and_finds_rtsp():
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    dead_port = dead.getsockname()[1]
    dead.close()  # closed port -> connection refused -> skipped
    rst_port, close_rst = _rtsp_server(b"")  # accepts, then closes without an RTSP line -> code 0 -> skipped
    ok_port, close_ok = _rtsp_server(b"RTSP/1.0 401 Unauthorized\r\nCSeq: 1\r\nWWW-Authenticate: Basic realm=\"cam\"\r\n\r\n")
    try:
        port, code = rd.find_rtsp_port("127.0.0.1", "", "", "/cam/realmonitor?channel=1", ports=(dead_port, rst_port, ok_port))
        assert (port, code) == (ok_port, 401)
        assert rd.find_rtsp_port("127.0.0.1", "", "", "/x", ports=(dead_port, rst_port)) == (0, 0)
    finally:
        close_rst()
        close_ok()


def test_probe_explains_reset_and_switches_to_alternate_port(monkeypatch):
    url = "rtsp://admin:Admin%40123@192.168.31.180:554/cam/realmonitor?channel=1&subtype=0"
    monkeypatch.setattr(engine.socket if hasattr(engine, "socket") else socket, "create_connection",
                        lambda addr, timeout=3: type("S", (), {"close": lambda self: None})())
    calls = []

    def fake_describe(u, timeout=3.0):
        calls.append(u)
        return (0, RST) if ":554/" in u else (401, "RTSP/1.0 401 Unauthorized")

    monkeypatch.setattr(rd, "rtsp_describe", fake_describe)
    monkeypatch.setattr(rd, "find_rtsp_port", lambda host, user, pw, path, ports=None, timeout=1.5: (8554, 401))
    monkeypatch.setattr(engine, "_try_capture", lambda u, transport, wait: (False, "no video"))
    monkeypatch.setattr(engine, "_try_ffmpeg_pipe", lambda u: (False, "no video"))
    ok, steps, fixed = engine.probe_rtsp(url, wait=0.1)
    names = [s[0] for s in steps]
    assert ok is False and "RTSP handshake" in names and "RTSP port" in names
    hs = dict((s[0], s) for s in steps)
    assert hs["RTSP handshake"][1] is False and "IP LOCK" in hs["RTSP handshake"][2] and "turant kaat deta hai" in hs["RTSP handshake"][2]
    assert hs["RTSP port"][1] is True and "8554" in hs["RTSP port"][2]
    assert ":8554/cam/realmonitor?channel=1&subtype=0" in fixed and "Admin%40123" in fixed
    assert any(":8554/" in c for c in calls)  # re-described on the new port (401 -> auth hint path)


def test_probe_reports_no_alternate_port(monkeypatch):
    url = "rtsp://admin:pw@192.168.31.180:554/video"
    monkeypatch.setattr(socket, "create_connection", lambda addr, timeout=3: type("S", (), {"close": lambda self: None})())
    monkeypatch.setattr(rd, "rtsp_describe", lambda u, timeout=3.0: (0, "timed out"))
    monkeypatch.setattr(rd, "find_rtsp_port", lambda host, user, pw, path, ports=None, timeout=1.5: (0, 0))
    monkeypatch.setattr(engine, "_try_capture", lambda u, transport, wait: (False, "no video"))
    monkeypatch.setattr(engine, "_try_ffmpeg_pipe", lambda u: (False, "no video"))
    ok, steps, fixed = engine.probe_rtsp(url, wait=0.1)
    hs = dict((s[0], s) for s in steps)
    assert hs["RTSP port"][1] is False and "8554, 10554" in hs["RTSP port"][2] and fixed == url


# ---------------- lock-aware backoff ----------------
def _worker():
    w = service.Worker("cam1")
    w._log = lambda m: None
    return w


def test_backoff_after_two_rejections_and_cleared_by_connect(monkeypatch):
    w = _worker()
    w.open_error, w.status = "auth", "ERROR: 401"
    w._set_backoff()
    assert w.backoff_until == 0.0 and w._reject_count == 1  # one failure: could be a glitch
    w._set_backoff()
    assert w.backoff_until > service.time.time() + 500 and "10 min" in w.status and "Connect dabayein" in w.status
    # pool.sync(auto=True) must not restart it while backing off
    started = []
    monkeypatch.setattr(w, "start", lambda: started.append(1))
    monkeypatch.setattr(service.pool, "get", lambda cid: w)
    cfg = {"cameras": [{"id": "cam1", "name": "Cam", "rtsp_url": "rtsp://u:p@1.2.3.4/x", "monitor": True}]}
    assert service.pool.sync(cfg, auto=True) == [] and started == []
    w.backoff_until = 0.0  # what /api/camera/connect does
    assert service.pool.sync(cfg, auto=True) == ["Cam"] and started == [1]


def test_backoff_for_connection_reset_is_5_min_and_other_errors_reset_counter():
    w = _worker()
    w.open_error, w.open_detail, w.status = "no-answer", RST, "ERROR: reset"
    w._set_backoff()
    w._set_backoff()
    assert 290 < w.backoff_until - service.time.time() <= 300 and "5 min" in w.status
    w2 = _worker()
    w2.open_error, w2.status = "auth", "x"
    w2._set_backoff()
    w2.open_error = "none"  # a different failure in between: not "the camera rejecting us"
    w2._set_backoff()
    assert w2._reject_count == 0 and w2.backoff_until == 0.0
    w3 = _worker()
    w3.open_error, w3.open_detail, w3.status = "no-answer", "timed out", "x"  # silent host: never a lock issue
    w3._set_backoff()
    w3._set_backoff()
    assert w3.backoff_until == 0.0


def test_connect_endpoint_clears_backoff(monkeypatch, tmp_path):
    import time

    import config
    from fastapi.testclient import TestClient

    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    cfg = {**config.DEFAULTS, "cameras": [{"id": "cam1", "name": "Cam", "rtsp_url": "rtsp://u:p@192.0.2.9/x", "monitor": True}],
           "active_camera_id": "cam1", "rtsp_url": "rtsp://u:p@192.0.2.9/x"}
    config.save_config(cfg)
    w = service.pool.get("cam1")
    w.backoff_until, w._reject_count = time.time() + 600, 2
    monkeypatch.setattr(w, "start", lambda: None)
    monkeypatch.setattr(service.pool, "active", lambda c=None: w)
    tok = "t-connect"
    service._tokens[tok] = time.time()
    c = TestClient(service.app)
    r = c.post("/api/camera/connect", json={"url": "rtsp://u:p@192.0.2.9/x"}, headers={"X-Auth-Token": tok})
    assert r.status_code == 200 and w.backoff_until == 0.0 and w._reject_count == 0
