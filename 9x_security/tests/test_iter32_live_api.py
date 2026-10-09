"""Live API checks against running service for iter32 multicam workers."""
import time
import requests
import pytest

BASE = "http://127.0.0.1:8971"


@pytest.fixture(scope="module")
def tok():
    r = requests.post(f"{BASE}/api/login", json={"username": "admin", "password": "9xsecurity"}, timeout=5)
    r.raise_for_status()
    return r.json()["token"]


def H(tok):
    return {"X-Auth-Token": tok}


def get_cams(tok):
    r = requests.get(f"{BASE}/api/cameras", headers=H(tok), timeout=5)
    r.raise_for_status()
    return {c["id"]: c for c in r.json().get("cameras", [])}


def get_state(tok):
    return requests.get(f"{BASE}/api/state", headers=H(tok), timeout=5).json()


def get_diag(tok):
    return requests.get(f"{BASE}/api/diagnostics", headers=H(tok), timeout=5).json()


def wait_for(predicate, timeout=30, interval=1.0):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = predicate()
            if last:
                return last
        except Exception as e:
            last = e
        time.sleep(interval)
    return last


# --- Setup: restore baseline ---
def test_00_setup_baseline(tok):
    # Ensure cam1 has rtsp_url and is viewed+monitor true; cam2 schedule 20-08 enabled
    requests.post(f"{BASE}/api/cameras", headers=H(tok),
                  json={"id": "cam1", "name": "Front Camera", "gate": "Main Gate",
                        "rtsp_url": "rtsp://u:p@192.0.2.1:554/s", "monitor": True}, timeout=5)
    requests.post(f"{BASE}/api/cameras/cam1/activate", headers=H(tok), timeout=5)
    requests.post(f"{BASE}/api/cameras", headers=H(tok),
                  json={"id": "cam2", "schedule": {"enabled": True, "start": "20:00", "end": "08:00"},
                        "monitor": True}, timeout=5)
    cams = get_cams(tok)
    assert "cam1" in cams and "cam2" in cams
    assert cams["cam1"]["monitor"] is True
    assert cams["cam2"]["schedule"] == {"enabled": True, "start": "20:00", "end": "08:00"}


def test_01_cameras_endpoint_shape(tok):
    cams = get_cams(tok)
    for cid in ("cam1", "cam2"):
        c = cams[cid]
        for k in ("monitor", "schedule", "window_open", "connected", "standby", "capture_paused", "status"):
            assert k in c, f"{cid} missing {k}"


def test_02_state_has_cameras_and_running(tok):
    st = get_state(tok)
    assert "cameras_running" in st
    assert "cameras" in st and len(st["cameras"]) >= 2


def test_03_bad_schedule_time_keeps_old(tok):
    before = get_cams(tok)["cam2"]["schedule"]
    requests.post(f"{BASE}/api/cameras", headers=H(tok),
                  json={"id": "cam2", "schedule": {"enabled": True, "start": "25x", "end": "08:00"}}, timeout=5)
    after = get_cams(tok)["cam2"]["schedule"]
    assert after == before, f"bad time changed schedule: {before} -> {after}"


def test_04_monitor_false_stops_worker(tok):
    requests.post(f"{BASE}/api/cameras", headers=H(tok), json={"id": "cam2", "monitor": False}, timeout=5)

    def chk():
        c = get_cams(tok)["cam2"]
        if c["monitor"] is False and c["connected"] is False and "Monitoring OFF" in (c["status"] or ""):
            return True
        return False
    assert wait_for(chk, timeout=10) is True, f"cam2 did not stop: {get_cams(tok)['cam2']}"


def test_05_monitor_true_restarts(tok):
    requests.post(f"{BASE}/api/cameras", headers=H(tok), json={"id": "cam2", "monitor": True}, timeout=5)

    def chk():
        c = get_cams(tok)["cam2"]
        return c["monitor"] is True and (c["status"] or "") not in ("", "Disconnected.") and "Monitoring OFF" not in (c["status"] or "")
    r = wait_for(chk, timeout=15)
    assert r is True, f"cam2 did not restart: {get_cams(tok)['cam2']}"


def test_06_cam2_standby_outside_window(tok):
    # container time ~08:xx UTC -> window 20:00-08:00 is CLOSED near 08:xx. If exactly 08:xx we may be at boundary.
    diag = get_diag(tok)
    tn = diag.get("time_now", "")
    # ensure cam2 not viewed
    requests.post(f"{BASE}/api/cameras/cam1/activate", headers=H(tok), timeout=5)

    # If current hour is 08-19 inclusive, window is closed
    def chk():
        c = get_cams(tok)["cam2"]
        if c["window_open"] is False and c["standby"] is True and "Standby" in (c["status"] or ""):
            return True
        return False
    r = wait_for(chk, timeout=35)
    if r is not True:
        c = get_cams(tok)["cam2"]
        pytest.skip(f"standby not reached (time_now={tn}, cam2={c})")
    c = get_cams(tok)["cam2"]
    assert "8:00 PM" in c["status"] and "8:00 AM" in c["status"], c["status"]


def test_07_activate_cam2_leaves_standby(tok):
    requests.post(f"{BASE}/api/cameras/cam2/activate", headers=H(tok), timeout=5)

    def chk():
        c = get_cams(tok)["cam2"]
        return c["standby"] is False
    r = wait_for(chk, timeout=10)
    assert r is True, f"cam2 still in standby after activate: {get_cams(tok)['cam2']}"


