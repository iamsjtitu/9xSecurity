"""Iter24: API tests for /api/camera/brands, /api/camera/build_url, /api/camera/test with FakeCam."""
import os
import sys
import time
import urllib.parse
import pytest
import requests

sys.path.insert(0, os.path.dirname(__file__))
from test_rtsp_discover import FakeCam  # noqa: E402

BASE = "http://127.0.0.1:8971"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE}/api/login", json={"username": "admin", "password": "9xsecurity"}, timeout=5)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def H(token):
    return {"X-Auth-Token": token}


@pytest.fixture(scope="module")
def fake_cam():
    fc = FakeCam()
    fc.start()
    yield fc
    fc.stop()


def test_brands(H):
    r = requests.get(f"{BASE}/api/camera/brands", headers=H, timeout=5)
    assert r.status_code == 200
    brands = r.json()
    # response may be {"brands":[...]} or a list
    if isinstance(brands, dict):
        brands = brands.get("brands", brands.get("items", []))
    assert isinstance(brands, list)
    assert len(brands) == 12, f"expected 12 brands, got {len(brands)}"
    ids = {b["id"] for b in brands}
    for req in ("hikvision", "dahua", "custom", "auto"):
        assert req in ids, f"missing brand {req}"
    for b in brands:
        for key in ("id", "name", "port"):
            assert key in b, f"brand {b} missing {key}"


def test_build_hikvision(H):
    r = requests.post(f"{BASE}/api/camera/build_url", headers=H, json={
        "brand": "hikvision", "ip": "192.168.1.28", "user": "admin", "password": "Admin@123"
    }, timeout=5)
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    assert url == "rtsp://admin:Admin%40123@192.168.1.28:554/Streaming/Channels/101", url


def test_build_dahua_ch2_sub(H):
    r = requests.post(f"{BASE}/api/camera/build_url", headers=H, json={
        "brand": "dahua", "ip": "192.168.1.28", "user": "admin", "password": "x",
        "channel": 2, "stream": "sub"
    }, timeout=5)
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    assert "cam/realmonitor?channel=2&subtype=1" in url, url


def test_build_custom(H):
    r = requests.post(f"{BASE}/api/camera/build_url", headers=H, json={
        "brand": "custom", "ip": "192.168.1.28", "port": 8554,
        "user": "u", "password": "p", "custom_path": "/x/y"
    }, timeout=5)
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    assert ":8554/x/y" in url, url


def test_build_bad_ip(H):
    r = requests.post(f"{BASE}/api/camera/build_url", headers=H, json={
        "brand": "hikvision", "ip": "bad ip!", "user": "a", "password": "b"
    }, timeout=5)
    assert r.status_code == 400, r.text


def test_build_missing_ip(H):
    r = requests.post(f"{BASE}/api/camera/build_url", headers=H, json={
        "brand": "hikvision", "user": "a", "password": "b"
    }, timeout=5)
    assert r.status_code == 400, r.text


def test_camera_test_auto_fix(H, fake_cam):
    url = f"rtsp://admin:Admin@123@127.0.0.1:{fake_cam.port}/"
    r = requests.post(f"{BASE}/api/camera/test", headers=H, json={"url": url}, timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["url"].endswith("/Streaming/Channels/101"), data
    step_names = [s.get("name", s.get("step", "")) for s in data["steps"]]
    assert any("RTSP handshake" in n for n in step_names), step_names
    assert any("Stream path auto-detect" in n for n in step_names), step_names
    # persisted?
    st = requests.get(f"{BASE}/api/state", headers=H, timeout=5).json()
    assert st.get("rtsp_url", "").endswith("/Streaming/Channels/101"), st.get("rtsp_url")


def test_camera_test_wrong_password(H, fake_cam):
    url = f"rtsp://admin:wrong@127.0.0.1:{fake_cam.port}/"
    r = requests.post(f"{BASE}/api/camera/test", headers=H, json={"url": url}, timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    # last handshake step should mention 401
    handshake_steps = [s for s in data["steps"] if "RTSP handshake" in s.get("name", s.get("step", ""))]
    assert handshake_steps, data
    last = handshake_steps[-1]
    detail = str(last.get("detail", "")) + " " + str(last.get("msg", "")) + " " + str(last)
    assert "401" in detail, last


def test_restore_state(H):
    r = requests.post(f"{BASE}/api/camera/connect", headers=H,
                      json={"url": "rtsp://u:p@192.0.2.1:554/s"}, timeout=10)
    assert r.status_code in (200, 202), r.text
    r2 = requests.post(f"{BASE}/api/camera/disconnect", headers=H, timeout=10)
    assert r2.status_code in (200, 202), r2.text
