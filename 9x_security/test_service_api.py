"""E2E backend API tests for 9x Security FastAPI service (127.0.0.1:8971)."""
import json
import os
import time

import pytest
import requests

BASE = f"http://127.0.0.1:{os.environ.get('ENGINE_PORT', '8971')}"
LOGIN_USER = "admin"
LOGIN_PASS = "9xsecurity"


@pytest.fixture(scope="session")
def token():
    r = requests.post(f"{BASE}/api/login", json={"username": LOGIN_USER, "password": LOGIN_PASS}, timeout=5)
    assert r.status_code == 200, r.text
    t = r.json().get("token")
    assert isinstance(t, str) and len(t) > 20
    return t


@pytest.fixture
def hdr(token):
    return {"X-Auth-Token": token}


# ---------- health / static ----------
def test_health():
    r = requests.get(f"{BASE}/api/health", timeout=5)
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert isinstance(j["version"], str) and j["version"]


def test_static_index_served():
    r = requests.get(f"{BASE}/", timeout=5)
    assert r.status_code == 200
    assert "<html" in r.text.lower() or "<!doctype" in r.text.lower()


# ---------- auth ----------
def test_login_wrong_password():
    r = requests.post(f"{BASE}/api/login", json={"username": "admin", "password": "wrong"}, timeout=5)
    assert r.status_code == 401


def test_protected_without_token_401():
    for ep in ("/api/state", "/api/counts", "/api/events", "/api/settings"):
        r = requests.get(f"{BASE}{ep}", timeout=5)
        assert r.status_code == 401, ep


# ---------- state / counts / events ----------
def test_state_keys(hdr):
    r = requests.get(f"{BASE}/api/state", headers=hdr, timeout=5)
    assert r.status_code == 200
    j = r.json()
    for k in ("connected", "status", "version", "rtsp_url", "vehicle_classes",
              "line", "snapshot_dir"):
        assert k in j, f"missing key {k}"
    assert isinstance(j["vehicle_classes"], list)


def test_counts(hdr):
    r = requests.get(f"{BASE}/api/counts", headers=hdr, timeout=5)
    assert r.status_code == 200
    j = r.json()
    assert "Entry" in j and "Exit" in j
    assert isinstance(j["Entry"], int) and isinstance(j["Exit"], int)


def test_events_filters(hdr):
    r = requests.get(f"{BASE}/api/events?all=1", headers=hdr, timeout=5)
    assert r.status_code == 200
    assert "events" in r.json()
    r2 = requests.get(f"{BASE}/api/events?date=2026-01-01&direction=Entry", headers=hdr, timeout=5)
    assert r2.status_code == 200


# ---------- line / swap / options ----------
def test_line_set(hdr):
    payload = {"x1": 0.1, "y1": 0.2, "x2": 0.9, "y2": 0.8}
    r = requests.post(f"{BASE}/api/line", headers=hdr, json=payload, timeout=5)
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] and j["line"]["x1"] == pytest.approx(0.1)
    # Verify persistence
    st = requests.get(f"{BASE}/api/state", headers=hdr, timeout=5).json()
    assert st["line"]["x2"] == pytest.approx(0.9)


def test_swap(hdr):
    before = requests.get(f"{BASE}/api/state", headers=hdr, timeout=5).json()["entry_direction"]
    r = requests.post(f"{BASE}/api/swap", headers=hdr, timeout=5)
    assert r.status_code == 200
    after = r.json()["entry_direction"]
    assert after != before
    # swap back
    requests.post(f"{BASE}/api/swap", headers=hdr, timeout=5)


def test_options_filter_invalid_classes(hdr):
    r = requests.post(f"{BASE}/api/options", headers=hdr,
                      json={"enable_plate": True, "vehicle_classes": ["car", "airplane", "bus"]}, timeout=5)
    assert r.status_code == 200
    st = requests.get(f"{BASE}/api/state", headers=hdr, timeout=5).json()
    assert "airplane" not in st["vehicle_classes"]
    assert "car" in st["vehicle_classes"] and "bus" in st["vehicle_classes"]
    assert st["enable_plate"] is True
    # reset
    requests.post(f"{BASE}/api/options", headers=hdr,
                  json={"enable_plate": False, "vehicle_classes": ["car", "truck", "bus"]}, timeout=5)


