"""Plate status/source columns, OCR -> toast update, manual correction API."""
import os
import sqlite3
import sys
import tempfile
import time

import numpy as np
import pytest
import requests

sys.path.insert(0, os.path.dirname(__file__))
import config  # noqa: E402
import database  # noqa: E402
import engine as eng  # noqa: E402

BASE = f"http://127.0.0.1:{os.environ.get('ENGINE_PORT', '8971')}"
http = requests.Session()  # immune to test_engine.py's global requests.post patch


def test_old_db_is_migrated_with_plate_columns():
    path = tempfile.mktemp(suffix=".db")
    c = sqlite3.connect(path)
    c.execute("""CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                 date TEXT NOT NULL, time TEXT NOT NULL, vehicle_type TEXT NOT NULL, direction TEXT NOT NULL,
                 plate TEXT, image_path TEXT NOT NULL)""")
    c.execute("INSERT INTO events (timestamp,date,time,vehicle_type,direction,plate,image_path) "
              "VALUES ('2026-09-01T10:00:00','2026-09-01','10:00:00','car','Entry','','')")
    c.commit()
    c.close()
    db = database.EventDB(db_path=path)
    rows = db.get_events(date_filter="2026-09-01")
    assert rows[0]["plate_status"] == "" and rows[0]["plate_source"] == ""
    eid = db.add_event("truck", "Exit", "", "", plate_status="pending")
    row = db.update_event_plate(eid, "MH12AB1234", source="manual", status="done")
    assert row["plate"] == "MH12AB1234" and row["plate_source"] == "manual" and row["plate_status"] == "done"
    assert db.update_event_plate(eid, "", source="manual")["plate_source"] == ""  # cleared -> no source
    assert db.update_event_plate(999999, "X") is None
    db.update_event_plate(eid, "MH12AB1234")
    assert db.get_events(plate_filter="mh12")[0]["id"] == eid  # searchable


def test_engine_emits_pending_then_done_event():
    class Det:
        def __init__(self):
            self.x = 310

        def detect(self, f):
            self.x += 60
            return [{"bbox": (self.x, 200, self.x + 120, 320), "label": "car"}]

    class Plate:
        def warmup(self):
            return True

        def read_many(self, crops, budget_s=8.0):
            time.sleep(0.2)
            return "KA01MJ2022", "fake"

    db = database.EventDB(db_path=tempfile.mktemp(suffix=".db"))
    cfg = {**config.DEFAULTS, "enable_plate": True, "detect_frame_skip": 1,
           "line": {"x1": 0.5, "y1": 0.0, "x2": 0.5, "y2": 1.0}}
    e = eng.SecurityEngine(cfg=cfg, db=db, detector=Det(), plate_reader=Plate())
    e.notifier.enabled = False
    seen = []
    e.on_event = seen.append
    frame = np.zeros((540, 960, 3), np.uint8)
    for _ in range(8):
        e.process_frame(frame)
    t0 = time.time()
    while len(seen) < 2 and time.time() - t0 < 5:
        time.sleep(0.05)
    assert len(seen) == 2, seen
    assert seen[0]["plate"] == "" and seen[0]["plate_status"] == "pending"
    assert seen[1]["id"] == seen[0]["id"] and seen[1]["plate"] == "KA01MJ2022"
    assert seen[1]["plate_status"] == "done" and seen[1]["plate_source"] == "ocr"
    row = db.get_events(plate_filter="KA01MJ2022")[0]
    assert row["plate_status"] == "done" and row["plate_source"] == "ocr"
    if os.path.exists(seen[0]["image_path"]):
        os.remove(seen[0]["image_path"])


@pytest.fixture(scope="module")
def token():
    try:
        r = http.post(f"{BASE}/api/login", json={"username": "admin", "password": "9xsecurity"}, timeout=5)
    except requests.RequestException:
        pytest.skip("engine service not running")
    if r.status_code != 200:
        pytest.skip("default login unavailable")
    return r.json()["token"]


def test_manual_plate_api(token):
    h = {"X-Auth-Token": token}
    db = database.EventDB(db_path=os.path.join(os.environ.get("NX_DATA_DIR") or os.path.dirname(__file__), "events.db"))  # service DB
    eid = db.add_event("truck", "Entry", "", "", plate_status="done")
    try:
        r = http.post(f"{BASE}/api/events/{eid}/plate", json={"plate": "mh 12-ab 1234"}, headers=h, timeout=5)
        assert r.status_code == 200, r.text
        ev = r.json()["event"]
        assert ev["plate"] == "MH12AB1234" and ev["plate_source"] == "manual" and ev["plate_status"] == "done"
        rows = http.get(f"{BASE}/api/events?all=1&plate=12AB", headers=h, timeout=5).json()["events"]
        assert any(x["id"] == eid for x in rows)  # searchable
        r = http.post(f"{BASE}/api/events/{eid}/plate", json={"plate": "A1"}, headers=h, timeout=5)
        assert r.status_code == 400
        r = http.post(f"{BASE}/api/events/{eid}/plate", json={"plate": ""}, headers=h, timeout=5)
        assert r.status_code == 200 and r.json()["event"]["plate"] == "" and r.json()["event"]["plate_source"] == ""
        assert http.post(f"{BASE}/api/events/99999999/plate", json={"plate": "MH12AB1234"}, headers=h, timeout=5).status_code == 404
        assert http.post(f"{BASE}/api/events/{eid}/plate", json={"plate": "MH12AB1234"}, timeout=5).status_code == 401
    finally:
        db.conn.execute("DELETE FROM events WHERE id=?", (eid,))
        db.conn.commit()
