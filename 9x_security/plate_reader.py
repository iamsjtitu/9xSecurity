"""9x Security - Number plate reader (EasyOCR, offline). Optional feature.

Strict by design: a plate is reported ONLY when the text can be made to fit a
valid Indian registration format (after fixing classic OCR confusions such as
O/0, I/1, S/5, B/8). Anything else -> '' ("Not detected"), never a wrong number.
"""
import os
import re
import sys
import threading
import time

import cv2
import numpy as np

_CLEAN_RE = re.compile(r"[^A-Z0-9]")
# Indian plate formats: MH12AB1234 / MH12A1234 / MH121234 (old, no series) / 22BH1234AB (Bharat)
_STD_RE = re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{4}$")
_BH_RE = re.compile(r"^\d{2}BH\d{4}[A-Z]{1,2}$")
ALLOW = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
MIN_CONF = 0.30   # mean OCR confidence a candidate must reach
MAX_FIXES = 3     # at most this many confusion substitutions
MAX_W = 640       # OCR input width cap for the whole plate zone (CRAFT cost grows ~quadratically)
REFINE_W = 560    # zoomed re-read width for each detected text box
PLATE_W = 480     # localized plate rectangles are upscaled to this width (<=4x; recognizer works at 64px rows)
HEAVY_PASS_S = 3.0    # a whole-zone/whole-crop OCR pass needs about this long on a slow CPU
ACCEPT_CLEAN = 0.70   # single crop, 0 fixes: min confidence (a 0.63 read was OD68.. for OD08..)
ACCEPT_FIXED = 0.85   # single crop, 1-2 slot-constrained fixes (I->1, O->0): min confidence
ACCEPT_VOTED = 0.55   # seen in 2 crops: min confidence

_TO_DIGIT = {"O": "0", "Q": "0", "D": "0", "U": "0", "I": "1", "L": "1", "Z": "2", "S": "5",
             "B": "8", "G": "6", "T": "7", "A": "4"}
_TO_ALPHA = {"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B", "6": "G", "7": "T", "4": "A"}
# Indian state / UT codes (first two letters of a standard plate)
STATE_CODES = {"AN", "AP", "AR", "AS", "BR", "CG", "CH", "DD", "DL", "DN", "GA", "GJ", "HP", "HR",
               "JH", "JK", "KA", "KL", "LA", "LD", "MH", "ML", "MN", "MP", "MZ", "NL", "OD", "OR",
               "PB", "PY", "RJ", "SK", "TN", "TR", "TS", "UK", "UA", "UP", "WB"}
# look-alike letters for the state slot (OCR confusions), cost 1 each
_STATE_ALT = {"0": "ODQ", "O": "DQ", "D": "O", "Q": "O", "1": "IL", "I": "L", "L": "I", "5": "S",
              "8": "BR", "B": "R", "R": "B", "2": "Z", "6": "G", "G": "C", "C": "G", "4": "A",
              "7": "T", "U": "V", "V": "U", "K": "X", "X": "K", "M": "N", "N": "M", "E": "F", "F": "E"}


def _best_state(two):
    """(state_code, fixes) for the first two chars, or (None, 0) if no valid code is reachable."""
    opts = []
    for ch in two:
        alts = [(ch, 0)] if ch.isalpha() else []
        alts += [(a, 1) for a in _STATE_ALT.get(ch, "")]
        opts.append(alts)
    best, best_cost = None, 99
    for a, ca in opts[0]:
        for b, cb in opts[1]:
            if a + b in STATE_CODES and ca + cb < best_cost:
                best, best_cost = a + b, ca + cb
    return best, (best_cost if best else 0)


def _coerce(s, kinds, allow_fix=True):
    """Force each char of s to the class in kinds ('A'=letter,'D'=digit).
    Returns (fixed, n_substitutions) or (None, _) if impossible."""
    out, fixes = [], 0
    for ch, k in zip(s, kinds):
        if k == "D":
            if ch.isdigit():
                out.append(ch)
            elif allow_fix and ch in _TO_DIGIT:
                out.append(_TO_DIGIT[ch])
                fixes += 1
            else:
                return None, 0
        else:
            if ch.isalpha():
                out.append(ch)
            elif allow_fix and ch in _TO_ALPHA:
                out.append(_TO_ALPHA[ch])
                fixes += 1
            else:
                return None, 0
    return "".join(out), fixes


