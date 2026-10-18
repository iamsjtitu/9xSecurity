"""Iteration 42: Daily PDF report (per gate Entry/Exit per vehicle type), Plate Recognizer cloud
plate reader (4-wheelers only, user's own key, fail closed) and the WhatsApp caption/photo number."""
import os
import sys
from datetime import datetime

import cv2
import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import cloud_plate  # noqa: E402
import config  # noqa: E402
import database  # noqa: E402
import engine as eng  # noqa: E402
import report  # noqa: E402
import whatsapp  # noqa: E402


# ---------------------------------------------------------------- report ----
def _ev(vt, d, gate="Main Gate", count=1, plate="", ts="2026-10-18T08:10:00"):
    return {"vehicle_type": vt, "direction": d, "gate": gate, "count": count, "plate": plate, "timestamp": ts}


def test_summarize_per_gate_rows_and_counts():
    evs = [_ev("truck", "Entry"), _ev("truck", "Exit"), _ev("car", "Entry"), _ev("motorcycle", "Entry", "Back"),
           _ev("bicycle", "Exit", "Back"), _ev("person", "Entry", "Back", count=3), _ev("bus", "Exit"), _ev("tractor", "Entry"),
           _ev("van", "Entry"), _ev("car", "weird")]
    s = report.summarize(evs, "2026-10-18")
    assert list(s["gates"]) == ["Main Gate", "Back"]
    assert s["gates"]["Main Gate"]["truck"] == {"Entry": 1, "Exit": 1}
    assert s["gates"]["Back"]["two_wheeler"] == {"Entry": 1, "Exit": 1}
    assert s["gates"]["Back"]["person"] == {"Entry": 3, "Exit": 0}  # group count
    assert s["total"]["other"] == {"Entry": 1, "Exit": 0}  # van
    assert s["total"]["tractor"]["Entry"] == 1
    assert (s["entry"], s["exit"]) == (8, 3) and s["events"] == 10  # 'weird' direction ignored in totals


def test_blank_gate_is_named_gate():
    s = report.summarize([_ev("car", "Entry", gate="")], "2026-10-18")
    assert list(s["gates"]) == ["Gate"]


def test_caption_lists_nonzero_rows_and_totals():
    s = report.summarize([_ev("truck", "Entry"), _ev("truck", "Entry"), _ev("car", "Exit")], "2026-10-18")
    c = report.caption(s)
    assert "Daily Report" in c and "18-10-2026" in c
    assert "Truck: Entry 2 / Exit 0" in c and "Car: Entry 0 / Exit 1" in c
    assert "Bus" not in c and "Total: Entry 2 / Exit 1" in c
    assert "(aaj" not in c
    assert "(aaj 09:30 PM tak" in report.caption(s, partial_until="09:30 PM")


def test_caption_empty_day():
    c = report.caption(report.summarize([], "2026-10-18"))
    assert "koi Entry/Exit record nahi" in c and "Total: Entry 0 / Exit 0" in c


def test_build_pdf_has_tables_and_event_list(tmp_path):
    evs = [_ev("truck", "Entry", plate="MH12AB1234"), _ev("truck", "Exit"), _ev("person", "Entry", "Back", count=2),
           _ev("car", "Entry", gate="Gate \u0915")]  # non-latin gate name must not crash the core font
    s = report.summarize(evs, "2026-10-18")
    p = report.build_pdf(s, evs, str(tmp_path / "r.pdf"), partial_until="09:00 PM")
    raw = open(p, "rb").read()
    assert raw[:5] == b"%PDF-" and len(raw) > 1500
    import fitz

    doc = fitz.open(p)
    text = "\n".join(pg.get_text() for pg in doc)
    assert "9x Security - Daily Report" in text and "All gates" in text and "Gate: Main Gate" in text
    assert "MH12AB1234" in text and "Not detected" in text  # truck without plate
    assert "All events (4)" in text and "(till 09:00 PM)" in text


def test_build_pdf_single_gate_skips_all_gates_table(tmp_path):
    evs = [_ev("car", "Entry")]
    p = report.build_pdf(report.summarize(evs, "2026-10-18"), evs, str(tmp_path / "r.pdf"))
    import fitz

    text = fitz.open(p)[0].get_text()
    assert "All gates" not in text and "Gate: Main Gate" in text


