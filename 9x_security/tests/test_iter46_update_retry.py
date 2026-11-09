"""Update download: GitHub 504 'Gateway Time-out' must not kill the update — retry, switch endpoint, resume."""
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import updater  # noqa: E402

PAYLOAD = bytes(range(256)) * 4000  # ~1 MB "installer"


class _GH(BaseHTTPRequestHandler):
    """/browser -> behaves per the scripted `plan` (list of actions consumed per request);
    /api -> serves the file (Range aware)."""
    plan = []
    hits = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        _GH.hits.append((self.path, self.headers.get("Range")))
        action = _GH.plan.pop(0) if _GH.plan else "ok"
        if action == "504":
            self.send_response(504)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Gateway Time-out</h1>")
            return
        if action == "drop_half":  # send half the file then cut the connection
            self.send_response(200)
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            self.wfile.write(PAYLOAD[: len(PAYLOAD) // 2])
            self.wfile.flush()
            self.connection.close()
            return
        start = 0
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            start = int(rng[6:].split("-")[0])
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}")
        else:
            self.send_response(200)
        body = PAYLOAD[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), _GH)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_port}"
    yield base
    srv.shutdown()


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(updater, "RETRY_WAIT", (0.05, 0.05, 0.05, 0.05, 0.05))
    _GH.plan, _GH.hits = [], []


def test_504_then_alternate_endpoint_succeeds(server, tmp_path):
    """Exactly the user's failure: github.com answers 504 -> next attempt uses the api asset url -> OK."""
    _GH.plan = ["504"]
    msgs = []
    dest = str(tmp_path / "setup.exe")
    out = updater.download([f"{server}/browser", f"{server}/api"], dest, status=msgs.append)
    assert out == dest and open(dest, "rb").read() == PAYLOAD
    assert [h[0] for h in _GH.hits] == ["/browser", "/api"]
    assert len(msgs) == 1 and "HTTP 504" in msgs[0] and "dobara koshish (2/6)" in msgs[0]


def test_connection_drop_resumes_with_range(server, tmp_path):
    _GH.plan = ["drop_half"]
    dest = str(tmp_path / "setup.exe")
    prog = []
    out = updater.download(f"{server}/browser", dest, progress=lambda r, t: prog.append((r, t)))
    assert out == dest and open(dest, "rb").read() == PAYLOAD
    got1 = int(_GH.hits[1][1].split("=")[1].rstrip("-"))  # resumed from the bytes actually received, not restarted
    assert _GH.hits[0][1] is None and 0 < got1 <= len(PAYLOAD) // 2
    assert prog[-1] == (len(PAYLOAD), len(PAYLOAD)) and all(t == len(PAYLOAD) for _, t in prog)


def test_gives_up_after_all_attempts_with_helpful_message(server, tmp_path):
    _GH.plan = ["504"] * 6
    with pytest.raises(RuntimeError) as ei:
        updater.download(f"{server}/browser", str(tmp_path / "s.exe"), attempts=6)
    assert "6 baar" in str(ei.value) and "HTTP 504" in str(ei.value) and "Browser me download" in str(ei.value)
    assert len(_GH.hits) == 6


def test_cancel_during_retry_wait(server, tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "RETRY_WAIT", (5, 5, 5, 5, 5))
    _GH.plan = ["504"]
    stop = {"v": False}

    def status(_):
        stop["v"] = True  # user hits Cancel while we wait to retry

    out = updater.download(f"{server}/browser", str(tmp_path / "s.exe"), should_stop=lambda: stop["v"], status=status)
    assert out is None and len(_GH.hits) == 1


def test_404_is_not_retried(server, tmp_path):
    class NF(_GH):
        def do_GET(self):
            _GH.hits.append((self.path, None))
            self.send_response(404)
            self.end_headers()

    srv = HTTPServer(("127.0.0.1", 0), NF)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        import requests

        with pytest.raises(requests.HTTPError):
            updater.download(f"http://127.0.0.1:{srv.server_port}/x", str(tmp_path / "s.exe"))
        assert len(_GH.hits) == 1
    finally:
        srv.shutdown()


def test_release_assets_public_and_private(monkeypatch):
    data = {"tag_name": "v1.0.49", "html_url": "http://rel", "assets": [
        {"name": "9xSecuritySetup-v1.0.49.exe", "browser_download_url": "http://gh/dl.exe", "url": "http://api/assets/1"}]}
    monkeypatch.setattr(updater, "_api", lambda url, token=None: (200, data))
    tag, urls, page = updater.release_assets("o/n")
    assert tag == "1.0.49" and urls == ["http://gh/dl.exe", "http://api/assets/1"] and page == "http://rel"
    tag, urls, _ = updater.release_assets("o/n", token="ghp_x")
    assert urls == ["http://api/assets/1"]  # private: browser url useless without a session
    assert updater.check_latest("o/n") == ("1.0.49", "http://gh/dl.exe", "http://rel")
    monkeypatch.setattr(updater, "_api", lambda url, token=None: (404, {}))
    assert updater.release_assets("o/n") == ("", [], "") and updater.check_latest("o/n") == ("", None, "")
