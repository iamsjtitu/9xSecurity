"""Iteration 25 - Camera Scan API tests."""
import os, time, socket, threading, pytest, requests

BASE = "http://127.0.0.1:8971"

def _login():
    r = requests.post(f"{BASE}/api/login", json={"username": "admin", "password": "9xsecurity"}, timeout=10)
    assert r.status_code == 200, r.text
    return r.json()["token"]

@pytest.fixture(scope="module")
def token():
    return _login()

@pytest.fixture(scope="module")
def headers(token):
    return {"X-Auth-Token": token}


def test_scan_no_auth():
    r = requests.post(f"{BASE}/api/camera/scan", json={}, timeout=10)
    assert r.status_code == 401


def test_scan_bad_subnet(headers):
    r = requests.post(f"{BASE}/api/camera/scan", headers=headers, json={"subnets": ["bad"]}, timeout=15)
    assert r.status_code == 400
    assert "Subnet aise likhein" in r.json().get("detail", "")


def test_scan_empty_ok(headers):
    t0 = time.time()
    r = requests.post(f"{BASE}/api/camera/scan", headers=headers, json={}, timeout=45)
    dt = time.time() - t0
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body.get("subnets"), list)
    for s in body["subnets"]:
        assert s.count(".") == 2
    assert isinstance(body.get("cameras"), list)
    assert isinstance(body.get("seconds"), (int, float))
    assert dt < 45


def test_brands_list(headers):
    r = requests.get(f"{BASE}/api/camera/brands", headers=headers, timeout=10)
    assert r.status_code == 200
    brands = r.json()
    # accept either list or dict with brands key
    items = brands if isinstance(brands, list) else brands.get("brands", [])
    assert len(items) == 13
    ids = [b.get("id") for b in items]
    assert "hipcam" in ids


# ---- Optional FakeCam-driven scan ----
def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_scan_with_fakecam(headers):
    try:
        from test_rtsp_discover import FakeCam
    except Exception as e:
        pytest.skip(f"FakeCam import failed: {e}")
    cam = FakeCam()
    cam.start()
    try:
        time.sleep(0.5)
        r = requests.post(f"{BASE}/api/camera/scan", headers=headers,
                          json={"subnets": ["127.0.0"], "port": cam.port}, timeout=60)
        assert r.status_code == 200, r.text
        body = r.json()
        ips = [c["ip"] for c in body["cameras"]]
        assert "127.0.0.1" in ips, body
        cam_entry = next(c for c in body["cameras"] if c["ip"] == "127.0.0.1")
        assert cam_entry.get("auth_needed") is True
        assert cam_entry.get("realm") == "IP Camera"
        assert cam_entry.get("brand") == "hikvision"
        assert cam_entry.get("onvif") is False
    finally:
        try:
            cam.stop()
        except Exception:
            pass
