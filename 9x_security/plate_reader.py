"""9x Security - Number plate reader (EasyOCR, offline). Optional feature."""
import os
import re
import sys
import threading

import cv2

_CLEAN_RE = re.compile(r"[^A-Z0-9]")
# Indian plate formats: MH12AB1234 / MH12A1234 / 22BH1234AB (Bharat series)
_PLATE_PATTERNS = (
    re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{4}$"),
    re.compile(r"^\d{2}BH\d{4}[A-Z]{1,2}$"),
)
ALLOW = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def _bundled_model_dir():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(base, "easyocr_models")
    return d if os.path.isdir(d) else None


class PlateReader:
    """Lazy-loaded; call warmup() from a background thread at engine start."""

    def __init__(self):
        self._reader = None
        self._lock = threading.Lock()
        self.last_error = ""

    def _ensure(self):
        with self._lock:
            if self._reader is None:
                import easyocr

                kw = {"gpu": False, "verbose": False}
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
    def _variants(crop):
        """Enhanced views of the vehicle crop: full + lower half (plate zone),
        upscaled + CLAHE so small/dark plates become readable."""
        h = crop.shape[0]
        outs = []
        for c in (crop, crop[h // 2:, :]):
            if c.size == 0:
                continue
            ch, cw = c.shape[:2]
            scale = max(1.0, 320.0 / max(1, ch))
            if scale > 1.0:
                c = cv2.resize(c, (int(cw * scale), int(ch * scale)), interpolation=cv2.INTER_CUBIC)
            gray = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY)
            gray = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gray)
            outs.append(gray)
        return outs

    def read(self, crop):
        """crop: BGR numpy image of the vehicle. Returns best plate string or ''."""
        if crop is None or crop.size == 0:
            return ""
        try:
            reader = self._ensure()
        except Exception as e:
            self.last_error = str(e)
            return ""
        best, best_score = "", 0.0
        for img in self._variants(crop):
            try:
                results = reader.readtext(img, allowlist=ALLOW, detail=1)
            except Exception:
                continue
            for _box, text, conf in results:
                clean = _CLEAN_RE.sub("", str(text).upper())
                if not 5 <= len(clean) <= 12:
                    continue
                score = float(conf)
                if any(p.match(clean) for p in _PLATE_PATTERNS):
                    score += 1.0  # strongly prefer valid Indian plate formats
                if score > best_score:
                    best, best_score = clean, score
        return best