def test_generate_uses_db_rows_and_marks_today_partial(tmp_path, monkeypatch):
    db = database.EventDB(db_path=str(tmp_path / "e.db"))
    now = datetime(2026, 10, 18, 21, 0, 0)
    db.add_event("truck", "Entry", "KA01MJ4567", "/x.jpg", ts=now.replace(hour=9), plate_status="done", gate="G1")
    db.add_event("car", "Exit", "", "/x.jpg", ts=now.replace(hour=10), gate="G1")
    db.add_event("truck", "Entry", "", "/x.jpg", ts=now.replace(day=17, hour=9), gate="G1")  # yesterday: excluded
    monkeypatch.setattr(report, "REPORT_DIR", str(tmp_path / "reports"))
    summary, path, text = report.generate(db, "2026-10-18", now=now)
    assert summary["total"]["truck"]["Entry"] == 1 and summary["exit"] == 1 and summary["events"] == 2
    assert path.endswith("9x_Report_2026-10-18.pdf") and os.path.exists(path)
    assert "(aaj 09:00 PM tak" in text and "KA01MJ4567" not in text  # caption has totals, PDF has plates
    _s, _p, text_y = report.generate(db, "2026-10-17", now=now)
    assert "(aaj" not in text_y and "Truck: Entry 1" in text_y


@pytest.mark.parametrize("now, last, due", [
    (datetime(2026, 10, 18, 20, 59), "", ["2026-10-17"]),              # today's time not reached, yesterday never sent
    (datetime(2026, 10, 18, 21, 0), "", ["2026-10-17", "2026-10-18"]),  # both due
    (datetime(2026, 10, 18, 21, 0), "2026-10-17", ["2026-10-18"]),
    (datetime(2026, 10, 18, 23, 59), "2026-10-18", []),
    (datetime(2026, 10, 19, 8, 0), "2026-10-17", ["2026-10-18"]),      # PC was off at 21:00 -> catch up next morning
])
def test_due_days(now, last, due):
    cfg = {"daily_report_enabled": True, "daily_report_time": "21:00", "daily_report_last": last}
    assert report.due_days(cfg, now=now) == due


def test_due_days_off_or_bad_time():
    assert report.due_days({"daily_report_enabled": False, "daily_report_time": "21:00"}, now=datetime(2026, 10, 18, 22)) == []
    assert report.due_days({"daily_report_enabled": True, "daily_report_time": "9pm"}, now=datetime(2026, 10, 18, 22)) == []
    assert report.due_days({"daily_report_enabled": True, "daily_report_time": "25:00"}, now=datetime(2026, 10, 18, 22)) == []


def test_mark_baseline_and_next_run():
    cfg = {"daily_report_enabled": True, "daily_report_time": "21:00"}
    assert report.mark_baseline(cfg, now=datetime(2026, 10, 18, 20, 0)) == "2026-10-17"
    assert report.next_run(cfg, now=datetime(2026, 10, 18, 20, 0)) == "aaj 09:00 PM"
    assert report.due_days(cfg, now=datetime(2026, 10, 18, 20, 30)) == []  # nothing retroactive
    assert report.due_days(cfg, now=datetime(2026, 10, 18, 21, 0)) == ["2026-10-18"]
    assert report.mark_baseline(cfg, now=datetime(2026, 10, 18, 22, 0)) == "2026-10-18"
    assert report.next_run(cfg, now=datetime(2026, 10, 18, 22, 0)) == "kal 09:00 PM"


# ------------------------------------------------------------ whatsapp ----
def _notifier(**over):
    n = whatsapp.WhatsAppNotifier({**config.DEFAULTS, "wa_enabled": True, "wa_api_key": "k", "wa_recipients": ["919999999999"], **over})
    return n


