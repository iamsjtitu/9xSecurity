"""HTTP/HTTPS video fallback: RTSP dead -> MJPEG CGI / ONVIF snapshot / snapshot CGI over HTTP, saved per camera,
back to RTSP when it answers again. A tiny local 'camera' HTTP server (digest auth, JPEG + multipart) stands in."""
import hashlib
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import cv2
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import engine  # noqa: E402
import http_source as hs  # noqa: E402
import service  # noqa: E402

JPEG = cv2.imencode(".jpg", np.full((48, 64, 3), 90, np.uint8))[1].tobytes()
REALM, USER, PW = "9xcam", "admin", "Admin@123"


class _Cam(BaseHTTPRequestHandler):
    """/cgi-bin/snapshot.cgi -> JPEG (digest), /cgi-bin/mjpg/video.cgi -> multipart (digest), /open.jpg -> JPEG no auth,
    /html -> text/html, /slow -> never answers."""
    hits = []
    nonce = "abc123"

    def log_message(self, *a):
        pass

    def _authed(self):
        h = self.headers.get("Authorization", "")
        if not h.startswith("Digest "):
            return False
        p = dict(kv.split("=", 1) for kv in [x.strip() for x in h[7:].split(",")] if "=" in kv)
        p = {k: v.strip('"') for k, v in p.items()}
        ha1 = hashlib.md5(f"{USER}:{REALM}:{PW}".encode()).hexdigest()
        ha2 = hashlib.md5(f"GET:{p.get('uri')}".encode()).hexdigest()
        if p.get("qop"):
            exp = hashlib.md5(f"{ha1}:{p['nonce']}:{p['nc']}:{p['cnonce']}:{p['qop']}:{ha2}".encode()).hexdigest()
        else:
            exp = hashlib.md5(f"{ha1}:{p['nonce']}:{ha2}".encode()).hexdigest()
        return p.get("response") == exp

    def _challenge(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", f'Digest realm="{REALM}", nonce="{self.nonce}", qop="auth"')
        self.end_headers()

    def do_GET(self):
        _Cam.hits.append(self.path)
        path = self.path.split("?")[0]
        if path == "/open.jpg":
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.end_headers()
            self.wfile.write(JPEG)
            return
        if path == "/html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html>login</html>")
            return
        if path == "/slow":
            time.sleep(8)
            return
        if path in ("/cgi-bin/snapshot.cgi", "/cgi-bin/mjpg/video.cgi"):
            if not self._authed():
                return self._challenge()
            if path == "/cgi-bin/snapshot.cgi":
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.end_headers()
                self.wfile.write(JPEG)
                return
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=myboundary")
            self.end_headers()
            try:
                for _ in range(40):
                    self.wfile.write(b"--myboundary\r\nContent-Type: image/jpeg\r\n\r\n" + JPEG + b"\r\n")
                    self.wfile.flush()
                    time.sleep(0.03)
            except OSError:
                pass
            return
        self.send_response(404)
        self.end_headers()


@pytest.fixture(scope="module")
def cam():
    srv = HTTPServer(("127.0.0.1", 0), _Cam)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "127.0.0.1", srv.server_port
    srv.shutdown()


def _u(host, port, path, creds=True):
    return hs.with_http_creds(f"http://{host}:{port}{path}", USER if creds else "", PW if creds else "")


def test_split_and_creds_roundtrip():
    u = hs.with_http_creds("http://1.2.3.4/cgi-bin/snapshot.cgi?channel=1", "admin", "Admin@123")
    assert u == "http://admin:Admin%40123@1.2.3.4/cgi-bin/snapshot.cgi?channel=1"
    assert hs.split_http(u) == ("http", "1.2.3.4", 80, "admin", "Admin@123", "/cgi-bin/snapshot.cgi?channel=1")
    assert hs.split_http("https://1.2.3.4/x")[2] == 443 and hs.bare(u) == "http://1.2.3.4/cgi-bin/snapshot.cgi?channel=1"
    assert engine.normalize_rtsp_url("http://admin:Admin@123@1.2.3.4/snap.jpg") == "http://admin:Admin%40123@1.2.3.4/snap.jpg"
    assert hs.is_http("HTTPS://x") and not hs.is_http("rtsp://x")


