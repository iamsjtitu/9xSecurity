"""Update download progress + cancel (background job)."""
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import updater  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    import service

    tok = "p" * 48
    service._tokens[tok] = time.time()
    c = TestClient(service.app)
    c.headers["X-Auth-Token"] = tok
    monkeypatch.setattr(updater, "DEFAULT_REPO", "o/r")
    monkeypatch.setattr(updater, "check_latest", lambda repo, token=None: ("9.9.9", "http://x/setup.exe", ""))
    service._update_job.update(state="idle")
    return c


def _fake_download(total=10, step=0.05):
    def dl(asset_url, dest, progress=None, token=None, should_stop=None):
        with open(dest, "wb") as f:
            for i in range(1, total + 1):
                if should_stop and should_stop():
                    return None
                f.write(b"x" * 1000)
                progress(i * 1000, total * 1000)
                time.sleep(step)
        return dest
    return dl


def test_progress_reported_and_finishes(client, monkeypatch):
    import service

    monkeypatch.setattr(updater, "download", _fake_download())
    monkeypatch.setattr(updater, "apply_update", lambda p: False)  # dev mode
    j = client.post("/api/update/apply").json()
    assert j["state"] in ("checking", "downloading")
    seen = set()
    for _ in range(60):
        j = client.get("/api/update/progress").json()
        seen.add(j["state"])
        if j["state"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert j["state"] == "done", j
    assert j["percent"] == 100 and j["total"] == 10000 and j["latest"] == "9.9.9"
    assert "downloading" in seen
    assert client.get("/api/state").json()["update_job"]["state"] == "done"
    assert "Dev mode" in j["message"]


def test_second_apply_returns_running_job_and_cancel_works(client, monkeypatch):
    import service

    monkeypatch.setattr(updater, "download", _fake_download(total=100, step=0.05))
    launched = []
    monkeypatch.setattr(updater, "apply_update", lambda p: launched.append(p) or True)
    client.post("/api/update/apply")
    time.sleep(0.3)
    j2 = client.post("/api/update/apply").json()  # no duplicate job
    assert j2["state"] == "downloading" and 0 < j2["percent"] < 100
    assert client.post("/api/update/cancel").json()["ok"] is True
    for _ in range(40):
        j = client.get("/api/update/progress").json()
        if j["state"] == "cancelled":
            break
        time.sleep(0.05)
    assert j["state"] == "cancelled" and not launched
    assert client.post("/api/update/cancel").json()["ok"] is False  # nothing to cancel


def test_download_error_reported(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("HTTP 404 asset")

    monkeypatch.setattr(updater, "download", boom)
    client.post("/api/update/apply")
    for _ in range(40):
        j = client.get("/api/update/progress").json()
        if j["state"] == "error":
            break
        time.sleep(0.05)
    assert j["state"] == "error" and "404" in j["message"]


def test_updater_download_stop_and_progress_without_total(monkeypatch, tmp_path):
    class R:
        headers = {}
        content = b""

        def raise_for_status(self):
            pass

        def iter_content(self, n):
            for _ in range(5):
                yield b"a" * 10

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(updater.requests, "get", lambda *a, **k: R())
    calls = []
    dest = str(tmp_path / "f.bin")
    assert updater.download("u", dest, progress=lambda r, t: calls.append((r, t))) == dest
    assert calls[-1] == (50, 0)  # progress reported even when Content-Length unknown
    stop = {"n": 0}

    def should_stop():
        stop["n"] += 1
        return stop["n"] > 2

    assert updater.download("u", dest, should_stop=should_stop) is None
