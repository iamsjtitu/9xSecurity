"""Iteration 26 API E2E tests against the running service (port 8971) with mediamtx (:8554)."""
import os, time, json, requests, pytest

BASE = "http://127.0.0.1:8971"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE}/api/login", json={"username": "admin", "password": "9xsecurity"}, timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    tok = body.get("token") or body.get("session") or body.get("X-Auth-Token")
    # try common shapes
    if not tok and isinstance(body, dict):
        for k, v in body.items():
            if isinstance(v, str) and len(v) > 10:
                tok = v; break
    assert tok, f"no token in {body}"
    return tok


def _h(tok):
    return {"X-Auth-Token": tok, "Content-Type": "application/json"}


def test_camera_test_root_autofix(token):
    r = requests.post(f"{BASE}/api/camera/test",
                      json={"url": "rtsp://admin:Admin@123@127.0.0.1:8554/"},
                      headers=_h(token), timeout=60)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("ok") is True, d
    assert d.get("url", "").endswith("/live"), d
    steps = {s["name"]: s for s in d.get("steps", [])}
    assert "Stream path auto-detect" in steps and steps["Stream path auto-detect"]["ok"] is True
    assert "Video stream (TCP)" in steps and steps["Video stream (TCP)"]["ok"] is True


def test_camera_test_live_ok(token):
    r = requests.post(f"{BASE}/api/camera/test",
                      json={"url": "rtsp://admin:Admin@123@127.0.0.1:8554/live"},
                      headers=_h(token), timeout=60)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("ok") is True, d
    steps = {s["name"]: s for s in d.get("steps", [])}
    assert steps["RTSP handshake"]["ok"] is True


def test_camera_test_wrong_password(token):
    r = requests.post(f"{BASE}/api/camera/test",
                      json={"url": "rtsp://admin:wrong@127.0.0.1:8554/live"},
                      headers=_h(token), timeout=60)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("ok") is False, d
    steps = {s["name"]: s for s in d.get("steps", [])}
    assert steps["RTSP handshake"]["ok"] is False
    msg = (steps["RTSP handshake"].get("detail") or "") + (steps["RTSP handshake"].get("msg") or "") + json.dumps(steps["RTSP handshake"])
    assert "401" in msg, msg
    # video attempts still ran
    assert "Video stream (TCP)" in steps
    # Hint should mention 401/password
    hint = steps.get("Hint") or {}
    hint_txt = json.dumps(hint).lower()
    assert "401" in hint_txt or "password" in hint_txt or "galat" in hint_txt, hint


def test_camera_connect_autofix_and_frame(token):
    r = requests.post(f"{BASE}/api/camera/connect",
                      json={"url": "rtsp://admin:Admin@123@127.0.0.1:8554/"},
                      headers=_h(token), timeout=30)
    assert r.status_code == 200, r.text

    deadline = time.time() + 25
    connected = False
    fixed = False
    rtsp_url = ""
    while time.time() < deadline:
        s = requests.get(f"{BASE}/api/state", headers=_h(token), timeout=10).json()
        rtsp_url = s.get("rtsp_url", "")
        if s.get("connected"):
            connected = True
        if rtsp_url.endswith("/live"):
            fixed = True
        if connected and fixed:
            break
        time.sleep(1)
    assert connected, "not connected within 25s"
    assert fixed, f"rtsp_url never auto-fixed to /live: {rtsp_url}"

    # wait for a live frame (AI model load can add a few seconds)
    deadline2 = time.time() + 25
    fr = None
    while time.time() < deadline2:
        fr = requests.get(f"{BASE}/api/frame", headers=_h(token), timeout=10)
        if fr.status_code == 200 and fr.content[:3] == b"\xff\xd8\xff":
            break
        time.sleep(1)
    assert fr is not None and fr.status_code == 200 and fr.content[:3] == b"\xff\xd8\xff", (fr.status_code, fr.content[:80])


def test_restore_disconnect(token):
    requests.post(f"{BASE}/api/camera/disconnect", headers=_h(token), timeout=15)
    # dummy connect to a non-routable to reset stored URL to something safe
    requests.post(f"{BASE}/api/camera/connect",
                  json={"url": "rtsp://u:p@192.0.2.1:554/s"},
                  headers=_h(token), timeout=10)
    time.sleep(1)
    r = requests.post(f"{BASE}/api/camera/disconnect", headers=_h(token), timeout=15)
    assert r.status_code == 200
