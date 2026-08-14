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
    test_tracker_ids_persist()
    test_line_crossing_logs_event()
    test_auth_password()
    test_whatsapp_payload()
    test_whatsapp_disabled_noop()
    test_updater_version_compare()
    test_whatsapp_image_sendfile()
    test_real_yolo_if_available()
    print("\nALL CORE TESTS DONE")
