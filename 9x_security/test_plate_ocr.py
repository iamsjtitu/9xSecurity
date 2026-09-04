"""Plate OCR strictness + accuracy on synthetic plates (real EasyOCR, CPU).
Plates are rendered with a real TTF font (tests/synth_plate.py): OpenCV's Hershey
stroke fonts are read wrongly by every OCR (3 -> 5, 2 -> Z) and prove nothing."""
import os
import sys

import cv2
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tests"))
from plate_reader import PlateReader, _group_lines, repair_plate  # noqa: E402
from synth_plate import plate_img as _plate_img, vehicle_with_plate as _vehicle_with_plate  # noqa: E402


def test_repair_plate_confusions_and_rejections():
    assert repair_plate("MH12AB1234") == ("MH12AB1234", 0)
    assert repair_plate("MH12 AB 1234") == ("MH12AB1234", 0)
    assert repair_plate("MHI2AB1234")[0] == "MH12AB1234"      # I -> 1 in digit slot
    assert repair_plate("MH12A8I234")[0] == "MH12AB1234"      # 8->B, I->1
    assert repair_plate("0L07CD5678")[0] == "DL07CD5678"      # 0 -> D via valid state code
    assert repair_plate("OL07CD5678")[0] == "DL07CD5678"      # OL invalid state -> DL
    assert repair_plate("XX12AB1234") == ("", 0)               # no such state code -> rejected
    assert repair_plate("DL7CA1234") == ("DL7CA1234", 0)       # 1-digit district
    assert repair_plate("KA01MJ2022") == ("KA01MJ2022", 0)
    assert repair_plate("22BH1234AB") == ("22BH1234AB", 0)
    assert repair_plate("TATA") == ("", 0)
    assert repair_plate("TATAMOTORS") == ("", 0)
    assert repair_plate("INDIA1234567") == ("", 0)
    assert repair_plate("ASHOKLEYLAND") == ("", 0)
    assert repair_plate("12345678") == ("", 0)                 # cannot be made valid within 3 fixes
    assert repair_plate("MH12AB12") == ("", 0)                 # too short
    assert repair_plate("MHIZAB1254") == ("MH12AB1254", 2)     # common layout beats rare MH1ZAB.. (1 fix)
    assert repair_plate("DL0ZCD5678") == ("DL02CD5678", 1)     # district '0' impossible -> 2-digit layout


def test_plate_localizer_finds_real_gate_plates():
    """User's real gate snapshots (tests/real/*_crop.png): the localizer must propose
    the plate rectangle among its candidates (known plate positions)."""
    from plate_reader import find_plate_regions, _iou
    real = os.path.join(os.path.dirname(__file__), "tests", "real")
    expected = {  # crop file -> approximate plate box (x1, y1, x2, y2)
        "wa1_crop.png": (276, 557, 389, 592),   # Bolero, yellow single-row
        "wa3_crop.png": (185, 603, 325, 650),   # Eicher, yellow, tilted
        "wa4_crop.png": (25, 575, 85, 643),     # Tata, yellow two-row
    }
    for name, box in expected.items():
        img = cv2.imread(os.path.join(real, name))
        regs = find_plate_regions(img)
        assert any(_iou(r, box) > 0.3 for r in regs), (name, regs)


def test_read_many_respects_budget_on_real_crop():
    pr = PlateReader()
    assert pr.warmup(), pr.last_error
    img = cv2.imread(os.path.join(os.path.dirname(__file__), "tests", "real", "wa3_crop.png"))
    import time
    t0 = time.time()
    plate, detail = pr.read_many([img, img, img], budget_s=6)
    took = time.time() - t0
    assert took < 14, took                      # budget + at most one heavy pass
    assert plate in ("", "OD08U5777"), (plate, detail)  # never a wrong number


def test_group_lines_merges_two_row_plate():
    res = [
        ([[0, 0], [100, 0], [100, 30], [0, 30]], "MH12", 0.9),
        ([[0, 40], [120, 40], [120, 70], [0, 70]], "AB1234", 0.8),
        ([[400, 0], [480, 0], [480, 30], [400, 30]], "TATA", 0.95),
    ]
    cands = dict(_group_lines(res))
    assert "MH12AB1234" in cands and abs(cands["MH12AB1234"] - 0.85) < 1e-6
    assert "MH12" in cands and "TATA" in cands


def test_read_many_acceptance_rules(monkeypatch):
    """Doubtful reads must come back as '' (Not detected), never a guessed number."""
    pr = PlateReader()
    dummy = np.zeros((10, 10, 3), np.uint8)

    def fake(seq):
        it = iter(seq)
        monkeypatch.setattr(pr, "candidates", lambda crop, deadline=None: next(it))

    fake([[("MH12AB1234", 0.8, 0)]])
    assert pr.read_many([dummy])[0] == "MH12AB1234"            # clean, confident read
    fake([[("MH12AB1234", 0.6, 0)]])
    assert pr.read_many([dummy])[0] == ""                      # clean but low-res confidence -> doubt
    fake([[("MH12AB1234", 0.7, 1)]])
    assert pr.read_many([dummy])[0] == ""                      # 1 fix + medium conf -> doubt (OD68.. case)
    fake([[("MH12AB1234", 0.9, 2)]])
    assert pr.read_many([dummy])[0] == "MH12AB1234"            # slot-constrained fixes, very confident
    fake([[("MH12AB1234", 0.9, 3)]])
    assert pr.read_many([dummy])[0] == ""                      # 3 fixes, single crop -> doubt
    fake([[("MH12AB1234", 0.4, 2)], [("MH12AB1234", 0.6, 1)]])
    plate, detail = pr.read_many([dummy, dummy])
    assert plate == "MH12AB1234" and "votes=2" in detail       # two crops agree
    fake([[("MH12AB1234", 0.4, 2)], [("MH12AB1234", 0.4, 1)]])
    assert pr.read_many([dummy, dummy])[0] == ""               # two crops agree but both very weak
    fake([[("MH12AB1234", 0.6, 0), ("MH12AB1284", 0.6, 0)]])
    plate, detail = pr.read_many([dummy])
    assert plate == "" and "ambiguous" in detail               # two equally good candidates
    fake([[]])
    assert pr.read_many([dummy]) == ("", "no valid plate text")


