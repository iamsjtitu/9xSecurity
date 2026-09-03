"""Sub-stream one-click switch: URL derivation for common brands + endpoints + state suggestion."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import service  # noqa: E402
from engine import substream_url  # noqa: E402


@pytest.mark.parametrize("main,sub", [
    ("rtsp://admin:Bittu@123@192.168.1.64:554/Streaming/Channels/101", "rtsp://admin:Bittu@123@192.168.1.64:554/Streaming/Channels/102"),
    ("rtsp://u:p@10.0.0.5/Streaming/Channels/201/", "rtsp://u:p@10.0.0.5/Streaming/Channels/202/"),
    ("rtsp://u:p@10.0.0.5:554/Streaming/Channels/101?transportmode=unicast", "rtsp://u:p@10.0.0.5:554/Streaming/Channels/102?transportmode=unicast"),
    ("rtsp://u:p@cam/h264/ch1/main/av_stream", "rtsp://u:p@cam/h264/ch1/sub/av_stream"),
    ("rtsp://admin:pw@192.168.1.108:554/cam/realmonitor?channel=1&subtype=0", "rtsp://admin:pw@192.168.1.108:554/cam/realmonitor?channel=1&subtype=1"),
    ("rtsp://admin:pw@192.168.1.108:554/cam/realmonitor?channel=1", "rtsp://admin:pw@192.168.1.108:554/cam/realmonitor?channel=1&subtype=1"),
    ("rtsp://u:p@cam/h264Preview_01_main", "rtsp://u:p@cam/h264Preview_01_sub"),
    ("rtsp://u:p@cam/media/video1", "rtsp://u:p@cam/media/video2"),
    ("rtsp://u:p@cam:554/stream1", "rtsp://u:p@cam:554/stream2"),
    ("rtsp://u:p@cam/live/main", "rtsp://u:p@cam/live/sub"),
])
def test_substream_url_brands(main, sub):
    assert substream_url(main) == sub


@pytest.mark.parametrize("already", [
    "rtsp://u:p@cam/Streaming/Channels/102",
    "rtsp://u:p@cam/cam/realmonitor?channel=1&subtype=1",
    "rtsp://u:p@cam/h264/ch1/sub/av_stream",
    "rtsp://u:p@cam/unknown/path",
    "",
])
def test_substream_url_none_when_unknown_or_already_sub(already):
    assert substream_url(already) == ""


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    store = {**service.config.DEFAULTS, "rtsp_url": "rtsp://u:p@cam/Streaming/Channels/101"}
    monkeypatch.setattr(service, "_cfg", lambda: dict(store))
    monkeypatch.setattr(service.config, "save_config", lambda c: store.update(c))
    started = []
    monkeypatch.setattr(service.worker, "start", lambda: started.append(1))
    tok = "s" * 48
    service._tokens[tok] = time.time()
    c = TestClient(service.app)
    c.headers["X-Auth-Token"] = tok
    c.store, c.started = store, started
    return c


def test_state_suggests_substream_only_for_live_hevc(client, monkeypatch):
    monkeypatch.setattr(service.Worker, "connected", property(lambda self: True))
    service.worker.codec = "h264"
    assert client.get("/api/state").json()["substream_url"] == ""
    service.worker.codec = "hevc"
    assert client.get("/api/state").json()["substream_url"] == "rtsp://u:p@cam/Streaming/Channels/102"
    monkeypatch.setattr(service.Worker, "connected", property(lambda self: False))
    assert client.get("/api/state").json()["substream_url"] == ""
    service.worker.codec = ""


def test_switch_and_revert_endpoints(client):
    r = client.post("/api/camera/substream").json()
    assert r["ok"] and r["url"].endswith("/102")
    assert client.store["rtsp_url"].endswith("/102") and client.store["rtsp_url_main"].endswith("/101")
    assert client.started == [1]
    st = client.get("/api/state").json()
    assert st["rtsp_url_main"].endswith("/101")
    assert client.post("/api/camera/substream").status_code == 400  # already on sub-stream
    r = client.post("/api/camera/mainstream").json()
    assert r["ok"] and r["url"].endswith("/101") and client.store["rtsp_url_main"] == ""
    assert client.started == [1, 1]
    assert client.post("/api/camera/mainstream").status_code == 400
