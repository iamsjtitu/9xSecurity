"""9x Security - Number plate reader (EasyOCR, offline). Optional feature."""
import re

_PLATE_RE = re.compile(r"[^A-Z0-9]")


class PlateReader:
    """Lazy-loaded so the app stays fast when the feature is disabled."""

    def __init__(self):
        self._reader = None

    def _ensure(self):
        if self._reader is None:
            import easyocr

            self._reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        return self._reader

    def read(self, crop):
        """crop: BGR numpy image of the vehicle. Returns best plate string or ''."""
        try:
            reader = self._ensure()
            results = reader.readtext(crop)
        except Exception:
            return ""
        best, best_score = "", 0.0
        for _box, text, conf in results:
            clean = _PLATE_RE.sub("", text.upper())
            # Indian plates are typically 8-10 chars, allow a bit of slack.
            if 5 <= len(clean) <= 12 and conf >= best_score:
                best, best_score = clean, conf
        return best