def test_engine_runs_one_ocr_job_at_a_time():
    import tempfile
    import threading
    import time

    import config
    import database
    import engine as eng

    class Det:
        def detect(self, f):
            return []

    class Slow:
        def __init__(self):
            self.active = 0
            self.peak = 0
            self.done = 0
            self.lock = threading.Lock()

        def warmup(self):
            return True

        def read_many(self, crops, budget_s=8.0):
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            time.sleep(0.3)
            with self.lock:
                self.active -= 1
                self.done += 1
            return ("MH12AB1234" if crops else ""), "fake"

    db = database.EventDB(db_path=tempfile.mktemp(suffix=".db"))
    slow = Slow()
    e = eng.SecurityEngine(cfg={**config.DEFAULTS, "enable_plate": True}, db=db, detector=Det(), plate_reader=slow)
    e.notifier.enabled = False
    crop = np.zeros((50, 50, 3), np.uint8)
    ids = [db.add_event("car", "Entry", "", "") for _ in range(3)]
    for eid in ids:
        e._queue_ocr(eid, eid, {"id": eid, "plate": ""}, [crop])
    t0 = time.time()
    while slow.done < 3 and time.time() - t0 < 5:
        time.sleep(0.05)
    assert slow.done == 3 and slow.peak == 1, (slow.done, slow.peak)
    assert db.conn.execute("SELECT plate FROM events WHERE id=?", (ids[-1],)).fetchone()["plate"] == "MH12AB1234"


@pytest.mark.parametrize("lines,expected", [
    (["MH12AB1234"], "MH12AB1234"),
    (["DL07CD5678"], "DL07CD5678"),
    (["KA01", "MJ2022"], "KA01MJ2022"),  # two-row plate
])
def test_easyocr_reads_synthetic_plates(lines, expected):
    pr = PlateReader()
    assert pr.warmup(), pr.last_error
    veh = _vehicle_with_plate(_plate_img(lines), extra_text="TATA")
    plate, detail = pr.read_many([veh], budget_s=30)
    assert plate == expected, (plate, detail, pr.last_trace)


def test_easyocr_handles_small_big_and_blurred_crops():
    pr = PlateReader()
    assert pr.warmup(), pr.last_error
    small = _vehicle_with_plate(_plate_img(["HR26DK8337"]), size=(320, 240), plate_scale=0.45)
    big = cv2.resize(_vehicle_with_plate(_plate_img(["UP32BH3311"])), (1000, 740))
    blur = cv2.GaussianBlur(_vehicle_with_plate(_plate_img(["GJ05RT2398"], yellow=True)), (0, 0), 1.5)
    # a small far-away plate reads with medium confidence: production always has several
    # crops of the same vehicle, so two agreeing reads are what makes it acceptable
    for crops, exp in (([small, small], "HR26DK8337"), ([big], "UP32BH3311"), ([blur], "GJ05RT2398")):
        plate, detail = pr.read_many(crops, budget_s=30)
        assert plate == exp, (plate, detail, pr.last_trace)


def test_easyocr_truncated_plate_is_not_detected():
    """A plate cut off at the crop edge must not be 'repaired' into a wrong number."""
    pr = PlateReader()
    assert pr.warmup(), pr.last_error
    p = _plate_img(["MH12AB1234"])
    cut = p[:, : int(p.shape[1] * 0.78)]  # last digit(s) missing
    veh = _vehicle_with_plate(cut)
    plate, detail = pr.read_many([veh], budget_s=30)
    assert plate == "", (plate, detail, pr.last_trace)


def test_easyocr_returns_not_detected_for_non_plate_text():
    pr = PlateReader()
    assert pr.warmup(), pr.last_error
    veh = np.full((520, 700, 3), (90, 95, 100), np.uint8)
    cv2.putText(veh, "ASHOK LEYLAND", (40, 300), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (235, 235, 235), 5)
    cv2.putText(veh, "GOODS CARRIER", (60, 420), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (235, 235, 235), 4)
    plate, detail = pr.read_many([veh], budget_s=30)
    assert plate == "", (plate, detail)
    blank = np.full((300, 500, 3), (70, 70, 70), np.uint8)
    assert pr.read_many([blank], budget_s=10)[0] == ""


def test_voting_prefers_plate_seen_in_two_crops():
    pr = PlateReader()
    assert pr.warmup(), pr.last_error
    a = _vehicle_with_plate(_plate_img(["MH12AB1234"]))
    b = _vehicle_with_plate(_plate_img(["MH12AB1234"], yellow=True))
    plate, detail = pr.read_many([a, b], budget_s=40)
    assert plate == "MH12AB1234" and "votes=2" in detail, detail