def test_caption_has_number_for_vehicles_only():
    n = _notifier()
    ev = {"timestamp": "2026-10-18T08:10:00", "direction": "Entry", "vehicle_type": "truck", "category": "vehicle",
          "plate": "MH12AB1234", "plate_status": "done", "gate": "Main"}
    c = n._caption(ev)
    assert "Entry - TRUCK" in c and "Number: MH12AB1234" in c and c.index("Number") < c.index("Gate")
    assert "Number" not in n._caption({**ev, "plate": ""})  # #40: no noisy 'Not detected' line in the text
    assert "Number" not in n._caption({**ev, "category": "person", "vehicle_type": "person", "count": 1})
    assert "Number" not in n._caption({**ev, "category": "two_wheeler", "vehicle_type": "motorcycle", "count": 1})


def test_deliver_sends_pdf_even_when_photos_off(tmp_path, monkeypatch):
    n = _notifier(wa_send_image=False)
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF-1.4 x")
    calls = []

    class R:
        ok, status_code, text = True, 200, '{"data":{"messageId":"1"}}'

    def post(url, **kw):
        calls.append((url, kw["files"]["file"][2] if "files" in kw and "file" in kw["files"] else "text"))
        return R()

    monkeypatch.setattr(whatsapp.requests, "post", post)
    ok, err = n._deliver("919999999999", "cap", str(pdf))
    assert ok and calls[0][0].endswith("/sendMessageFile") and calls[0][1] == "application/pdf"
    jpg = tmp_path / "s.jpg"
    jpg.write_bytes(b"\xff\xd8\xff")
    calls.clear()
    n._deliver("919999999999", "cap", str(jpg))
    assert calls[0][0].endswith("/sendMessage")  # photos off -> plain text for a snapshot


def test_send_report_queues_in_outbox_when_offline(tmp_path, monkeypatch):
    db = database.EventDB(db_path=str(tmp_path / "e.db"))
    n = whatsapp.WhatsAppNotifier({**config.DEFAULTS, "wa_enabled": False, "wa_api_key": "k",
                                   "wa_recipients": ["919999999999"], "wa_groups": [{"id": "120363012345678901@g.us", "name": "G"}]}, db=db)
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF-1.4 x")

    def post(url, **kw):
        raise whatsapp.requests.ConnectionError("Name resolution failed")

    monkeypatch.setattr(whatsapp.requests, "post", post)
    ok, detail = n.send_report("cap", str(pdf))
    assert not ok and "queue me" in detail and db.outbox_count() == 2
    items = db.outbox_pending()
    assert {i["recipient"] for i in items} == {"919999999999", "120363012345678901@g.us"}
    assert all(i["image_path"].endswith("r.pdf") for i in items)


def test_send_report_without_key_or_recipients():
    assert not _notifier(wa_api_key="").send_report("c", "/x.pdf")[0]
    assert not _notifier(wa_recipients=[]).send_report("c", "/x.pdf")[0]


# ------------------------------------------------------------ cloud plate ----
def _resp(status, payload=None, text=""):
    class R:
        pass

    r = R()
    r.status_code = status
    r.text = text or (str(payload) if payload is not None else "")

    def js():
        if payload is None:
            raise ValueError("no json")
        return payload

    r.json = js
    return r


def _payload(plate, score, dscore=0.9, candidates=()):
    return {"results": [{"plate": plate, "score": score, "dscore": dscore,
                         "candidates": [{"plate": p, "score": s} for p, s in candidates],
                         "box": {"xmin": 1, "ymin": 1, "xmax": 50, "ymax": 20}}]}


def test_pick_plate_valid_formats_upper_and_thresholds():
    assert cloud_plate.pick_plate(_payload("mh12ab1234", 0.9))[0] == "MH12AB1234"
    assert cloud_plate.pick_plate(_payload("22bh1234ab", 0.9))[0] == "22BH1234AB"
    assert cloud_plate.pick_plate(_payload("dl8caf5031", 0.61))[0] == "DL8CAF5031"
    plate, d = cloud_plate.pick_plate(_payload("mh12ab1234", 0.59))
    assert plate == "" and "low confidence" in d
    plate, d = cloud_plate.pick_plate(_payload("mh12ab1234", 0.9, dscore=0.2))
    assert plate == "" and "low confidence" in d