# ---------- settings roundtrip + password change ----------
def test_settings_roundtrip(hdr):
    orig = requests.get(f"{BASE}/api/settings", headers=hdr, timeout=5).json()
    new_body = {
        "wa_api_key": "TEST_KEY_ABC",
        "wa_recipients": ["+911234567890", "+919876543210"],
        "gh_token": "TEST_GH",
        "auth_user": "admin",
    }
    r = requests.post(f"{BASE}/api/settings", headers=hdr, json=new_body, timeout=5)
    assert r.status_code == 200
    got = requests.get(f"{BASE}/api/settings", headers=hdr, timeout=5).json()
    assert got["wa_api_key"] == "TEST_KEY_ABC"
    assert got["wa_recipients"] == ["+911234567890", "+919876543210"]
    assert got["gh_token"] == "TEST_GH"
    # restore
    restore = {k: orig.get(k) for k in ("wa_api_key", "wa_recipients", "gh_token")}
    restore["auth_user"] = orig.get("auth_user", "admin")
    requests.post(f"{BASE}/api/settings", headers=hdr, json=restore, timeout=5)


def test_password_change_and_restore(hdr):
    # Change password
    r = requests.post(f"{BASE}/api/settings", headers=hdr,
                      json={"new_password": "TEMPPASS123"}, timeout=5)
    assert r.status_code == 200
    # Old password fails
    r_old = requests.post(f"{BASE}/api/login", json={"username": "admin", "password": "9xsecurity"}, timeout=5)
    assert r_old.status_code == 401
    # New password works
    r_new = requests.post(f"{BASE}/api/login", json={"username": "admin", "password": "TEMPPASS123"}, timeout=5)
    assert r_new.status_code == 200
    new_tok = r_new.json()["token"]
    # Restore
    r_restore = requests.post(f"{BASE}/api/settings", headers={"X-Auth-Token": new_tok},
                              json={"new_password": "9xsecurity"}, timeout=5)
    assert r_restore.status_code == 200
    r_final = requests.post(f"{BASE}/api/login", json={"username": "admin", "password": "9xsecurity"}, timeout=5)
    assert r_final.status_code == 200


# ---------- camera ----------
def test_camera_test_unreachable(hdr):
    # TEST-NET address, will fail all steps within ~4s
    r = requests.post(f"{BASE}/api/camera/test", headers=hdr,
                      json={"url": "rtsp://u:p@192.0.2.1:554/s"}, timeout=30)
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is False
    assert isinstance(j["steps"], list) and len(j["steps"]) >= 2
    for step in j["steps"]:
        assert "name" in step and "ok" in step and "detail" in step


def test_camera_test_missing_url(hdr):
    r = requests.post(f"{BASE}/api/camera/test", headers=hdr, json={"url": ""}, timeout=5)
    assert r.status_code == 400


def test_camera_connect_disconnect(hdr):
    # Empty url => webcam 0, will fail in container -> ERROR status
    r = requests.post(f"{BASE}/api/camera/connect", headers=hdr,
                      json={"url": "rtsp://u:p@192.0.2.1:554/s"}, timeout=5)
    assert r.status_code == 200
    # Poll a bit for status transition
    seen_error_or_loading = False
    for _ in range(15):
        time.sleep(1)
        st = requests.get(f"{BASE}/api/state", headers=hdr, timeout=5).json()
        if "ERROR" in st["status"] or "load" in st["status"].lower() or "connect" in st["status"].lower():
            seen_error_or_loading = True
        if "ERROR" in st["status"]:
            break
    assert seen_error_or_loading
    # Disconnect
    r2 = requests.post(f"{BASE}/api/camera/disconnect", headers=hdr, timeout=5)
    assert r2.status_code == 200


def test_stream_headers(hdr, token):
    # Just check headers/status quickly
    r = requests.get(f"{BASE}/api/stream?t={token}", stream=True, timeout=3)
    assert r.status_code == 200
    assert "multipart/x-mixed-replace" in r.headers.get("content-type", "")
    r.close()


# ---------- snapshot security ----------
def test_snapshot_forbidden_outside(hdr):
    r = requests.get(f"{BASE}/api/snapshot?path=/etc/passwd", headers=hdr, timeout=5)
    assert r.status_code == 403


def test_snapshot_not_found_inside(hdr):
    st = requests.get(f"{BASE}/api/state", headers=hdr, timeout=5).json()
    p = os.path.join(st["snapshot_dir"], "does_not_exist_xyz.jpg")
    r = requests.get(f"{BASE}/api/snapshot?path={p}", headers=hdr, timeout=5)
    assert r.status_code == 404


# ---------- whatsapp ----------
def test_whatsapp_fake_key(hdr):
    r = requests.post(f"{BASE}/api/whatsapp/test", headers=hdr,
                      json={"wa_api_key": "FAKE_KEY", "wa_recipients": ["+911234567890"]},
                      timeout=30)
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is False
    assert "detail" in j


# ---------- updates ----------
def test_update_check(hdr):
    r = requests.get(f"{BASE}/api/update/check", headers=hdr, timeout=10)
    assert r.status_code == 200
    j = r.json()
    assert j["available"] is False
    assert "message" in j
