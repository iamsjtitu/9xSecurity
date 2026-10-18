"""9x Security — Plate Recognizer Snapshot Cloud (paid, plate-special AI) as the number-plate reader.
The user's own API token lives in config (Settings > Number Plate). Fails closed: no plate unless the
provider answered 2xx with a plate that is a valid Indian format at good confidence."""
import json
import threading
import time

import cv2
import requests

from plate_reader import repair_plate

URL = "https://api.platerecognizer.com/v1/plate-reader/"
STATS_URL = "https://api.platerecognizer.com/v1/statistics/"
MIN_SCORE = 0.60        # provider text confidence for a clean (0-fix) plate
MIN_SCORE_FIXED = 0.85  # ... for a plate that needed one I->1 / O->0 style fix
MIN_DSCORE = 0.40       # plate-detection confidence
MIN_GAP_S = 1.2         # free plans allow 1 lookup/second (counted from the provider's answer)
MAX_BYTES = 3 * 1024 * 1024
MAX_LOOKUPS = 2         # per vehicle: best crop, then one more only if the first found nothing

_EXPLAIN = {
    401: "API key galat hai — app.platerecognizer.com par Snapshot > API Token se dobara copy karein",
    403: "Access nahi / is mahine ke lookups khatam (403) — platerecognizer.com par plan dekhein",
    429: "Bahut tez requests (429) — free plan 1 lookup/second deta hai",
    413: "Photo badi hai (3 MB se zyada)",
}


def explain(status, text=""):
    hint = _EXPLAIN.get(status, "Provider error — thodi der baad try karein" if status >= 500 else "")
    if "invalid token" in str(text or "").lower():
        hint = _EXPLAIN[401]  # the provider answers 403 'Invalid token.' for a wrong key
    snippet = " ".join(str(text or "")[:120].split())
    return f"HTTP {status}: {hint}" + (f" [{snippet}]" if snippet else "")


def _jpeg(crop):
    """JPEG bytes <= 3 MB; plates are never shrunk below the provider's useful size."""
    if crop is None or not getattr(crop, "size", 0):
        return None
    img = crop
    if img.shape[1] > 1600:
        s = 1600 / img.shape[1]
        img = cv2.resize(img, (1600, max(1, int(img.shape[0] * s))))
    for q in (92, 85, 75):
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), q])
        if ok and len(buf) <= MAX_BYTES:
            return buf.tobytes()
    return None


def pick_plate(payload):
    """(plate, detail) from a provider response; '' unless a result is a valid Indian plate at
    good confidence. candidates are alternatives: the top one is used only if the main read fails."""
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return "", "bad response"
    if not results:
        return "", "no plate in photo"
    best = None
    for r in sorted((x for x in results if isinstance(x, dict)), key=lambda x: -float(x.get("score") or 0)):
        dscore = float(r.get("dscore") or 0)
        options = [(r.get("plate"), float(r.get("score") or 0))]
        options += [(c.get("plate"), float(c.get("score") or 0)) for c in (r.get("candidates") or []) if isinstance(c, dict)]
        for raw, score in options:
            plate, fixes = repair_plate(raw)
            if not plate:
                continue
            ok = dscore >= MIN_DSCORE and ((fixes == 0 and score >= MIN_SCORE) or (fixes == 1 and score >= MIN_SCORE_FIXED))
            if ok:
                return plate, f"cloud {plate} score={score:.2f} dscore={dscore:.2f} fixes={fixes}"
            if best is None:
                best = (plate, score, dscore, fixes)
    if best:
        return "", f"low confidence: {best[0]} score={best[1]:.2f} dscore={best[2]:.2f} fixes={best[3]}"
    return "", "no valid Indian plate: " + ", ".join(str(r.get("plate", "")) for r in results[:3] if isinstance(r, dict))


