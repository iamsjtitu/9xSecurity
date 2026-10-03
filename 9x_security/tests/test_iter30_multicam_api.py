"""Iteration 30 — live API checks for multi-camera feature.
Runs against local service at http://127.0.0.1:8971.
"""
import os
import time
import pytest
import requests

BASE = "http://127.0.0.1:8971"


@pytest.fixture(scope="module")
def tok():
    r = requests.post(f"{BASE}/api/login", json={"username": "admin", "password": "9xsecurity"}, timeout=5)
    assert r.status_code == 200
    return r.json()["token"]


@pytest.fixture()
def h(tok):
    return {"X-Auth-Token": tok}


def test_cameras_shape(h):
    r = requests.get(f"{BASE}/api/cameras", headers=h, timeout=5)
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert "cameras" in j and "active_camera_id" in j and "rtsp_url" in j and "gate" in j
    for c in j["cameras"]:
        for k in ("id", "name", "gate", "rtsp_url", "active"):
            assert k in c


def test_state_includes_multicam_fields(h):
    r = requests.get(f"{BASE}/api/state", headers=h, timeout=5)
    assert r.status_code == 200
    j = r.json()
    for k in ("cameras", "active_camera_id", "gate"):
        assert k in j


def test_add_edit_activate_delete_flow(h):
    # Snapshot current state
    initial = requests.get(f"{BASE}/api/cameras", headers=h, timeout=5).json()
    initial_ids = [c["id"] for c in initial["cameras"]]
    initial_active = initial["active_camera_id"]

    # Add
    r = requests.post(f"{BASE}/api/cameras", headers=h,
                      json={"name": "TEST_Cam", "gate": "TEST_Gate", "rtsp_url": "rtsp://u:p@192.0.2.9:554/s"}, timeout=5)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True
    new_id = None
    for c in j["cameras"]:
        if c["name"] == "TEST_Cam":
            new_id = c["id"]
            break
    assert new_id and new_id not in initial_ids

    # Edit — name/gate
    r = requests.post(f"{BASE}/api/cameras", headers=h,
                      json={"id": new_id, "name": "TEST_Cam2", "gate": "TEST_Gate2"}, timeout=5)
    assert r.status_code == 200
    cams = {c["id"]: c for c in r.json()["cameras"]}
    assert cams[new_id]["name"] == "TEST_Cam2"
    assert cams[new_id]["gate"] == "TEST_Gate2"

    # Activate
    r = requests.post(f"{BASE}/api/cameras/{new_id}/activate", headers=h, timeout=10)
    assert r.status_code == 200
    time.sleep(0.5)
    st = requests.get(f"{BASE}/api/state", headers=h, timeout=5).json()
    assert st["active_camera_id"] == new_id
    assert st["gate"] == "TEST_Gate2"
    assert st["rtsp_url"] == "rtsp://u:p@192.0.2.9:554/s"

    # Activate same again — no-op 200
    r = requests.post(f"{BASE}/api/cameras/{new_id}/activate", headers=h, timeout=10)
    assert r.status_code == 200

    # Unknown activate -> 404
    r = requests.post(f"{BASE}/api/cameras/bogus/activate", headers=h, timeout=5)
    assert r.status_code == 404

    # Per-camera line persistence: set line on new cam
    r = requests.post(f"{BASE}/api/line", headers=h,
                      json={"x1": 0.1, "y1": 0.77, "x2": 0.9, "y2": 0.77}, timeout=5)
    assert r.status_code == 200

    # Switch back to initial active
    if initial_active and initial_active != new_id:
        r = requests.post(f"{BASE}/api/cameras/{initial_active}/activate", headers=h, timeout=10)
        assert r.status_code == 200
        time.sleep(0.5)
        # Activate back
        r = requests.post(f"{BASE}/api/cameras/{new_id}/activate", headers=h, timeout=10)
        assert r.status_code == 200
        time.sleep(0.5)
        st = requests.get(f"{BASE}/api/state", headers=h, timeout=5).json()
        assert st["line"] is not None
        assert abs(st["line"]["y1"] - 0.77) < 0.001

    # Switch to initial before delete
    if initial_active and initial_active != new_id:
        requests.post(f"{BASE}/api/cameras/{initial_active}/activate", headers=h, timeout=10)
        time.sleep(0.3)

    # Unknown delete -> 404
    r = requests.delete(f"{BASE}/api/cameras/bogus", headers=h, timeout=5)
    assert r.status_code == 404

    # Delete the test camera (not active) — active stays
    r = requests.delete(f"{BASE}/api/cameras/{new_id}", headers=h, timeout=5)
    assert r.status_code == 200
    cams = requests.get(f"{BASE}/api/cameras", headers=h, timeout=5).json()
    assert new_id not in [c["id"] for c in cams["cameras"]]
    assert cams["active_camera_id"] == initial_active


def test_max_8_limit(h):
    cams_before = requests.get(f"{BASE}/api/cameras", headers=h, timeout=5).json()["cameras"]
    added = []
    try:
        for i in range(10):
            r = requests.post(f"{BASE}/api/cameras", headers=h,
                              json={"name": f"TEST_L{i}", "gate": "G", "rtsp_url": "rtsp://x/y"}, timeout=5)
            if r.status_code == 400:
                assert len(cams_before) + len(added) >= 8
                return
            assert r.status_code == 200
            j = r.json()
            new_ids = [c["id"] for c in j["cameras"] if c["name"] == f"TEST_L{i}"]
            if new_ids:
                added.append(new_ids[0])
        pytest.fail("Expected 400 at MAX_CAMERAS=8")
    finally:
        for cid in added:
            requests.delete(f"{BASE}/api/cameras/{cid}", headers=h, timeout=5)


def test_long_name_trimmed_to_40(h):
    long_name = "N" * 60
    long_gate = "G" * 60
    r = requests.post(f"{BASE}/api/cameras", headers=h,
                      json={"name": long_name, "gate": long_gate, "rtsp_url": "rtsp://a/b"}, timeout=5)
    assert r.status_code == 200
    j = r.json()
    added = None
    for c in j["cameras"]:
        if c["name"].startswith("N") and len(c["name"]) == 40:
            added = c
            break
    assert added is not None, "name not trimmed to 40"
    assert len(added["gate"]) == 40
    requests.delete(f"{BASE}/api/cameras/{added['id']}", headers=h, timeout=5)