def repair_plate(raw):
    """Best valid plate for a raw OCR string, or ('', 0). Tries every legal
    layout of the Indian formats and keeps the one needing the fewest fixes."""
    s = _CLEAN_RE.sub("", str(raw or "").upper())
    n = len(s)
    if not 8 <= n <= 11:
        return "", 0
    best, best_fix, best_cost = "", 99, 99.0
    # Standard: 2 letters (valid state code), 1-2 digits, 0-3 letters, 4 digits
    state, sfix = _best_state(s[:2])
    if state:
        for d in (1, 2):
            series = n - 2 - d - 4
            if not 0 <= series <= 3:
                continue
            dist, f1 = _coerce(s[2:2 + d], "D" * d)
            if dist == "0":
                continue  # district '0' does not exist (a 1-digit district is 1-9)
            ser, f2 = _coerce(s[2 + d:2 + d + series], "A" * series) if series else ("", 0)
            # old format without series letters is easy to fabricate from a truncated
            # read (MH12AB12 -> MH124812): accept it only when the digits are clean.
            tail, f3 = _coerce(s[2 + d + series:], "DDDD", allow_fix=series > 0)
            if dist is None or ser is None or tail is None:
                continue
            if f3 > 1 or f2 > 1 or f1 + f2 + f3 + sfix > MAX_FIXES:
                continue  # real OCR slips are 1-2 chars; more than that is a different string
            fixed = state + dist + ser + tail
            fx = f1 + f2 + f3 + sfix
            # rare layouts (1-digit district, 3-letter series) must not win a tie on
            # fix-count alone: MHIZAB1254 is MH12AB.. (2 fixes), not MH1ZAB.. (1 fix)
            cost = fx + (0.5 if d == 1 else 0.0) + (1.0 if series == 3 else 0.0)
            if _STD_RE.match(fixed) and cost < best_cost:
                best, best_fix, best_cost = fixed, fx, cost
    # Bharat series: 2 digits, BH, 4 digits, 1-2 letters
    if n in (9, 10) and s[2:4] in ("BH", "8H", "B4", "84"):
        kinds = "DD" + "AA" + "DDDD" + "A" * (n - 8)
        fixed, fx = _coerce(s, kinds)
        if fixed and fixed[2:4] == "BH" and _BH_RE.match(fixed) and fx < best_cost:
            best, best_fix, best_cost = fixed, fx, float(fx)
    if best and best_fix <= MAX_FIXES:
        return best, best_fix
    return "", 0


def _bundled_model_dir():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(base, "easyocr_models")
    return d if os.path.isdir(d) else None


def _group_lines(results):
    """EasyOCR boxes -> text lines (reading order) -> candidate strings:
    each box, contiguous runs of boxes within a line ('MH 12 AB 1234' split by
    spaces), and x-overlapping boxes of two adjacent lines (two-row plates:
    'MH12' over 'AB1234'). Stickers beside the plate don't pollute candidates."""
    items = []
    for box, text, conf in results:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        items.append({"x1": min(xs), "x2": max(xs), "cy": (min(ys) + max(ys)) / 2.0,
                      "h": max(1.0, max(ys) - min(ys)), "text": str(text), "conf": float(conf)})
    items.sort(key=lambda it: it["cy"])
    lines = []
    for it in items:
        if lines and abs(it["cy"] - lines[-1]["cy"]) < 0.6 * max(it["h"], lines[-1]["h"]):
            ln = lines[-1]
            ln["items"].append(it)
            ln["cy"] = sum(i["cy"] for i in ln["items"]) / len(ln["items"])
            ln["h"] = max(ln["h"], it["h"])
        else:
            lines.append({"items": [it], "cy": it["cy"], "h": it["h"]})

    def joined(seq):
        return "".join(i["text"] for i in seq), sum(i["conf"] for i in seq) / len(seq)

    cands = [(it["text"], it["conf"]) for it in items]
    for ln in lines:
        ln["items"].sort(key=lambda i: i["x1"])
        its = ln["items"]
        for a in range(len(its)):
            for b in range(a + 2, min(len(its), a + 4) + 1):  # runs of 2..4 boxes
                cands.append(joined(its[a:b]))

    def overlap(p, q):
        return min(p["x2"], q["x2"]) - max(p["x1"], q["x1"]) > 0.3 * min(p["x2"] - p["x1"], q["x2"] - q["x1"])

    for a, b in zip(lines, lines[1:]):
        if b["cy"] - a["cy"] > 2.5 * max(a["h"], b["h"]):
            continue
        for ia in a["items"]:
            for ib in b["items"]:
                if overlap(ia, ib):
                    cands.append(joined([ia, ib]))
        top = [i for i in a["items"] if any(overlap(i, j) for j in b["items"])]
        bot = [i for i in b["items"] if any(overlap(i, j) for j in a["items"])]
        if top and bot and (len(top) > 1 or len(bot) > 1):
            cands.append(joined(top + bot))
    return cands


