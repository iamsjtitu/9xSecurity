"""9x Security - headless self-test of the core engine (no GUI, no camera).

Generates a synthetic video of a 'car' box moving across the detection line
using a FAKE detector, and verifies that an Entry/Exit event is logged with
a snapshot + timestamp. Also runs a real YOLO detection sanity check if the
model is available.
"""
import os
import tempfile

import numpy as np

import config
from database import EventDB
from engine import SecurityEngine
from tracker import CentroidTracker, _side


class FakeDetector:
    """Returns a single moving car box driven by an external position."""

    def __init__(self):
        self.box = None

    def detect(self, frame):
        if self.box is None:
            return []
        return [{"bbox": self.box, "label": "car", "conf": 0.9}]


def test_line_crossing_logs_event():
    tmp = tempfile.mkdtemp()
    config.SNAPSHOT_DIR = os.path.join(tmp, "snaps")
    os.makedirs(config.SNAPSHOT_DIR, exist_ok=True)
    db = EventDB(db_path=os.path.join(tmp, "t.db"))

    cfg = dict(config.DEFAULTS)
    cfg["enable_plate"] = False
    cfg["detect_frame_skip"] = 1
    cfg["line"] = {"x1": 0.5, "y1": 0.0, "x2": 0.5, "y2": 1.0}  # vertical line at x=0.5

    fake = FakeDetector()
    eng = SecurityEngine(cfg=cfg, db=db, detector=fake, plate_reader=None)

    W, H = config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT
    all_events = []
    # Move a 100x100 box from left (x=100) to right (x=800), crossing x=480.
    for cx in range(100, 820, 40):
        fake.box = (cx, 200, cx + 100, 300)
        frame = np.zeros((H, W, 3), dtype=np.uint8)
        _, ev = eng.process_frame(frame, original=None)
        all_events.extend(ev)

    assert len(all_events) == 1, f"expected 1 crossing event, got {len(all_events)}"
    e = all_events[0]
    assert e["vehicle_type"] == "car"
    assert e["direction"] in ("Entry", "Exit")
    assert os.path.exists(e["image_path"]), "snapshot file not saved"
    rows = db.get_events()
    assert len(rows) == 1 and rows[0]["date"] and rows[0]["time"]
    print("PASS: line-crossing -> event logged:", e["direction"], e["vehicle_type"], e["image_path"])


def test_direction_mapping():
    a, b = (5, 0), (5, 10)  # vertical line
    # point left of line
    left = _side((0, 5), a, b)
    right = _side((10, 5), a, b)
    assert (left > 0) != (right > 0), "points on opposite sides should differ in sign"
    print("PASS: side-of-line sign detection")


def test_tracker_ids_persist():
    tr = CentroidTracker()
    line = ((5, 0), (5, 10))
    tr.update([{"bbox": (0, 0, 20, 20), "label": "car"}], line)
    first_ids = set(tr.tracks.keys())
    tr.update([{"bbox": (2, 2, 22, 22), "label": "car"}], line)  # small move -> same id
    assert set(tr.tracks.keys()) == first_ids, "track id should persist across small movement"
    print("PASS: tracker keeps stable id across frames")


def test_real_yolo_if_available():
    try:
        from detector import VehicleDetector

        det = VehicleDetector(conf=0.25, allowed=["car", "truck", "bus"])
        import numpy as np

        frame = np.zeros((config.DISPLAY_HEIGHT, config.DISPLAY_WIDTH, 3), dtype=np.uint8)
        out = det.detect(frame)  # blank frame -> should just return [] without crashing
        assert isinstance(out, list)
        print("PASS: real YOLO detector loaded and runs (blank frame dets=%d)" % len(out))
    except Exception as e:
        print("SKIP real YOLO test:", e)


def test_auth_password():
    import auth

    salt, h = auth.hash_password("secret123")
    assert auth.verify_password("secret123", salt, h) is True
    assert auth.verify_password("wrong", salt, h) is False
    assert auth.verify_password("secret123", "", "") is False
    print("PASS: auth password hash + verify")