def test_pick_plate_rejects_non_indian_and_uses_candidate():
    plate, d = cloud_plate.pick_plate(_payload("abc123", 0.95))
    assert plate == "" and "no valid Indian plate" in d
    assert cloud_plate.pick_plate(_payload("mh12ab12", 0.9, candidates=(("mh12ab1234", 0.88),)))[0] == "MH12AB1234"
    # one O->0 style fix needs a high score
    assert cloud_plate.pick_plate(_payload("mh12ab12O4", 0.9))[0] == "MH12AB1204"
    assert cloud_plate.pick_plate(_payload("mh12ab12O4", 0.7))[0] == ""


def test_pick_plate_empty_and_bad():
    assert cloud_plate.pick_plate({"results": []}) == ("", "no plate in photo")
    assert cloud_plate.pick_plate({"nope": 1})[0] == ""
    assert cloud_plate.pick_plate("junk")[0] == ""


def test_jpeg_caps_width_and_size():
    big = np.random.randint(0, 255, (1200, 3200, 3), np.uint8)
    raw = cloud_plate._jpeg(big)
    assert raw is not None and len(raw) <= cloud_plate.MAX_BYTES
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[1] == 1600
    assert cloud_plate._jpeg(None) is None and cloud_plate._jpeg(np.zeros((0, 0, 3), np.uint8)) is None


@pytest.fixture
def reader(monkeypatch):
    r = cloud_plate.CloudPlateReader("abc")
    monkeypatch.setattr(cloud_plate.time, "sleep", lambda s: None)
    r.sent = []

    def fake_post(url, **kw):
        r.sent.append(kw)
        return r.queue.pop(0)

    monkeypatch.setattr(cloud_plate.requests, "post", fake_post)
    return r


CROP_BIG = np.full((300, 600, 3), 120, np.uint8)
CROP_SMALL = np.full((100, 200, 3), 120, np.uint8)


def test_lookup_request_shape_and_auth(reader):
    reader.queue = [_resp(201, _payload("mh12ab1234", 0.95))]
    plate, detail = reader.read_many([CROP_SMALL, CROP_BIG])
    assert plate == "MH12AB1234" and len(reader.sent) == 1
    kw = reader.sent[0]
    assert kw["headers"]["Authorization"] == "Token abc"
    assert "upload" in kw["files"] and kw["files"]["upload"][2] == "image/jpeg"
    assert kw["data"]["regions"] == "in" and "strict" in kw["data"]["config"]
    # largest crop went first
    sent_img = cv2.imdecode(np.frombuffer(kw["files"]["upload"][1], np.uint8), cv2.IMREAD_COLOR)
    assert sent_img.shape[:2] == (300, 600)


def test_read_many_second_crop_only_when_first_finds_nothing(reader):
    reader.queue = [_resp(201, {"results": []}), _resp(201, _payload("ka01mj4567", 0.9))]
    plate, detail = reader.read_many([CROP_BIG, CROP_SMALL, CROP_SMALL])
    assert plate == "KA01MJ4567" and len(reader.sent) == 2 and "no plate in photo" in detail


def test_read_many_stops_on_bad_key_and_credits(reader):
    for status, text, hint in ((403, '{"detail":"Invalid token."}', "API key galat"), (401, "", "API key galat"),
                               (403, '{"detail":"quota"}', "lookups khatam")):
        reader.sent.clear()
        reader.queue = [_resp(status, None, text), _resp(201, _payload("mh12ab1234", 0.9))]
        plate, detail = reader.read_many([CROP_BIG, CROP_SMALL])
        assert plate == "" and len(reader.sent) == 1 and hint in detail, (status, detail)
        assert reader.last_error


def test_read_many_offline_is_fatal(reader, monkeypatch):
    def boom(url, **kw):
        raise cloud_plate.requests.ConnectionError("getaddrinfo failed")

    monkeypatch.setattr(cloud_plate.requests, "post", boom)
    plate, detail = reader.read_many([CROP_BIG, CROP_SMALL])
    assert plate == "" and "Internet/connection error" in detail


