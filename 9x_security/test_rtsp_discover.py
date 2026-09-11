"""USER (11-09): rtsp://admin:Admin@123@192.168.1.28:554/ — 'mill entry system' software plays it,
9x Security did not: camera answered DESCRIBE 404 for '/' (no stream path). The app must ask the
camera for the real path itself (ONVIF / known vendor paths) instead of failing."""
import hashlib
import os
import re
import socket
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import engine  # noqa: E402
import rtsp_discover as rd  # noqa: E402

USER, PW, REALM, NONCE = "admin", "Admin@123", "IP Camera", "abc123"
GOOD_PATH = "/Streaming/Channels/101"


class FakeCam(threading.Thread):
    """Minimal RTSP server: Digest auth, 200 only for GOOD_PATH, 404 elsewhere."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.requests = []
        self._stop = False

    def run(self):
        while not self._stop:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(c,), daemon=True).start()

    def _handle(self, c):
        try:
            c.settimeout(3)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = c.recv(4096)
                if not chunk:
                    break
                data += chunk
            text = data.decode("latin-1")
            m = re.match(r"(\w+)\s+(\S+)\s+RTSP", text)
            if not m:
                return  # reachability check (connect + close) sends nothing
            method, uri = m.group(1), m.group(2)
            self.requests.append(uri)
            cseq = re.search(r"CSeq:\s*(\d+)", text).group(1)
            auth = re.search(r"Authorization:\s*Digest\s+([^\r\n]+)", text)
            if not auth:
                c.sendall(f'RTSP/1.0 401 Unauthorized\r\nCSeq: {cseq}\r\nWWW-Authenticate: Digest realm="{REALM}", nonce="{NONCE}"\r\n\r\n'.encode())
                return
            p = dict(re.findall(r'(\w+)="([^"]*)"', auth.group(1)))
            ha1 = hashlib.md5(f"{USER}:{REALM}:{PW}".encode()).hexdigest()
            ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
            expect = hashlib.md5(f"{ha1}:{NONCE}:{ha2}".encode()).hexdigest()
            if p.get("username") != USER or p.get("response") != expect:
                c.sendall(f"RTSP/1.0 401 Unauthorized\r\nCSeq: {cseq}\r\n\r\n".encode())
                return
            path = uri.split(f":{self.port}", 1)[1] if f":{self.port}" in uri else "/"
            if path == GOOD_PATH:
                c.sendall(f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\nContent-Type: application/sdp\r\nContent-Length: 0\r\n\r\n".encode())
            else:
                c.sendall(f"RTSP/1.0 404 Not Found\r\nCSeq: {cseq}\r\n\r\n".encode())
        finally:
            c.close()

    def stop(self):
        self._stop = True
        self.sock.close()


@pytest.fixture(scope="module")
def cam():
    s = FakeCam()
    s.start()
    yield s
    s.stop()


def _url(cam, pw=PW, path="/"):
    return engine.normalize_rtsp_url(f"rtsp://{USER}:{pw}@127.0.0.1:{cam.port}{path}")


def test_describe_tells_path_vs_password_apart(cam):
    assert rd.rtsp_describe(_url(cam, path="/"))[0] == 404
    assert rd.rtsp_describe(_url(cam, path=GOOD_PATH))[0] == 200
    assert rd.rtsp_describe(_url(cam, pw="wrong", path=GOOD_PATH))[0] == 401
    assert rd.rtsp_describe("rtsp://u:p@127.0.0.1:1/x", timeout=1)[0] == 0  # nothing listening


def test_discover_finds_vendor_path_and_keeps_encoded_password(cam, monkeypatch):
    monkeypatch.setattr(rd, "onvif_stream_uris", lambda *a, **k: [])  # camera without ONVIF
    found, why = rd.discover_stream_url(_url(cam), log=print)
    assert why == "common-path" and found.endswith(GOOD_PATH)
    assert "Admin%40123@127.0.0.1" in found  # '@' in the password stays encoded
    assert rd.rtsp_describe(found)[0] == 200


def test_discover_prefers_onvif_uri(cam, monkeypatch):
    monkeypatch.setattr(rd, "onvif_stream_uris",
                        lambda host, user, pw, timeout=4: [f"rtsp://{host}:{cam.port}{GOOD_PATH}", f"rtsp://{host}:{cam.port}/Streaming/Channels/102"])
    found, why = rd.discover_stream_url(_url(cam))
    assert why == "onvif" and found.endswith(GOOD_PATH) and "Admin%40123@" in found


def test_discover_stops_on_wrong_password(cam, monkeypatch):
    monkeypatch.setattr(rd, "onvif_stream_uris", lambda *a, **k: [])
    found, why = rd.discover_stream_url(_url(cam, pw="wrong"))
    assert found == "" and why == "auth"
    assert len([r for r in cam.requests if r.endswith(rd.COMMON_PATHS[0])]) >= 1  # gave up after the first 401


def test_probe_auto_detects_path_and_returns_fixed_url(cam, monkeypatch):
    monkeypatch.setattr(rd, "onvif_stream_uris", lambda *a, **k: [])
    ok, steps, url = engine.probe_rtsp(f"rtsp://{USER}:{PW}@127.0.0.1:{cam.port}/", wait=1.0)
    names = [s[0] for s in steps]
    assert "RTSP handshake" in names and "Stream path auto-detect" in names
    hs = steps[names.index("RTSP handshake")]
    ad = steps[names.index("Stream path auto-detect")]
    assert hs[1] is False and "404" in hs[2]
    assert ad[1] is True and GOOD_PATH in ad[2]
    assert url.endswith(GOOD_PATH) and "Admin%40123@" in url
    assert not ok  # the fake camera sends no video, but the URL is fixed for the user


def test_probe_reports_wrong_password_clearly(cam):
    ok, steps, url = engine.probe_rtsp(f"rtsp://{USER}:wrong@127.0.0.1:{cam.port}/", wait=1.0)
    assert not ok and steps[-1][0] == "RTSP handshake" and "401" in steps[-1][2]


def test_auto_fix_stream_url_for_worker(cam, monkeypatch):
    monkeypatch.setattr(rd, "onvif_stream_uris", lambda *a, **k: [])
    fixed, why = engine.auto_fix_stream_url(_url(cam), log=lambda m: None)
    assert why == "common-path" and fixed.endswith(GOOD_PATH)
    assert engine.auto_fix_stream_url(_url(cam, path=GOOD_PATH), log=lambda m: None) == ("", "path-ok")
    assert engine.auto_fix_stream_url(_url(cam, pw="wrong"), log=lambda m: None) == ("", "auth")


def test_brand_url_builder():
    u = rd.build_url("hikvision", "192.168.1.28", "admin", "Admin@123")
    assert u == "rtsp://admin:Admin%40123@192.168.1.28:554/Streaming/Channels/101"
    assert rd.build_url("hikvision", "192.168.1.28", "admin", "x", channel=3, stream="sub").endswith("/Streaming/Channels/302")
    assert rd.build_url("dahua", "10.0.0.9", "admin", "p", channel=2, stream="sub").endswith("/cam/realmonitor?channel=2&subtype=1")
    x = rd.build_url("xmeye", "10.0.0.9", "admin", "a@b", stream="main")
    assert "/user=admin_password=a%40b_channel=1_stream=0.sdp?real_stream" in x
    assert rd.build_url("custom", "10.0.0.9", "u", "p", port=8554, custom_path="live/{weird}") == "rtsp://u:p@10.0.0.9:8554/live/{weird}"
    assert rd.build_url("auto", "10.0.0.9", "", "") == "rtsp://10.0.0.9:554/"
    # every template renders and normalizes idempotently
    for b in rd.BRANDS:
        u = rd.build_url(b["id"], "1.2.3.4", "admin", "P@ss:1/2", channel=1, stream="main", custom_path="/x")
        assert u.startswith("rtsp://admin:P%40ss%3A1%2F2@1.2.3.4:554/"), u
        assert engine.normalize_rtsp_url(u) == u


def test_with_creds_and_with_path_helpers():
    u = rd.with_creds("rtsp://0.0.0.0:554/cam/realmonitor?channel=1&subtype=0", "admin", "Ad@1", host="10.0.0.5")
    assert u == "rtsp://admin:Ad%401@10.0.0.5:554/cam/realmonitor?channel=1&subtype=0"
    assert rd.with_path("rtsp://a:b@h:554/", "/x?y=1") == "rtsp://a:b@h:554/x?y=1"
    assert not rd.has_stream_path("rtsp://a:b@h:554/") and rd.has_stream_path("rtsp://a:b@h:554/s")