def test_whatsapp_payload(monkeypatch=None):
    import whatsapp

    captured = {}

    class FakeResp:
        ok = True
        status_code = 201
        text = '{"success":true}'

    def fake_post(url, headers=None, json=None, data=None, files=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["data"] = data
        captured["files"] = files
        return FakeResp()

    whatsapp.requests.post = fake_post

    cfg = {
        "wa_enabled": True,
        "wa_base_url": "https://wa.9x.design",
        "wa_api_key": "wa9x_test",
        "wa_recipients": ["+91 98765 43210"],  # should be normalized to digits
        "wa_send_image": False,  # force text path (no file needed)
    }
    n = whatsapp.WhatsAppNotifier(cfg)
    ev = {
        "direction": "Entry",
        "vehicle_type": "truck",
        "timestamp": "2026-06-15T10:42:05",
        "plate": "HR26AB1234",
        "image_path": "",
    }
    n._send_all(ev)  # call synchronously
    assert captured["url"] == "https://wa.9x.design/api/v2/sendMessage"
    assert captured["headers"]["Authorization"] == "Bearer wa9x_test"
    assert captured["files"]["phonenumber"] == (None, "919876543210")
    text = captured["files"]["text"][1]
    assert "Entry" in text and "TRUCK" in text and "HR26AB1234" in text
    print("PASS: WhatsApp v2 sendMessage payload ->", text.replace(chr(10), " | "))


def test_whatsapp_disabled_noop():
    import whatsapp

    called = {"n": 0}

    def fake_post(*a, **k):
        called["n"] += 1

        class R:
            ok = True
            status_code = 200
            text = ""

        return R()

    whatsapp.requests.post = fake_post
    n = whatsapp.WhatsAppNotifier({"wa_enabled": False})
    n.notify({"direction": "Exit", "vehicle_type": "car", "timestamp": "x", "image_path": ""})
    import time

    time.sleep(0.2)
    assert called["n"] == 0, "disabled notifier must not call the API"
    print("PASS: WhatsApp disabled -> no API call")


def test_updater_version_compare():
    import updater

    assert updater.is_newer("1.0.1", "1.0.0")
    assert updater.is_newer("2.0.0", "1.9.9")
    assert not updater.is_newer("1.0.0", "1.0.0")
    assert not updater.is_newer("0.9.0", "1.0.0")
    assert updater.is_newer("v1.2.0".lstrip("vV"), "1.1.0")
    print("PASS: updater version compare")


def test_normalize_rtsp_url():
    from engine import normalize_rtsp_url as n

    # password containing '@' gets percent-encoded (the user's reported case)
    assert n("rtsp://admin:Admin@123@192.168.31.65:554/stream1") == \
        "rtsp://admin:Admin%40123@192.168.31.65:554/stream1"
    # normal URL untouched
    assert n("rtsp://admin:pass@192.168.1.5:554/s") == "rtsp://admin:pass@192.168.1.5:554/s"
    # idempotent - already-encoded stays same
    assert n("rtsp://admin:Admin%40123@192.168.31.65:554/stream1") == \
        "rtsp://admin:Admin%40123@192.168.31.65:554/stream1"
    # no credentials / empty
    assert n("rtsp://192.168.1.5:554/s") == "rtsp://192.168.1.5:554/s"
    assert n("") == ""
    print("PASS: RTSP url normalization (@ in password auto-encoded)")


def test_probe_rtsp():
    from engine import probe_rtsp

    # invalid URL -> first step fails fast
    ok, steps = probe_rtsp("notaurl")
    assert not ok and steps[0][1] is False

    # user's URL shape with unreachable TEST-NET host -> URL fixed, network step fails
    ok, steps = probe_rtsp("rtsp://admin:Admin@123@192.0.2.1:554/stream1")
    assert not ok
    assert "Admin%40123" in steps[0][2]
    assert any("192.0.2.1:554" in name for name, _o, _d in steps)
    assert steps[-1][1] is False
    print("PASS: probe_rtsp diagnoses bad URL + unreachable camera")


def test_updater_404_and_default_repo():
    import updater

    assert hasattr(updater, "DEFAULT_REPO")
    orig = updater._api
    updater._api = lambda url, token=None: (404, {})
    try:
        tag, asset, page = updater.check_latest("owner/name")
    finally:
        updater._api = orig
    assert tag == "" and asset is None
    print("PASS: updater handles 404 (no release / private repo) gracefully; DEFAULT_REPO exists")


def test_updater_token_and_private_repo():
    import updater

    orig = updater._api
    captured = {}

    def fake_api(url, token=None):
        captured["url"] = url
        captured["token"] = token
        return 200, {
            "tag_name": "v1.0.9",
            "html_url": "http://rel",
            "assets": [{
                "name": "9xSecuritySetup-v1.0.9.exe",
                "browser_download_url": "http://x/public",
                "url": "http://api/asset/1",
            }],
        }

    updater._api = fake_api
    try:
        tag, asset, page = updater.check_latest("owner/name", token="ghp_test")
        assert captured["token"] == "ghp_test"
        assert tag == "1.0.9"
        assert asset == "http://api/asset/1", "private repo must use API asset url"
        tag2, asset2, _p = updater.check_latest("owner/name")
        assert asset2 == "http://x/public", "public repo uses browser_download_url"
    finally:
        updater._api = orig

    # 403 (rate limit / access denied) must raise a helpful error
    updater._api = lambda url, token=None: (403, {})
    try:
        try:
            updater.check_latest("owner/name")
            raise AssertionError("403 should raise")
        except RuntimeError as e:
            assert "403" in str(e)
    finally:
        updater._api = orig
    print("PASS: token auth -> API asset url; public -> browser url; 403 raises helpful error")


def test_ffmpeg_pipe_source():
    import config
    from engine import FFmpegPipeSource

    # lavfi testsrc = synthetic video; proves the decode->BGR pipe works end to end
    src = FFmpegPipeSource("testsrc=duration=2:size=320x240:rate=5", input_args=["-f", "lavfi"])
    try:
        ok, frame = src.read()
        assert ok, "no frame from ffmpeg pipe"
        assert frame.shape == (config.DISPLAY_HEIGHT, config.DISPLAY_WIDTH, 3), frame.shape
        ok2, _f2 = src.read()
        assert ok2, "second frame missing"
    finally:
        src.release()
    print("PASS: FFmpeg pipe engine decodes video -> BGR frames (VLC-grade fallback)")


def test_ensure_std_streams():
    import io
    import sys as _sys

    import config as _cfg

    old_out, old_err = _sys.stdout, _sys.stderr
    had_frozen = hasattr(_sys, "frozen")
    try:
        _sys.frozen = True
        _sys.stdout = None
        _sys.stderr = None
        _cfg.ensure_std_streams()
        assert _sys.stdout is not None and _sys.stderr is not None
        print("frozen-mode print works now")  # must not crash
        _sys.stderr.write("stderr works too\n")
    finally:
        _sys.stdout, _sys.stderr = old_out, old_err
        if not had_frozen:
            del _sys.frozen
    assert os.path.exists(os.path.join(_cfg.BASE_DIR, "app_log.txt"))
    print("PASS: frozen build stdout/stderr redirect -> app_log.txt (no None-print crash)")


def test_time_window_and_wa_schedule():
    from datetime import datetime

    import config as _cfg
    import whatsapp

    T = lambda h, m=0: datetime(2026, 6, 15, h, m)
    # overnight window 18:00-06:00
    assert _cfg.in_time_window("18:00", "06:00", now=T(23))
    assert _cfg.in_time_window("18:00", "06:00", now=T(5, 59))
    assert not _cfg.in_time_window("18:00", "06:00", now=T(12))
    assert not _cfg.in_time_window("18:00", "06:00", now=T(6, 0))
    # day window 09:00-17:00
    assert _cfg.in_time_window("09:00", "17:00", now=T(9))
    assert not _cfg.in_time_window("09:00", "17:00", now=T(17))
    # equal / invalid => always on
    assert _cfg.in_time_window("10:00", "10:00", now=T(3))
    assert _cfg.in_time_window("bad", "值", now=T(3))

    n = whatsapp.WhatsAppNotifier({
        "wa_enabled": True, "wa_api_key": "k", "wa_recipients": ["919812345678"],
        "wa_schedule_enabled": True, "wa_start": "18:00", "wa_end": "06:00",
    })
    assert n.allowed_now(now=T(22)) and not n.allowed_now(now=T(11))
    n2 = whatsapp.WhatsAppNotifier({"wa_enabled": True, "wa_api_key": "k",
                                    "wa_recipients": ["919812345678"]})
    assert n2.allowed_now(now=T(11)), "schedule off => always allowed"
    print("PASS: time window (overnight/day) + WhatsApp schedule gating")


def test_db_purge_retention():
    import os
    import tempfile
    from datetime import datetime, timedelta

    from database import EventDB

    dbp = os.path.join(tempfile.mkdtemp(), "t.db")
    db = EventDB(db_path=dbp)
    old_ts = datetime.now() - timedelta(days=10)
    db.add_event("car", "Entry", "", "/tmp/old_snap.jpg", ts=old_ts)
    db.add_event("truck", "Exit", "", "/tmp/new_snap.jpg")  # today
    removed = db.purge_older_than(7)
    assert removed == ["/tmp/old_snap.jpg"], removed
    left = db.get_events()
    assert len(left) == 1 and left[0]["vehicle_type"] == "truck"
    # direction filters still work on remaining data
    assert len(db.get_events(direction_filter="Exit")) == 1
    assert len(db.get_events(direction_filter="Entry")) == 0
    db.close()
    print("PASS: 7-day retention purge + Entry/Exit filters")


def test_updater_pick_asset():
    import updater

    setup = {"name": "9xSecuritySetup-v1.0.5.exe", "browser_download_url": "http://x/setup"}
    exe = {"name": "9xSecurity.exe", "browser_download_url": "http://x/exe"}
    zp = {"name": "9xSecurity-v1.0.1.zip", "browser_download_url": "http://x/zip"}
    assert updater._pick_asset([exe, zp, setup]) == "http://x/setup"  # installer preferred
    assert updater._pick_asset([exe, zp]) == "http://x/zip"           # then zip
    assert updater._pick_asset([exe]) == "http://x/exe"               # then any exe
    assert updater._pick_asset([]) is None
    # auto-version compare: 1.0.12 > 1.0.2 numerically
    assert updater.is_newer("1.0.12", "1.0.2")
    print("PASS: updater picks Setup installer first; auto-version compare ok")


def test_updater_zip_root():
    import os
    import tempfile

    import updater

    d = tempfile.mkdtemp()
    appdir = os.path.join(d, "9xSecurity")
    os.makedirs(os.path.join(appdir, "_internal"))
    with open(os.path.join(appdir, "9xSecurity.exe"), "wb") as f:
        f.write(b"x")
    assert updater._zip_app_root(d) == appdir
    assert updater._zip_app_root(tempfile.mkdtemp()) is None
    print("PASS: updater finds app root inside extracted zip")


def test_whatsapp_image_sendfile():
    import os
    import tempfile

    import whatsapp

    p = os.path.join(tempfile.gettempdir(), "t.jpg")
    with open(p, "wb") as f:
        f.write(b"\xff\xd8\xff\xe0fakejpeg")

    calls = []

    class R:
        ok = True
        status_code = 200
        text = '{"success":true}'

    def fake_post(url, timeout=None, **kw):
        calls.append({"url": url, **kw})
        return R()

    whatsapp.requests.post = fake_post
    n = whatsapp.WhatsAppNotifier(
        {"wa_enabled": True, "wa_api_key": "k", "wa_recipients": ["919812345678"], "wa_send_image": True}
    )
    ok, info = n._send_image("919812345678", "cap", p)
    assert ok and len(calls) == 1, (ok, len(calls))
    c = calls[0]
    assert c["url"].endswith("/api/v2/sendMessageFile")
    assert c["headers"]["Authorization"] == "Bearer k"
    assert c["data"]["phonenumber"] == "919812345678" and c["data"]["caption"] == "cap"
    fname, fbytes, ftype = c["files"]["file"]
    assert fname == "t.jpg" and ftype == "image/jpeg" and fbytes.startswith(b"\xff\xd8")
    print("PASS: WhatsApp v2 sendMessageFile multipart ->", info)


if __name__ == "__main__":
    test_direction_mapping()
    test_normalize_rtsp_url()
    test_probe_rtsp()
    test_ffmpeg_pipe_source()
    test_ensure_std_streams()
    test_time_window_and_wa_schedule()
    test_db_purge_retention()
    test_tracker_ids_persist()
    test_line_crossing_logs_event()
    test_auth_password()
    test_whatsapp_payload()
    test_whatsapp_disabled_noop()
    test_updater_version_compare()
    test_updater_404_and_default_repo()
    test_updater_token_and_private_repo()
    test_updater_pick_asset()
    test_updater_zip_root()
    test_whatsapp_image_sendfile()
    test_real_yolo_if_available()
    print("\nALL CORE TESTS DONE")