def test_throttled_429_retried_once_then_next_crop(reader):
    reader.queue = [_resp(429, None, "throttled"), _resp(201, _payload("mh12ab1234", 0.9))]
    plate, _ = reader.read_many([CROP_BIG])
    assert plate == "MH12AB1234" and len(reader.sent) == 2
    reader.sent.clear()
    reader.queue = [_resp(429, None, "t"), _resp(429, None, "t"), _resp(201, _payload("mh12ab1234", 0.9))]
    plate, detail = reader.read_many([CROP_BIG, CROP_SMALL])
    assert plate == "MH12AB1234" and len(reader.sent) == 3 and "429" in detail


def test_bad_json_and_server_error(reader):
    reader.queue = [_resp(200, None, "<html>"), _resp(201, _payload("mh12ab1234", 0.9))]
    assert reader.read_many([CROP_BIG, CROP_SMALL])[0] == "MH12AB1234"
    reader.sent.clear()
    reader.queue = [_resp(500, None, "oops"), _resp(201, _payload("mh12ab1234", 0.9))]
    plate, detail = reader.read_many([CROP_BIG, CROP_SMALL])
    assert plate == "" and len(reader.sent) == 1  # 5xx: stop, do not burn credits


def test_statistics(monkeypatch):
    r = cloud_plate.CloudPlateReader("abc")
    monkeypatch.setattr(cloud_plate.requests, "get", lambda url, **kw: _resp(200, {"usage": {"calls": 128, "resets_on": "2026-11-18T00:00:00Z"}, "total_calls": 2500}))
    s = r.statistics()
    assert s["valid"] and (s["used"], s["limit"], s["remaining"]) == (128, 2500, 2372) and "2372 baaki" in s["message"]
    monkeypatch.setattr(cloud_plate.requests, "get", lambda url, **kw: _resp(403, None, '{"detail":"Invalid token."}'))
    s = r.statistics()
    assert not s["valid"] and "API key galat" in s["message"]
    assert not cloud_plate.CloudPlateReader("").statistics()["valid"]
    monkeypatch.setattr(cloud_plate.requests, "get", lambda url, **kw: _resp(200, {"weird": 1}))
    assert not r.statistics()["valid"]


def test_disabled_reader_never_calls(reader):
    reader.update("")
    assert reader.read_many([CROP_BIG]) == ("", "cloud off") and not reader.sent


# ------------------------------------------------------------- engine ----
class _Local:
    def __init__(self, plate="", detail="local-detail"):
        self.plate, self.detail, self.calls = plate, detail, 0

    def read_many(self, crops, budget_s=8.0):
        self.calls += 1
        return self.plate, self.detail


class _Cloud:
    def __init__(self, plate="", detail="cloud-detail"):
        self.plate, self.detail, self.calls, self.enabled = plate, detail, 0, True

    def update(self, key, region="in"):
        pass

    def read_many(self, crops, max_lookups=2):
        self.calls += 1
        return self.plate, self.detail


def _eng(tmp_path, **over):
    from test_categories import _engine

    e = _engine(tmp_path, enable_plate=True, **over)
    return e


def test_read_plate_cloud_first_then_local(tmp_path):
    e = _eng(tmp_path, pr_enabled=True, pr_api_key="k", pr_mode="always")
    e.plate_reader, e.cloud_reader = _Local("MH12AB1234"), _Cloud("")
    plate, detail, src = e._read_plate([CROP_BIG])
    assert (plate, src) == ("MH12AB1234", "ocr") and e.cloud_reader.calls == 1 and e.plate_reader.calls == 1
    e.plate_reader, e.cloud_reader = _Local("XX"), _Cloud("KA01MJ4567")
    plate, detail, src = e._read_plate([CROP_BIG])
    assert (plate, src) == ("KA01MJ4567", "cloud") and e.plate_reader.calls == 0 and detail.startswith("cloud:")