class CloudPlateReader:
    def __init__(self, api_key="", region="in"):
        self.api_key = ""
        self.region = region
        self.last_error = ""
        self.last_status = 0
        self._last_call = 0.0
        self._lock = threading.Lock()
        self.update(api_key, region)

    def update(self, api_key, region="in"):
        self.api_key = str(api_key or "").strip()
        self.region = (region or "in").strip().lower() or "in"

    @property
    def enabled(self):
        return bool(self.api_key)

    def _headers(self):
        return {"Authorization": f"Token {self.api_key}", "Accept": "application/json"}

    def _throttle(self):
        wait = self._last_call + MIN_GAP_S - time.time()
        if wait > 0:
            time.sleep(wait)

    def _post(self, raw):
        with self._lock:
            self._throttle()
            try:
                return requests.post(
                    URL, headers=self._headers(),
                    files={"upload": ("vehicle.jpg", raw, "image/jpeg")},
                    data={"regions": self.region, "config": json.dumps({"region": "strict"})},
                    timeout=(5, 25),
                )
            finally:
                self._last_call = time.time()  # the provider counts from its answer, not our send

    def lookup(self, crop):
        """One provider call (a throttled 429 is retried once). Returns (plate, detail, fatal):
        fatal=True means stop trying more crops (bad key / credits over / offline)."""
        raw = _jpeg(crop)
        if raw is None:
            return "", "empty crop", False
        for attempt in (1, 2):
            try:
                r = self._post(raw)
            except requests.RequestException as e:
                self.last_error = f"Internet/connection error: {e}"
                return "", self.last_error, True
            self.last_status = r.status_code
            if r.status_code == 429 and attempt == 1:
                time.sleep(1.5)
                continue
            break
        if r.status_code == 429:
            return "", explain(429, r.text), False  # still throttled after a retry: try the next crop
        if r.status_code in (401, 403):
            self.last_error = explain(r.status_code, r.text)
            return "", self.last_error, True
        if not 200 <= r.status_code < 300:
            self.last_error = explain(r.status_code, r.text)
            return "", self.last_error, r.status_code >= 500
        try:
            payload = r.json()
        except ValueError:
            return "", "bad JSON from provider", False
        self.last_error = ""
        plate, detail = pick_plate(payload)
        return plate, detail, False

    def read_many(self, crops, max_lookups=MAX_LOOKUPS):
        """Largest crop first; a second lookup only when the first had no usable plate.
        Returns (plate, detail)."""
        if not self.enabled:
            return "", "cloud off"
        crops = [c for c in crops if c is not None and getattr(c, "size", 0)]
        crops.sort(key=lambda c: -(c.shape[0] * c.shape[1]))
        notes = []
        for crop in crops[:max_lookups]:
            plate, detail, fatal = self.lookup(crop)
            notes.append(detail)
            if plate:
                return plate, " | ".join(notes)
            if fatal:
                break
        return "", " | ".join(notes) or "no crops"

    def statistics(self, api_key=None):
        """Token check + monthly usage for Settings: {valid, used, limit, remaining, resets_on, message}."""
        key = str(api_key or self.api_key or "").strip()
        if not key:
            return {"valid": False, "message": "API key khaali hai"}
        try:
            r = requests.get(STATS_URL, headers={"Authorization": f"Token {key}", "Accept": "application/json"}, timeout=(5, 15))
        except requests.RequestException as e:
            return {"valid": False, "message": f"Internet/connection error: {e}"}
        if r.status_code != 200:
            return {"valid": False, "message": explain(r.status_code, r.text), "status": r.status_code}
        try:
            p = r.json()
            used = int(p["usage"]["calls"])
            limit = int(p["total_calls"])
        except (ValueError, KeyError, TypeError):
            return {"valid": False, "message": "Provider ka jawab samajh nahi aaya"}
        remaining = max(0, limit - used)
        return {"valid": True, "used": used, "limit": limit, "remaining": remaining,
                "resets_on": (p.get("usage") or {}).get("resets_on", ""),
                "message": f"Key sahi hai ✔ — is mahine {used}/{limit} lookups use hue, {remaining} baaki"}