def find_plate_regions(crop, max_regions=4):
    """Cheap plate localizer (no ML): yellow or white rectangles with dense vertical
    edges (text strokes) in the lower 65% of the vehicle crop. Lets OCR work on a
    4x-upscaled plate instead of a downscaled whole vehicle — at 1080p a gate
    plate is only ~130 px wide. Returns [(x1, y1, x2, y2)] in crop coords, best first."""
    h, w = crop.shape[:2]
    if h < 40 or w < 40:
        return []
    y0 = int(h * 0.35)
    zone = crop[y0:]
    hsv = cv2.cvtColor(zone, cv2.COLOR_BGR2HSV)
    masks = (
        cv2.inRange(hsv, (12, 70, 90), (40, 255, 255)),     # yellow (commercial plates)
        cv2.inRange(hsv, (0, 0, 140), (180, 70, 255)),      # white (private plates)
    )
    close_k = cv2.getStructuringElement(cv2.MORPH_RECT, (max(5, w // 70), 3))
    found = []
    for mask in masks:
        m = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_k)
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            x, y, bw, bh = cv2.boundingRect(c)
            ar = bw / max(1, bh)
            if bw < 0.05 * w or bw > 0.7 * w or bh < 8 or bh > 0.3 * h or not (0.8 <= ar <= 7.5):
                continue  # two-row truck plates are nearly square (ar ~1)
            fill = cv2.contourArea(c) / float(bw * bh)
            if fill < 0.35:
                continue  # tilted plates fill their bounding box only partly
            sub = cv2.cvtColor(zone[y:y + bh, x:x + bw], cv2.COLOR_BGR2GRAY)
            gx = cv2.Sobel(sub, cv2.CV_16S, 1, 0, ksize=3)
            density = float((np.abs(gx) > 80).mean())
            if density < 0.06:
                continue  # plain panel / sticker without text strokes
            yc = (y0 + y + bh / 2.0) / h                  # plates sit low on the vehicle
            shape = 1.0 if 1.5 <= ar <= 5.5 else 0.7      # single-row plates are ~2-4:1; two-row ~1:1
            found.append((density * fill * min(1.0, bw / (0.12 * w)) * (0.4 + yc) * shape,
                          (x, y0 + y, x + bw, y0 + y + bh)))
    found.sort(key=lambda t: -t[0])
    out = []
    for _s, box in found:
        if any(_iou(box, b) > 0.3 for b in out):
            continue
        out.append(box)
        if len(out) >= max_regions:
            break
    return out


def _iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter == 0:
        return 0.0
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / float(ua)


def _score(conf, fixes):
    return conf - 0.15 * fixes + (0.25 if fixes == 0 else 0.0)


def _box_area(box):
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


class PlateReader:
    """Lazy-loaded EasyOCR; strict Indian-plate validation; multi-crop voting."""

    def __init__(self):
        self._reader = None
        self._lock = threading.Lock()
        self.last_error = ""
        self.last_trace = []  # raw OCR reads of the last candidates() call (diagnostics)
        self.last_plate_px = 0

    def _ensure(self):
        with self._lock:
            if self._reader is None:
                import easyocr

                kw = {"gpu": False, "verbose": False, "quantize": False}
                # quantize=False is REQUIRED: EasyOCR's default int8 dynamic quantization of
                # the recognizer LSTM returns garbage ('LELELD', conf 0.00) on CPU depending on
                # the torch thread count / input width (torch 2.5 quantized-LSTM bug). fp32 is
                # exact, stable, and just as fast here.
                mdir = _bundled_model_dir()
                if mdir:
                    kw.update(model_storage_directory=mdir, download_enabled=False)
                self._reader = easyocr.Reader(["en"], **kw)
        return self._reader

    def warmup(self):
        """Load OCR models up-front so the first crossing isn't slow/blocked."""
        try:
            self._ensure()
            self.last_error = ""
            return True
        except Exception as e:
            self.last_error = str(e)
            return False

    @staticmethod
    def _prep(img, max_w, min_w=320):
        """Gray + CLAHE view, downscaled to max_w / upscaled (<=4x) to min_w.
        Upscaled views are lightly smoothed (interpolation/JPEG blocks read as
        garbage). Returns (gray, scale) so box coords can be mapped back."""
        h, w = img.shape[:2]
        scale = 1.0
        if w > max_w:
            scale = max_w / w
        elif w < min_w:
            scale = min(4.0, min_w / w)
        if scale != 1.0:
            img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))),
                             interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        return cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gray), scale

    @staticmethod
    def _read(reader, img):
        try:
            return reader.readtext(img, allowlist=ALLOW, detail=1, paragraph=False, mag_ratio=1.0)
        except Exception:
            return []

    @staticmethod
    def _pad(img, frac=0.25):
        """The recognizer needs whitespace around the glyphs: a tight crop reads as garbage."""
        h = img.shape[0]
        p = max(8, int(frac * h))
        return cv2.copyMakeBorder(img, p, p, p, p, cv2.BORDER_CONSTANT, value=int(np.median(img)))

    def candidates(self, crop, deadline=None):
        """[(plate, conf, fixes)] found in one vehicle crop, best first.
        1) plate zone (lower 60%) at <=640px, 2) zoomed re-read of each detected
        text box at full resolution (a plate frame close to the text garbles the
        recognizer at low res), 3) whole crop only if nothing valid was found."""
        if crop is None or crop.size == 0:
            return []
        try:
            reader = self._ensure()
        except Exception as e:
            self.last_error = str(e)
            return []
        deadline = deadline or (time.time() + 30)
        found = {}
        trace = self.last_trace = []
        self.last_plate_px = 0  # widest localized plate (camera pixels): tells if the camera is too far

        def add(text, conf):
            plate, fixes = repair_plate(text)
            if plate and conf >= MIN_CONF:
                cur = found.get(plate)
                if cur is None or (conf, -fixes) > (cur[0], -cur[1]):
                    found[plate] = (conf, fixes)

        zone = crop[int(crop.shape[0] * 0.4):, :]
        # stage 0: localized plate rectangles, read at 4x — the decisive step for small gate plates
        for (x1, y1, x2, y2) in find_plate_regions(crop):
            if time.time() > deadline:
                break
            self.last_plate_px = max(self.last_plate_px, x2 - x1)
            m = max(4, int(0.15 * (y2 - y1)))
            sub = crop[max(0, y1 - m):min(crop.shape[0], y2 + m), max(0, x1 - m):min(crop.shape[1], x2 + m)]
            if sub.size == 0:
                continue
            sub_img, _ = self._prep(sub, PLATE_W, min_w=PLATE_W)
            res = self._read(reader, self._pad(sub_img))
            trace.append(f"plate@{x1},{y1} {x2 - x1}x{y2 - y1}: " + ", ".join(f"{t}({float(c):.2f})" for _, t, c in res))
            for text, conf in _group_lines(res):
                add(text, conf)
        if any(f == 0 and c >= 0.6 for c, f in found.values()) or time.time() > deadline - HEAVY_PASS_S:
            return sorted(((p, c, f) for p, (c, f) in found.items()), key=lambda t: -_score(t[1], t[2]))
        img, s = self._prep(zone, MAX_W)
        results = self._read(reader, img)
        trace.append("zone: " + ", ".join(f"{t}({float(c):.2f})" for _, t, c in results))
        items = list(results)
        for text, conf in _group_lines(items):
            add(text, conf)
        replaced = False
        order = sorted(range(len(results)), key=lambda i: -_box_area(results[i][0]))[:2]
        for i in order:
            if time.time() > deadline:
                break
            box, text, conf = results[i]
            plate, fixes = repair_plate(text)
            if (plate and fixes == 0 and conf >= 0.6) or conf >= 0.85:
                continue  # already a clean/confident read: re-reading cannot improve it
            t = _CLEAN_RE.sub("", str(text).upper())
            if conf >= 0.5 and len(t) >= 4 and t.isalpha():
                continue  # a confidently read WORD (TATA, LEYLAND, GOODS): not a plate
            xs = [p[0] / s for p in box]
            ys = [p[1] / s for p in box]
            bw, bh = max(xs) - min(xs), max(ys) - min(ys)
            if bw < 1.5 * bh or bw < 24 or bh < 8:
                continue
            m = max(4, int(0.12 * bh))
            x1, y1 = max(0, int(min(xs)) - m), max(0, int(min(ys)) - m)
            x2, y2 = min(zone.shape[1], int(max(xs)) + m), min(zone.shape[0], int(max(ys)) + m)
            sub = zone[y1:y2, x1:x2]
            if sub.size == 0:
                continue
            sub_img, _ = self._prep(sub, REFINE_W, min_w=REFINE_W)
            sub_res = self._read(reader, self._pad(sub_img))
            trace.append(f"zoom '{text}': " + ", ".join(f"{t}({float(c):.2f})" for _, t, c in sub_res))
            for t, c in _group_lines(sub_res):
                add(t, c)
            if len(sub_res) == 1 and float(sub_res[0][2]) > float(conf):
                items[i] = (box, str(sub_res[0][1]), float(sub_res[0][2]))  # better text for two-row merging
                replaced = True
        if replaced:
            for text, conf in _group_lines(items):
                add(text, conf)
        clean = any(f == 0 and c >= 0.6 for c, f in found.values())
        if not clean and time.time() < deadline - HEAVY_PASS_S:
            # no clean read in the plate zone: the plate may sit higher (cabin plate,
            # cut-off bbox) or the zone cut through it
            full, _ = self._prep(crop, MAX_W)
            res = self._read(reader, full)
            trace.append("full: " + ", ".join(f"{t}({float(c):.2f})" for _, t, c in res))
            for text, conf in _group_lines(res):
                add(text, conf)
        return sorted(((p, c, f) for p, (c, f) in found.items()), key=lambda t: -_score(t[1], t[2]))

    def read_many(self, crops, budget_s=8.0):
        """Vote across several crops of the same vehicle within a HARD time budget
        (checked before every OCR call). Returns (plate, detail); plate is ''
        ("Not detected") unless the read is confident: seen in 2 crops, or a
        clean read >= ACCEPT_CLEAN, or a 1-2 fix read >= ACCEPT_FIXED."""
        deadline = time.time() + budget_s
        votes = {}
        for crop in crops:
            if time.time() > deadline:
                break
            for plate, conf, fixes in self.candidates(crop, deadline):
                v = votes.setdefault(plate, {"n": 0, "conf": 0.0, "fixes": fixes})
                v["n"] += 1
                v["conf"] = max(v["conf"], conf)
                v["fixes"] = min(v["fixes"], fixes)
            if any(v["n"] >= 2 for v in votes.values()):
                break  # two crops agree: confident enough
        if not votes:
            return "", "no valid plate text"
        ranked = sorted(votes.items(), key=lambda kv: (-kv[1]["n"], -_score(kv[1]["conf"], kv[1]["fixes"])))
        plate, v = ranked[0]
        detail = f"votes={v['n']} conf={v['conf']:.2f} fixes={v['fixes']}"
        if len(ranked) > 1:
            p2, v2 = ranked[1]
            if v2["n"] == v["n"] and abs(_score(v2["conf"], v2["fixes"]) - _score(v["conf"], v["fixes"])) < 0.05:
                return "", f"ambiguous: {plate} vs {p2} ({detail})"
        if ((v["n"] >= 2 and v["conf"] >= ACCEPT_VOTED) or (v["fixes"] == 0 and v["conf"] >= ACCEPT_CLEAN)
                or (v["fixes"] <= 2 and v["conf"] >= ACCEPT_FIXED)):
            return plate, detail
        return "", f"low confidence: {plate} ({detail})"

    def read(self, crop):
        """crop: BGR numpy image of the vehicle. Returns best plate string or ''."""
        return self.read_many([crop])[0]
