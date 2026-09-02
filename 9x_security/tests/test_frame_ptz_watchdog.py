"""Pytest for /api/frame, /api/ptz/zoom, watchdog reconnect and frame_age."""
import os, sys, time, subprocess, pytest, requests

sys.path.insert(0, "/app/9x_security")

BASE = "http://127.0.0.1:8971"
CLIP = "/app/9x_security/test_clip_short.avi"
CLIP_LONG = "/app/9x_security/test_clip_long.avi"


def _gen_clip(path, seconds=2, fps=15, w=320, h=240):
    import cv2, numpy as np
    fourcc = cv2.VideoWriter_fourcc(*"MJPG")
    vw = cv2.VideoWriter(path, fourcc, fps, (w, h))
    for i in range(seconds * fps):
        f = np.zeros((h, w, 3), dtype="uint8")
        # moving square to guarantee pixel changes
        x = (i * 8) % (w - 40)
        f[80:160, x:x + 40] = (0, 200 - (i * 5) % 200, 255)
        vw.write(f)
    vw.release()


@pytest.fixture(scope="module")
def clips():
    _gen_clip(CLIP, seconds=2)
    _gen_clip(CLIP_LONG, seconds=30)
    yield
    for p in (CLIP, CLIP_LONG):
        try: os.remove(p)
        except FileNotFoundError: pass


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE}/api/login", json={"username": "admin", "password": "9xsecurity"}, timeout=10)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def hdr(token):
    return {"X-Auth-Token": token}


def _wait_connected(hdr, timeout=70):
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout:
        r = requests.get(f"{BASE}/api/state", headers=hdr, timeout=5)
        if r.status_code == 200:
            s = r.json()
            last = s.get("status", "")
            if s.get("connected") and "Connected" in last:
                return s
        time.sleep(1)
    raise AssertionError(f"camera did not connect in {timeout}s (last status: {last})")


# ------------- /api/frame auth / 404 / 200 -------------
def test_frame_no_token_401():
    r = requests.get(f"{BASE}/api/frame", timeout=5)
    assert r.status_code == 401


def test_frame_with_token_when_disconnected_404(hdr):
    requests.post(f"{BASE}/api/camera/disconnect", headers=hdr, timeout=5)
    time.sleep(1.5)
    r = requests.get(f"{BASE}/api/frame", headers=hdr, timeout=5)
    assert r.status_code == 404


def test_frame_state_frame_age_null_when_disconnected(hdr):
    r = requests.get(f"{BASE}/api/state", headers=hdr, timeout=5)
    assert r.status_code == 200
    assert r.json().get("frame_age") is None


def test_connect_and_frame_200_jpeg(hdr, clips):
    r = requests.post(f"{BASE}/api/camera/connect", headers=hdr, json={"url": CLIP_LONG}, timeout=10)
    assert r.status_code == 200
    st = _wait_connected(hdr, timeout=70)
    assert st["connected"] is True
    # frame endpoint returns 200 with jpeg + Cache-Control: no-store
    for _ in range(20):
        r = requests.get(f"{BASE}/api/frame", headers=hdr, timeout=5)
        if r.status_code == 200:
            break
        time.sleep(0.5)
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("image/jpeg")
    assert "no-store" in r.headers.get("cache-control", "").lower()
    assert r.content[:3] == b"\xff\xd8\xff"  # JPEG SOI


def test_frame_age_populated_when_streaming(hdr):
    r = requests.get(f"{BASE}/api/state", headers=hdr, timeout=5)
    fa = r.json().get("frame_age")
    assert fa is not None and 0.0 <= fa <= 3.0, f"unexpected frame_age={fa}"


def test_two_frames_differ(hdr):
    r1 = requests.get(f"{BASE}/api/frame", headers=hdr, timeout=5).content
    time.sleep(1.0)
    r2 = requests.get(f"{BASE}/api/frame", headers=hdr, timeout=5).content
    assert r1 and r2 and r1 != r2, "consecutive frames identical => stream frozen"


# ------------- watchdog reconnect -------------
def test_watchdog_reconnects_on_short_clip(hdr, clips):
    # short 2s clip loops in OpenCV VideoCapture returns EOF -> watchdog kicks in
    r = requests.post(f"{BASE}/api/camera/connect", headers=hdr, json={"url": CLIP}, timeout=10)
    assert r.status_code == 200
    _wait_connected(hdr, timeout=70)
    # let watchdog window (>15s) elapse
    time.sleep(20)
    log = open("/app/9x_security/camera_log.txt", "r", errors="ignore").read()
    assert "stream lost, reconnecting (watchdog)" in log, "watchdog log not written"
    st = requests.get(f"{BASE}/api/state", headers=hdr, timeout=5).json()
    # should be either reconnected (Connected) or in reconnecting status, never permanently frozen
    assert "Connected" in st["status"] or "dobara connect" in st["status"], st["status"]


# ------------- PTZ endpoint -------------
def test_ptz_unreachable_returns_supported_false(hdr):
    # current rtsp_url is the file path -> ptz.creds_from_rtsp returns nothing / no ONVIF port
    r = requests.post(f"{BASE}/api/ptz/zoom", headers=hdr, json={"dir": "in", "action": "start"}, timeout=15)
    assert r.status_code == 200
    body = r.json()
    assert body["supported"] is False
    assert body["ok"] is False


def test_camera_connect_resets_ptz_cache(hdr, clips):
    # Service runs in a separate process — verify the source path invokes ptz.reset_cache()
    src = open("/app/9x_security/service.py").read()
    assert "ptz.reset_cache()" in src
    # And ensure the endpoint still works after we call it
    r = requests.post(f"{BASE}/api/camera/connect", headers=hdr, json={"url": CLIP_LONG}, timeout=10)
    assert r.status_code == 200 and r.json().get("ok") is True


# ------------- direct ptz.py module tests (rerun 6/6) -------------
def test_ptz_module_direct_6_of_6():
    # Reruns the standalone test_ptz.py in a subprocess
    r = subprocess.run(
        [sys.executable, "/app/9x_security/test_ptz.py"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, f"stdout={r.stdout}\nstderr={r.stderr}"
    assert "ALL PTZ TESTS PASSED" in r.stdout


# ------------- cleanup -------------
def test_zzz_cleanup(hdr):
    requests.post(f"{BASE}/api/camera/disconnect", headers=hdr, timeout=5)
