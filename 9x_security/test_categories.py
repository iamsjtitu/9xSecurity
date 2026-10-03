"""Part 1 of the USER (12-09) request: detect Person + Two-wheeler with independent ON/OFF
toggles, per-category time windows (outside window => not counted at all), and category-specific
WhatsApp captions (👤 Person — N persons / 🏍️ Two-wheeler)."""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import config  # noqa: E402
import database  # noqa: E402
import engine as eng  # noqa: E402
from detector import category_of  # noqa: E402
from whatsapp import WhatsAppNotifier  # noqa: E402


class _ScriptDet:
    dets = []

    def detect(self, f):
        return list(self.dets)


def _engine(tmp_path, **over):
    db = database.EventDB(db_path=str(tmp_path / "c.db"))
    cfg = {**config.DEFAULTS, "enable_plate": False, "detect_frame_skip": 1,
           "line": {"x1": 700 / 960, "y1": 119 / 540, "x2": 850 / 960, "y2": 143 / 540}, **over}
    e = eng.SecurityEngine(cfg=cfg, db=db, detector=_ScriptDet(), plate_reader=None)
    e.notifier.enabled = False
    return e


def _drive(e, dets_per_frame, flush=True):
    import numpy as np
    frame = np.zeros((540, 960, 3), np.uint8)
    out = []
    for dets in dets_per_frame:
        e.detector.dets = dets
        out += e.process_frame(frame)[1]
    if flush:
        out += e.flush_pending(force=True)  # person alerts wait PERSON_GROUP_S for the group
    return out


def _cross(label, x=780, extra=0):
    return [[{"bbox": (x - 60 + extra, y - 40, x + 60 + extra, y + 40), "label": label}] for y in range(60, 300, 6)]


def test_category_of_mapping():
    assert category_of("person") == "person"
    assert category_of("motorcycle") == "two_wheeler" and category_of("bicycle") == "two_wheeler"
    assert category_of("car") == "vehicle" and category_of("truck") == "vehicle"


def test_allowed_classes_from_toggles():
    base = {"vehicle_classes": ["car", "truck", "bus"]}
    assert set(config.allowed_classes(base)) == {"car", "truck", "bus"}
    assert "person" in config.allowed_classes({**base, "enable_person": True})
    two = config.allowed_classes({**base, "enable_two_wheeler": True})
    assert "motorcycle" in two and "bicycle" in two
    assert config.allowed_classes({"vehicle_classes": []}) == []  # everything off = detect nothing


def test_person_toggle_off_no_event_on_toggle_counts(tmp_path):
    e = _engine(tmp_path, enable_person=False)
    assert _drive(e, _cross("person")) == []            # person off -> ignored even if detected
    e2 = _engine(tmp_path, enable_person=True)
    ev = _drive(e2, _cross("person"))
    assert len(ev) == 1 and ev[0]["category"] == "person"
    for x in ev:
        if os.path.exists(x["image_path"]):
            os.remove(x["image_path"])


def test_two_wheeler_toggle(tmp_path):
    assert _drive(_engine(tmp_path, enable_two_wheeler=False), _cross("motorcycle")) == []
    ev = _drive(_engine(tmp_path, enable_two_wheeler=True), _cross("motorcycle"))
    assert len(ev) == 1 and ev[0]["category"] == "two_wheeler"
    for x in ev:
        if os.path.exists(x["image_path"]):
            os.remove(x["image_path"])


def test_person_group_is_one_event_with_count(tmp_path):
    """Two people crossing side by side (slanted line => different frames) -> ONE alert, count 2.
    Nothing is emitted while the group window is still open."""
    e = _engine(tmp_path, enable_person=True)
    frames = []
    for y in range(60, 300, 6):
        frames.append([{"bbox": (700, y - 40, 760, y + 40), "label": "person"},
                       {"bbox": (820, y - 40, 880, y + 40), "label": "person"}])
    assert _drive(e, frames, flush=False) == []
    ev = e.flush_pending(force=True)
    assert len(ev) == 1 and ev[0]["count"] == 2, ev
    assert ev[0]["vehicle_type"] == "person" and os.path.exists(ev[0]["image_path"])
    for x in ev:
        if os.path.exists(x["image_path"]):
            os.remove(x["image_path"])


def test_person_group_flushes_after_quiet_time_and_separate_people_are_separate(tmp_path, monkeypatch):
    """Group closes PERSON_GROUP_S after the last person; someone arriving later = new alert."""
    import numpy as np
    e = _engine(tmp_path, enable_person=True)
    frame = np.zeros((540, 960, 3), np.uint8)
    clock = [1000.0]
    monkeypatch.setattr(eng.time, "time", lambda: clock[0])
    out = []
    for y in range(60, 300, 6):
        e.detector.dets = [{"bbox": (720, y - 40, 840, y + 40), "label": "person"}]
        out += e.process_frame(frame)[1]
        clock[0] += 0.03
    assert out == []                      # within the 2.5 s window: still waiting
    clock[0] += eng.PERSON_GROUP_S + 0.1
    e.detector.dets = []
    out += e.process_frame(frame)[1]      # next frame after the quiet time -> emitted
    assert len(out) == 1 and out[0]["count"] == 1
    for y in range(60, 300, 6):           # a second person much later -> its own alert
        e.detector.dets = [{"bbox": (720, y - 40, 840, y + 40), "label": "person"}]
        out += e.process_frame(frame)[1]
        clock[0] += 0.03
    out += e.flush_pending(force=True)
    assert len(out) == 2 and all(x["count"] == 1 for x in out)
    for x in out:
        if os.path.exists(x["image_path"]):
            os.remove(x["image_path"])


def test_category_schedule_blocks_counting(tmp_path):
    """person schedule 06:00-07:00; a crossing 'now' (outside that) must not be counted."""
    now = datetime.now()
    far_start = f"{(now.hour + 2) % 24:02d}:00"
    far_end = f"{(now.hour + 3) % 24:02d}:00"
    e = _engine(tmp_path, enable_person=True,
                cat_schedules={**config.DEFAULTS["cat_schedules"],
                               "person": {"enabled": True, "start": far_start, "end": far_end}})
    assert _drive(e, _cross("person")) == []
    # vehicle has no schedule -> still counts
    ev = _drive(e, _cross("car"))
    assert len(ev) == 1
    for x in ev:
        if os.path.exists(x["image_path"]):
            os.remove(x["image_path"])


def _cap(category, direction, count=1, vehicle_type="", gate=""):
    n = WhatsAppNotifier({"wa_enabled": True, "wa_base_url": "x", "wa_api_key": "k", "wa_recipients": ["919"]})
    return n._caption({"category": category, "direction": direction, "count": count,
                       "vehicle_type": vehicle_type, "gate": gate,
                       "timestamp": "2026-09-12T15:14:09"})


def test_captions_per_category():
    assert "👤 Person Entry — 2 persons" in _cap("person", "Entry", 2)
    assert "👤 Person Exit — 1 person" in _cap("person", "Exit", 1)
    assert "🏍️ Two-wheeler Exit" in _cap("two_wheeler", "Exit")
    veh = _cap("vehicle", "Entry", vehicle_type="truck")
    assert "Entry - TRUCK" in veh
    assert "Gate: Main Gate" in _cap("vehicle", "Entry", vehicle_type="car", gate="Main Gate")
    assert "Gate:" not in _cap("vehicle", "Entry", vehicle_type="car")
    assert "Time: 12-09-2026 03:14:09 PM" in veh
