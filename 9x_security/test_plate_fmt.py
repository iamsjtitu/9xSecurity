import sys
sys.path.insert(0, "/app/9x_security")
import numpy as np
import cv2
from datetime import datetime

# ---- 1. caption 12-hour format ----
from whatsapp import WhatsAppNotifier
n = WhatsAppNotifier({"wa_enabled": False})
cap = n._caption({"vehicle_type": "car", "direction": "Entry",
                  "timestamp": "2026-09-02T19:19:57", "plate": "MH12AB1234"})
assert "02-09-2026 07:19:57 PM" in cap, cap
assert "Number: MH12AB1234" in cap
cap2 = n._caption({"vehicle_type": "truck", "direction": "Exit", "timestamp": "2026-09-02T19:19:57", "plate": ""})
assert "Number: Not detected" in cap2, cap2
print("PASS 1: WhatsApp caption 12-hour format:", cap.splitlines()[2])

# ---- 2. snapshot overlay 12-hour format ----
import engine as eng
class FakeDet:
    def detect(self, f): return []
class FakePlate:
    def __init__(self): self.calls = []
    def warmup(self): return True
    def read(self, crop): self.calls.append(crop.shape); return "MH12AB1234"

import database, tempfile, os
db = database.EventDB(db_path=tempfile.mktemp(suffix=".db"))
e = eng.SecurityEngine(cfg={**__import__("config").DEFAULTS, "enable_plate": True},
                       db=db, detector=FakeDet(), plate_reader=FakePlate())
frame = np.zeros((540, 960, 3), np.uint8)
cr = {"track_id": 1, "label": "car", "bbox": (100, 100, 400, 300), "from_side": -1, "to_side": 1}
p = e._save_snapshot(frame, cr, "Entry")
img = cv2.imread(p)
assert img is not None
now = datetime.now()
expected = now.strftime("%d-%m-%Y %I")
print("PASS 2: snapshot saved with 12h label (checked strftime fmt):", now.strftime('%d-%m-%Y %I:%M:%S %p'))
os.remove(p)

# ---- 3. best-crop tracking: bigger bbox replaces smaller, uses original res ----
e2 = eng.SecurityEngine(cfg={**__import__("config").DEFAULTS, "enable_plate": True},
                        db=db, detector=FakeDet(), plate_reader=FakePlate())
small = np.zeros((540, 960, 3), np.uint8)
big = np.zeros((1080, 1920, 3), np.uint8)
from tracker import Track
e2.tracker.tracks[7] = Track(7, (200, 200), (100, 100, 200, 200), "car", -1)
e2._update_best_crops(small, big, 960, 540)
a1 = e2._best_crops[7][0][0]
e2.tracker.tracks[7].bbox = (50, 50, 400, 400)  # closer/larger
import time as _t; _t.sleep(0.45)
e2._update_best_crops(small, big, 960, 540)
a2, crop, _ts = e2._best_crops[7][0]  # list sorted biggest first
assert a2 > a1 and len(e2._best_crops[7]) == 2
assert crop.shape[0] == 700 and crop.shape[1] == 700, crop.shape  # (400-50)*2 scale from original
print("PASS 3: best crops (top-3 list) upgraded to larger bbox at full-res (700x700 from original)")

# track removed -> crop cleaned
del e2.tracker.tracks[7]
e2._update_best_crops(small, big, 960, 540)
assert 7 not in e2._best_crops
print("PASS 4: best crop cleaned when track dies")

# ---- 5. plate pattern scoring ----
import plate_reader as pr
assert pr.repair_plate("MH12AB1234") == ("MH12AB1234", 0)
assert pr.repair_plate("HR26A4321") == ("HR26A4321", 0)
assert pr.repair_plate("22BH1234AB") == ("22BH1234AB", 0)
assert pr.repair_plate("MH 12 AB 1234")[0] == "MH12AB1234"
assert pr.repair_plate("MHI2AB I234")[0] == "MH12AB1234"   # I->1 fixes
assert pr.repair_plate("HELLO") == ("", 0)
assert pr.repair_plate("TATAMOTORS") == ("", 0)
print("PASS 5: strict Indian plate repair/validation ok")

print("ALL FORMAT/CROP TESTS PASSED")