def test_read_plate_fallback_mode_saves_credits(tmp_path):
    e = _eng(tmp_path, pr_enabled=True, pr_api_key="k", pr_mode="fallback")
    e.plate_reader, e.cloud_reader = _Local("MH12AB1234"), _Cloud("KA01MJ4567")
    plate, _, src = e._read_plate([CROP_BIG])
    assert (plate, src) == ("MH12AB1234", "ocr") and e.cloud_reader.calls == 0
    e.plate_reader = _Local("")
    plate, _, src = e._read_plate([CROP_BIG])
    assert (plate, src) == ("KA01MJ4567", "cloud")


def test_read_plate_cloud_off_without_key_or_toggle(tmp_path):
    for over in ({"pr_enabled": True, "pr_api_key": ""}, {"pr_enabled": False, "pr_api_key": "k"}):
        e = _eng(tmp_path, **over)
        e.plate_reader = _Local("MH12AB1234")
        assert e._cloud() is None
        assert e._read_plate([CROP_BIG])[0] == "MH12AB1234"


def test_read_plate_both_fail_is_not_detected_and_errors_are_caught(tmp_path):
    e = _eng(tmp_path, pr_enabled=True, pr_api_key="k")

    class Boom(_Cloud):
        def read_many(self, crops, max_lookups=2):
            raise RuntimeError("down")

    e.plate_reader, e.cloud_reader = _Local(""), Boom()
    plate, detail, src = e._read_plate([CROP_BIG])
    assert (plate, src) == ("", "") and "cloud error: down" in detail and "local:" in detail


def test_ocr_and_notify_stamps_photo_and_caption_has_number(tmp_path):
    e = _eng(tmp_path, pr_enabled=True, pr_api_key="k")
    e.plate_reader, e.cloud_reader = _Local(""), _Cloud("MH12AB1234")
    img = np.zeros((540, 960, 3), np.uint8)
    cv2.rectangle(img, (0, 0), (960, 28), (0, 0, 0), -1)
    snap = tmp_path / "s.jpg"
    cv2.imwrite(str(snap), img)
    sent = []
    e.notifier.notify = lambda ev: sent.append(ev)
    eid = e.db.add_event("truck", "Entry", "", str(snap), plate_status="pending", gate="G")
    ev = {"id": eid, "vehicle_type": "truck", "category": "vehicle", "direction": "Entry", "plate": "",
          "plate_status": "pending", "image_path": str(snap), "timestamp": "2026-10-18T08:00:00", "gate": "G"}
    e._ocr_and_notify(eid, 1, ev, [CROP_BIG])
    assert sent and sent[0]["plate"] == "MH12AB1234" and sent[0]["plate_source"] == "cloud"
    row = e.db.get_events()[0]
    assert row["plate"] == "MH12AB1234" and row["plate_source"] == "cloud" and row["plate_status"] == "done"
    after = cv2.imread(str(snap))
    assert after[:28, 700:].max() > 150  # text drawn on the right of the caption bar
    n = _notifier()
    assert "Number: MH12AB1234" in n._caption(sent[0])


def test_stamp_plate_not_detected_and_missing_file(tmp_path):
    assert eng.stamp_plate(str(tmp_path / "nope.jpg"), "X") is False
    img = np.zeros((540, 960, 3), np.uint8)
    p = tmp_path / "s.jpg"
    cv2.imwrite(str(p), img)
    assert eng.stamp_plate(str(p), "") is True
    after = cv2.imread(str(p))
    assert after[:28, 700:].max() > 100 and after[40:, :].max() == 0


def test_ocr_only_for_vehicles(tmp_path):
    """persons / two-wheelers never queue OCR -> never reach the cloud."""
    e = _eng(tmp_path, pr_enabled=True, pr_api_key="k", enable_person=True, enable_two_wheeler=True)
    e.cloud_reader = _Cloud("MH12AB1234")
    queued = []
    e._queue_ocr = lambda eid, tid, ev, crops: queued.append(ev["category"])
    frame = np.zeros((540, 960, 3), np.uint8)
    for cat, label in (("person", "person"), ("two_wheeler", "motorcycle"), ("vehicle", "car")):
        cr = {"label": label, "bbox": (100, 100, 200, 220), "track_id": 1, "to_side": 1, "which": 1}
        e._emit_crossing(frame, None, cr, cat, 960, 540)
    assert queued == ["vehicle"], queued