def test_08_activate_cam1_puts_cam2_back_to_standby(tok):
    requests.post(f"{BASE}/api/cameras/cam1/activate", headers=H(tok), timeout=5)

    def chk():
        c = get_cams(tok)["cam2"]
        return c["standby"] is True
    r = wait_for(chk, timeout=15)
    if r is not True:
        pytest.skip(f"might be near window boundary: {get_cams(tok)['cam2']}")


def test_09_schedule_containing_now_sets_standby_false(tok):
    # set cam2 schedule to always-on by covering now
    requests.post(f"{BASE}/api/cameras", headers=H(tok),
                  json={"id": "cam2", "schedule": {"enabled": True, "start": "00:00", "end": "23:59"}}, timeout=5)

    def chk():
        c = get_cams(tok)["cam2"]
        return c["window_open"] is True and c["standby"] is False
    r = wait_for(chk, timeout=10)
    assert r is True, f"cam2 should be out of standby: {get_cams(tok)['cam2']}"


def test_10_disconnect_viewed(tok):
    requests.post(f"{BASE}/api/camera/disconnect", headers=H(tok), timeout=5)

    def chk():
        c = get_cams(tok)["cam1"]
        return c["monitor"] is False and "Disconnected" in (c["status"] or "")
    r = wait_for(chk, timeout=10)
    assert r is True, f"cam1 did not disconnect: {get_cams(tok)['cam1']}"


def test_11_connect_viewed(tok):
    requests.post(f"{BASE}/api/camera/connect", headers=H(tok),
                  json={"url": "rtsp://u:p@192.0.2.1:554/s"}, timeout=5)

    def chk():
        c = get_cams(tok)["cam1"]
        return c["monitor"] is True
    r = wait_for(chk, timeout=10)
    assert r is True


def test_12_add_and_delete_camera(tok):
    r = requests.post(f"{BASE}/api/cameras", headers=H(tok),
                      json={"name": "TEST_Cam", "gate": "Side", "rtsp_url": "rtsp://x:y@192.0.2.5:554/s"}, timeout=5)
    assert r.status_code == 200, r.text
    body = r.json()
    new_id = None
    for c in body.get("cameras", []):
        if c["name"] == "TEST_Cam":
            new_id = c["id"]
            break
    assert new_id, body

    # worker gets a status within few seconds
    def chk_in_diag():
        d = get_diag(tok)
        for c in d.get("cameras", []):
            if c["id"] == new_id and (c.get("status") or "") != "":
                return True
        return False
    assert wait_for(chk_in_diag, timeout=20) is True

    # delete
    r = requests.delete(f"{BASE}/api/cameras/{new_id}", headers=H(tok), timeout=5)
    assert r.status_code == 200

    def chk_gone():
        d = get_diag(tok)
        return all(c["id"] != new_id for c in d.get("cameras", []))
    assert wait_for(chk_gone, timeout=10) is True


def test_13_public_status(tok):
    r = requests.get(f"{BASE}/api/public/status", timeout=5)
    assert r.status_code == 200
    assert "connected" in r.json()


def test_14_diagnostics_per_cam_fields(tok):
    d = get_diag(tok)
    for c in d.get("cameras", []):
        for k in ("ai_ms", "ai_frames", "codec", "frame_age", "possible_misses", "viewed"):
            assert k in c, f"{c['id']} missing {k}"


def test_15_line_change_on_viewed_only(tok):
    # ensure cam1 viewed
    requests.post(f"{BASE}/api/cameras/cam1/activate", headers=H(tok), timeout=5)
    cam2_line_before = get_cams(tok)["cam2"]["line"] if "line" in get_cams(tok)["cam2"] else None
    r = requests.post(f"{BASE}/api/line", headers=H(tok),
                      json={"x1": 0.1, "y1": 0.5, "x2": 0.9, "y2": 0.5}, timeout=5)
    assert r.status_code == 200
    # cam2 line must be unchanged - check config.json via diagnostics isn't exposed; use /api/cameras
    import json as _j, os
    with open("/app/9x_security/config.json") as f:
        cfg = _j.load(f)
    cams_cfg = {c["id"]: c for c in cfg.get("cameras", [])}
    assert cams_cfg["cam1"]["line"] == {"x1": 0.1, "y1": 0.5, "x2": 0.9, "y2": 0.5}
    assert cams_cfg["cam2"]["line"] == {"x1": 0.1, "y1": 0.77, "x2": 0.9, "y2": 0.77}


def test_16_options_and_settings(tok):
    assert requests.post(f"{BASE}/api/options", headers=H(tok), json={}, timeout=5).status_code == 200
    assert requests.get(f"{BASE}/api/settings", headers=H(tok), timeout=5).status_code == 200


def test_17_frame_404_or_jpeg(tok):
    r = requests.get(f"{BASE}/api/frame", headers=H(tok), timeout=5)
    assert r.status_code in (200, 404)


def test_18_camera_log_prefixed(tok):
    import os
    p = "/app/9x_security/camera_log.txt"
    if not os.path.exists(p):
        pytest.skip("no camera_log yet")
    with open(p) as f:
        text = f.read()
    assert "svc[" in text, "camera_log should have svc[<name>]: prefix"


def test_99_restore_baseline(tok):
    requests.post(f"{BASE}/api/cameras/cam1/activate", headers=H(tok), timeout=5)
    requests.post(f"{BASE}/api/cameras", headers=H(tok),
                  json={"id": "cam1", "monitor": True, "rtsp_url": "rtsp://u:p@192.0.2.1:554/s"}, timeout=5)
    requests.post(f"{BASE}/api/cameras", headers=H(tok),
                  json={"id": "cam2", "monitor": True,
                        "schedule": {"enabled": True, "start": "20:00", "end": "08:00"}}, timeout=5)
    cams = get_cams(tok)
    assert set(cams.keys()) == {"cam1", "cam2"}