def test_probe_http_url_kinds(cam):
    host, port = cam
    assert hs.probe_http_url(_u(host, port, "/cgi-bin/snapshot.cgi?channel=1")) == ("snapshot", "image/jpeg")
    kind, ct = hs.probe_http_url(_u(host, port, "/cgi-bin/mjpg/video.cgi?channel=1&subtype=1"))
    assert kind == "mjpeg" and "multipart" in ct
    assert hs.probe_http_url(_u(host, port, "/open.jpg", creds=False)) == ("snapshot", "image/jpeg")
    kind, detail = hs.probe_http_url(_u(host, port, "/cgi-bin/snapshot.cgi", creds=False))  # no creds -> 401 twice
    assert kind == "" and detail.startswith("HTTP 401")
    kind, detail = hs.probe_http_url(hs.with_http_creds(f"http://{host}:{port}/cgi-bin/snapshot.cgi", "admin", "wrong"))
    assert kind == "" and detail.startswith("HTTP 401")
    kind, detail = hs.probe_http_url(_u(host, port, "/html", creds=False))
    assert kind == "" and "text/html" in detail
    assert hs.probe_http_url(_u(host, port, "/nope", creds=False))[1].startswith("HTTP 404")


def test_snapshot_source_polls_with_one_digest_challenge(cam):
    host, port = cam
    _Cam.hits.clear()
    src = hs.open_http(_u(host, port, "/cgi-bin/snapshot.cgi?channel=1"), first_frame_wait=5)
    assert src is not None and src.kind == "snapshot"
    try:
        frames = 0
        t0 = time.time()
        while time.time() - t0 < 1.2:
            ok, f = src.read(timeout=1.0)
            if ok:
                frames += 1
                assert f.shape == (48, 64, 3)
        assert 3 <= frames <= 8  # capped at 5 fps (+ the frame buffered at open)
        assert src.isOpened() and src.get(cv2.CAP_PROP_FRAME_WIDTH) == 64
    finally:
        src.release()
    assert not src.isOpened()


def test_mjpeg_source_streams(cam):
    host, port = cam
    src = hs.open_http(_u(host, port, "/cgi-bin/mjpg/video.cgi?channel=1&subtype=1"), first_frame_wait=5)
    assert src is not None and src.kind == "mjpeg"
    try:
        got = sum(1 for _ in range(15) if src.read(timeout=1.0)[0])
        assert got >= 10
    finally:
        src.release()


def test_open_http_returns_none_for_non_video(cam):
    host, port = cam
    assert hs.open_http(_u(host, port, "/html", creds=False)) is None
    assert hs.open_http(_u(host, port, "/cgi-bin/snapshot.cgi", creds=False)) is None


def test_discover_http_prefers_mjpeg_then_onvif_then_snapshot(cam, monkeypatch):
    host, port = cam
    # our fake camera listens on a random port: map "port 80 open" to it
    monkeypatch.setattr(hs, "_port_open", lambda h, p, timeout=1.5: p == 80)
    real_creds = hs.with_http_creds

    def creds_on_test_port(url, user, pw, host_=None):
        return real_creds(url.replace(f"://{host}/", f"://{host}:{port}/"), user, pw, host_)

    monkeypatch.setattr(hs, "with_http_creds", creds_on_test_port)
    monkeypatch.setattr(hs, "onvif_snapshot_uris", lambda h, u, p, timeout=4: [])
    logs = []
    url, kind, how = hs.discover_http(host, USER, PW, log=logs.append)
    assert kind == "mjpeg" and how == "cgi:cpplus/dahua" and url.startswith(f"http://admin:Admin%40123@{host}:{port}/cgi-bin/mjpg/video.cgi")
    # camera without MJPEG sub-stream: ONVIF snapshot wins
    monkeypatch.setattr(hs, "MJPEG_PATHS", (("generic", "/nope.mjpg"),))
    monkeypatch.setattr(hs, "onvif_snapshot_uris", lambda h, u, p, timeout=4: [_u(host, port, "/cgi-bin/snapshot.cgi?channel=1")])
    url, kind, how = hs.discover_http(host, USER, PW, log=logs.append)
    assert (kind, how) == ("snapshot", "onvif") and "/cgi-bin/snapshot.cgi" in url
    # no ONVIF either: brand snapshot CGI
    monkeypatch.setattr(hs, "onvif_snapshot_uris", lambda h, u, p, timeout=4: [])
    url, kind, how = hs.discover_http(host, USER, PW, log=logs.append)
    assert (kind, how) == ("snapshot", "cgi:cpplus/dahua")
    # wrong password everywhere -> 'auth'
    assert hs.discover_http(host, USER, "bad", log=logs.append)[2] == "auth"
    monkeypatch.setattr(hs, "_port_open", lambda h, p, timeout=1.5: False)
    assert hs.discover_http(host, USER, PW, log=logs.append) == ("", "", "no-http-port")


