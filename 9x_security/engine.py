"""9x Security - Core processing engine (no GUI, unit-testable).

Ties together detection, tracking, line-crossing, snapshot saving and DB logging.
The GUI (main.py) feeds frames into `process_frame` and renders `annotated`.
"""
import os
import re
from datetime import datetime
from urllib.parse import quote, unquote

import cv2

import config
from database import EventDB
from detector import VehicleDetector
from tracker import CentroidTracker


def normalize_rtsp_url(url):
    """Fix RTSP URLs whose password contains special chars like '@'.
    e.g. rtsp://admin:Admin@123@host:554/s -> rtsp://admin:Admin%40123@host:554/s
    Idempotent (already-encoded URLs stay unchanged)."""
    url = (url or "").strip()
    m = re.match(r"^(rtsps?://)(.*)$", url, re.IGNORECASE)
    if not m or "@" not in m.group(2):
        return url
    scheme, rest = m.groups()
    creds, host = rest.rsplit("@", 1)
    if ":" in creds:
        user, pw = creds.split(":", 1)
        user = quote(unquote(user), safe="")
        pw = quote(unquote(pw), safe="")
        return f"{scheme}{user}:{pw}@{host}"
    return f"{scheme}{quote(unquote(creds), safe='')}@{host}"


class SecurityEngine:
    def __init__(self, cfg=None, db=None, detector=None, plate_reader=None):
        self.cfg = cfg or config.load_config()
        self.db = db or EventDB()
        self.detector = detector or VehicleDetector(
            conf=self.cfg.get("confidence", 0.4),
            allowed=self.cfg.get("vehicle_classes"),
        )
        self.tracker = CentroidTracker()
        self.plate_reader = plate_reader
        if self.cfg.get("enable_plate") and self.plate_reader is None:
            from plate_reader import PlateReader

            self.plate_reader = PlateReader()

        self.frame_idx = 0
        self.last_dets = []
        self.on_event = None  # optional callback(event_dict) for the GUI

        from whatsapp import WhatsAppNotifier

        self.notifier = WhatsAppNotifier(self.cfg)

    # ---- line helpers (normalized <-> pixel) ------------------------------
    def line_points(self, w, h):
        ln = self.cfg["line"]
        a = (int(ln["x1"] * w), int(ln["y1"] * h))
        b = (int(ln["x2"] * w), int(ln["y2"] * h))
        return a, b

    def _direction_for(self, to_side):
        """Map crossing target side sign to Entry/Exit per configuration."""
        entry_sign = 1 if self.cfg.get("entry_direction", "pos") == "pos" else -1
        return "Entry" if to_side == entry_sign else "Exit"

    # ---- main per-frame pipeline ------------------------------------------
    def process_frame(self, frame, original=None):
        """
        frame: BGR image already resized to processing/display resolution.
        original: optional full-res frame for higher quality plate/snapshot.
        Returns (annotated_frame, events_this_frame)
        """
        h, w = frame.shape[:2]
        a, b = self.line_points(w, h)
        skip = max(1, int(self.cfg.get("detect_frame_skip", 2)))

        if self.frame_idx % skip == 0:
            self.last_dets = self.detector.detect(frame)
        self.frame_idx += 1

        crossings = self.tracker.update(self.last_dets, (a, b))

        events = []
        for cr in crossings:
            direction = self._direction_for(cr["to_side"])
            plate = ""
            if self.cfg.get("enable_plate") and self.plate_reader is not None:
                plate = self._read_plate(frame, original, cr["bbox"], w, h)
            image_path = self._save_snapshot(frame, cr, direction)
            eid = self.db.add_event(cr["label"], direction, plate, image_path)
            ev = {
                "id": eid,
                "vehicle_type": cr["label"],
                "direction": direction,
                "plate": plate,
                "image_path": image_path,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
            events.append(ev)
            if self.on_event:
                try:
                    self.on_event(ev)
                except Exception:
                    pass
            try:
                self.notifier.notify(ev)
            except Exception:
                pass

        annotated = self._annotate(frame, a, b)
        return annotated, events

    def _read_plate(self, frame, original, bbox, w, h):
        x1, y1, x2, y2 = bbox
        src, sx, sy = frame, 1.0, 1.0
        if original is not None:
            oh, ow = original.shape[:2]
            sx, sy, src = ow / w, oh / h, original
        cx1, cy1 = max(0, int(x1 * sx)), max(0, int(y1 * sy))
        cx2, cy2 = int(x2 * sx), int(y2 * sy)
        crop = src[cy1:cy2, cx1:cx2]
        if crop.size == 0:
            return ""
        return self.plate_reader.read(crop)

    def _save_snapshot(self, frame, cr, direction):
        ts = datetime.now()
        day_dir = os.path.join(config.SNAPSHOT_DIR, ts.strftime("%Y-%m-%d"))
        os.makedirs(day_dir, exist_ok=True)
        fname = f"{direction}_{cr['label']}_{ts.strftime('%H-%M-%S-%f')[:-3]}.jpg"
        path = os.path.join(day_dir, fname)

        img = frame.copy()
        x1, y1, x2, y2 = cr["bbox"]
        color = (0, 200, 0) if direction == "Entry" else (0, 140, 255)
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        label = f"{direction} - {cr['label'].upper()}  {ts.strftime('%Y-%m-%d %H:%M:%S')}"
        cv2.rectangle(img, (0, 0), (img.shape[1], 28), (0, 0, 0), -1)
        cv2.putText(img, label, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        cv2.imwrite(path, img)
        return path

    def _annotate(self, frame, a, b):
        img = frame
        cv2.line(img, a, b, (0, 255, 255), 2)
        for tr in self.tracker.tracks.values():
            x1, y1, x2, y2 = tr.bbox
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 200, 0), 2)
            cv2.putText(
                img, tr.label, (x1, max(12, y1 - 6)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 0), 1,
            )
        return img