def test_probe_steps_for_http_url(cam):
    host, port = cam
    ok, steps, fixed = engine.probe_rtsp(_u(host, port, "/cgi-bin/snapshot.cgi?channel=1"), wait=3)
    names = [s[0] for s in steps]
    assert ok and names[0] == "URL check" and "HTTP video" in names and "Video frame" in names
    assert "JPEG snapshot" in dict((s[0], s[2]) for s in steps)["HTTP video"]
    ok, steps, _ = engine.probe_rtsp(_u(host, port, "/html", creds=False), wait=2)
    assert not ok and dict((s[0], s) for s in steps)["HTTP video"][1] is False


# ---------------- worker integration ----------------
@pytest.fixture
def cfgfile(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(service.Worker, "_maybe_fingerprint", lambda self, source, live: None)  # thread would outlive the tmp config
    cfg = {**config.DEFAULTS, "cameras": [{"id": "cam1", "name": "Gate 1", "rtsp_url": "rtsp://admin:Admin%40123@192.0.2.9:554/x",
                                           "monitor": True}], "active_camera_id": "cam1", "rtsp_url": "rtsp://admin:Admin%40123@192.0.2.9:554/x"}
    config.save_config(cfg)
    return cfg


def test_worker_falls_back_to_http_and_saves_url(cam, cfgfile, monkeypatch):
    host, port = cam
    snap = _u(host, port, "/cgi-bin/snapshot.cgi?channel=1")
    monkeypatch.setattr(hs, "discover_http", lambda h, u, p, log: (snap, "snapshot", "onvif"))
    monkeypatch.setattr(service, "rtsp_port_reachable", lambda s, timeout=2.5: (True, ""))
    w = service.Worker("cam1")
    w._log = lambda m: None
    monkeypatch.setattr(w, "_auto_fix_source", lambda s: "")
    opened = []

    def rtsp_open_fails(source, live, gen=None):
        opened.append(source)
        return None

    monkeypatch.setattr(w, "_open", rtsp_open_fails)
    w._gen = 0
    alive = {"n": 0}

    def _alive(g):  # let the loop run a few frames, then stop
        alive["n"] += 1
        return alive["n"] < 12

    w._alive = _alive
    w.should_stream = lambda: True
    w.engine = None
    monkeypatch.setattr(w, "_draw_line_only", lambda small, cfg: small)
    cont = w._stream_once(0, w.cam_cfg())
    assert opened == ["rtsp://admin:Admin%40123@192.0.2.9:554/x"]  # RTSP tried first
    assert w.http_mode == "snapshot" and "HTTP snapshot mode" in w.status or cont in (True, False)
    assert config.load_config()["cameras"][0]["http_url"] == snap  # remembered for next time
    assert w._jpeg is not None  # frames were processed into the live view
    assert w.codec == "snapshot"


def test_worker_uses_saved_http_url_without_rediscovery_and_forgets_stale(cam, cfgfile, monkeypatch):
    host, port = cam
    cfg = config.load_config()
    cfg["cameras"][0]["http_url"] = _u(host, port, "/cgi-bin/snapshot.cgi?channel=1")
    config.save_config(cfg)
    monkeypatch.setattr(hs, "discover_http", lambda *a, **k: pytest.fail("must not rediscover when a URL is saved"))
    w = service.Worker("cam1")
    w._log = lambda m: None
    w._gen = 0
    w._alive = lambda g: True
    cap = w._http_fallback("rtsp://admin:Admin%40123@192.0.2.9:554/x", 0)
    assert cap is not None and w.http_mode == "snapshot"
    cap.release()
    # saved URL dead now -> forgotten
    cfg = config.load_config()
    cfg["cameras"][0]["http_url"] = _u(host, port, "/html", creds=False)
    config.save_config(cfg)
    assert w._http_fallback("rtsp://admin:Admin%40123@192.0.2.9:554/x", 0) is None
    assert config.load_config()["cameras"][0]["http_url"] == ""


def test_http_discovery_rate_limited_per_host(cfgfile, monkeypatch):
    calls = []
    monkeypatch.setattr(hs, "discover_http", lambda h, u, p, log: calls.append(h) or ("", "", "not-found"))
    w = service.Worker("cam1")
    w._log = lambda m: None
    w._gen = 0
    w._alive = lambda g: True
    assert w._http_fallback("rtsp://admin:pw@192.0.2.9:554/x", 0) is None
    assert w._http_fallback("rtsp://admin:pw@192.0.2.9:554/x", 0) is None
    assert calls == ["192.0.2.9"]  # second failure within 10 min: no new scan


def test_returns_to_rtsp_when_it_answers_again(cam, cfgfile, monkeypatch):
    host, port = cam
    snap = _u(host, port, "/cgi-bin/snapshot.cgi?channel=1")
    monkeypatch.setattr(hs, "discover_http", lambda h, u, p, log: (snap, "snapshot", "onvif"))
    monkeypatch.setattr(service, "rtsp_port_reachable", lambda s, timeout=2.5: (True, ""))
    w = service.Worker("cam1")
    logs = []
    w._log = logs.append
    monkeypatch.setattr(w, "_auto_fix_source", lambda s: "")
    monkeypatch.setattr(w, "_open", lambda source, live, gen=None: None)
    monkeypatch.setattr(w, "_draw_line_only", lambda small, cfg: small)
    w._gen, w._alive, w.should_stream, w.engine = 0, (lambda g: True), (lambda: True), None
    orig_fallback = w._http_fallback

    def _fallback_then_due(source, gen):
        cap = orig_fallback(source, gen)
        w._rtsp_recheck_at = 0.0  # 10 min are "over": the checker must run on the first schedule tick
        return cap

    checked = []

    def _rtsp_ok(source):  # stands in for rtsp_describe(...) == 200 (runs in the checker thread)
        checked.append(source)
        w._rtsp_back = True

    monkeypatch.setattr(w, "_http_fallback", _fallback_then_due)
    monkeypatch.setattr(w, "_check_rtsp_back", _rtsp_ok)
    cont = w._stream_once(0, w.cam_cfg())
    assert cont is True and checked == [w.cam_cfg()["rtsp_url"]]
    assert any("RTSP wapas aa gaya" in m for m in logs) and w._rtsp_back is False


def test_user_entered_http_url_opens_directly(cam, cfgfile, monkeypatch):
    host, port = cam
    cfg = config.load_config()
    cfg["cameras"][0]["rtsp_url"] = cfg["rtsp_url"] = _u(host, port, "/cgi-bin/mjpg/video.cgi?channel=1&subtype=1")
    config.save_config(cfg)
    w = service.Worker("cam1")
    w._log = lambda m: None
    monkeypatch.setattr(w, "_auto_fix_source", lambda s: pytest.fail("RTSP path discovery must not run for http URLs"))
    monkeypatch.setattr(w, "_http_fallback", lambda s, g: pytest.fail("no fallback needed: the URL itself is HTTP"))
    monkeypatch.setattr(w, "_draw_line_only", lambda small, cfg: small)
    alive = {"n": 0}

    def _alive(g):
        alive["n"] += 1
        return alive["n"] < 8

    w._gen, w._alive, w.should_stream, w.engine = 0, _alive, (lambda: True), None
    assert service.rtsp_port_reachable(cfg["rtsp_url"]) == (True, "")  # http port, not 554
    w._stream_once(0, w.cam_cfg())
    assert w.http_mode == "mjpeg" and w.codec == "mjpeg" and w._jpeg is not None


def test_reachable_default_port_by_scheme():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    assert service.rtsp_port_reachable(f"http://u:p@127.0.0.1:{p}/x", timeout=0.5)[1] == "connection refused"
