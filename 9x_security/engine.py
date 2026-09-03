"""9x Security - Core processing engine (no GUI, unit-testable).

Ties together detection, tracking, line-crossing, snapshot saving and DB logging.
The GUI (main.py) feeds frames into `process_frame` and renders `annotated`.
"""
import os
import re
import time
from datetime import datetime
from urllib.parse import quote, unquote

import cv2
import numpy as np

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


CAMERA_LOG = os.path.join(config.BASE_DIR, "camera_log.txt")
# analyzeduration/probesize: slow cameras get extra time to deliver stream info.
FFMPEG_OPTS = "|timeout;5000000|analyzeduration;10000000|probesize;5000000|max_delay;500000"


def redact_url(url):
    """Hide user:pass in URLs before logging."""
    return re.sub(r"//[^/@]*@", "//****@", str(url))


def clog(msg):
    try:
        with open(CAMERA_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} | {msg}\n")
    except Exception:
        pass


def _try_capture(url, transport, wait=10.0):
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"rtsp_transport;{transport}{FFMPEG_OPTS}"
    clog(f"capture attempt transport={transport} wait={wait}s")
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    try:
        if not cap.isOpened():
            clog(f"capture {transport}: open FAILED")
            return False, "Stream open nahi hua (username/password ya stream path galat ho sakta hai)"
        t0 = time.time()
        while time.time() - t0 < wait:
            ok, frame = cap.read()
            if ok and frame is not None:
                h, w = frame.shape[:2]
                clog(f"capture {transport}: OK first frame {w}x{h} in {time.time()-t0:.1f}s")
                return True, f"Video aa raha hai ({w}x{h})"
        clog(f"capture {transport}: opened but NO frames in {wait}s")
        return False, f"Stream khula par {int(wait)} sec me video frames nahi aaye"
    finally:
        cap.release()


class FFmpegPipeSource:
    """VLC-grade fallback: decodes RTSP via bundled ffmpeg into BGR frames.
    Used when OpenCV's own capture cannot play a stream that works in VLC."""

    def __init__(self, url, input_args=None):
        import subprocess

        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        self._nbytes = config.DISPLAY_WIDTH * config.DISPLAY_HEIGHT * 3
        if input_args is None:
            input_args = [
                "-rtsp_transport", "tcp", "-timeout", "5000000",
                "-analyzeduration", "10000000", "-probesize", "5000000",
            ]
        cmd = [
            exe, "-nostdin", "-loglevel", "error", *input_args, "-i", url,
            "-an", "-vf", f"scale={config.DISPLAY_WIDTH}:{config.DISPLAY_HEIGHT}",
            "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1",
        ]
        self._err = open(os.path.join(config.BASE_DIR, "ffmpeg_err.txt"), "ab")
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        clog("ffmpeg-pipe: starting")
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=self._err,
            stdin=subprocess.DEVNULL, creationflags=flags,
        )

    def isOpened(self):
        return self.proc.poll() is None

    def read(self):
        buf = self.proc.stdout.read(self._nbytes) if self.proc.stdout else b""
        if not buf or len(buf) < self._nbytes:
            return False, None
        frame = np.frombuffer(buf, dtype=np.uint8).reshape(
            config.DISPLAY_HEIGHT, config.DISPLAY_WIDTH, 3
        )
        return True, frame.copy()

    def release(self):
        try:
            self.proc.kill()
        except Exception:
            pass
        try:
            self._err.close()
        except Exception:
            pass


def _try_ffmpeg_pipe(url):
    try:
        src = FFmpegPipeSource(url)
    except Exception as e:
        clog(f"ffmpeg-pipe: unavailable ({e})")
        return False, f"FFmpeg engine start nahi hua ({e})"
    try:
        t0 = time.time()
        ok, _frame = src.read()  # blocks until first frame or ffmpeg exits
        if ok:
            clog(f"ffmpeg-pipe: OK first frame in {time.time()-t0:.1f}s")
            return True, "Video aa raha hai (FFmpeg engine) — app isi engine se video chalayega"
        clog("ffmpeg-pipe: no frames (process exited)")
        return False, "FFmpeg engine se bhi video nahi mila (ffmpeg_err.txt file check karein)"
    finally:
        src.release()


def open_stream(source, is_running=lambda: True):
    """Open a video source with the TCP -> UDP -> FFmpeg-pipe -> default ladder.
    Returns an opened capture-like object, or None if aborted/unavailable."""
    if not (isinstance(source, str) and source.lower().startswith("rtsp")):
        return cv2.VideoCapture(source)
    for transport in ("tcp", "udp"):
        if not is_running():
            return None
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"rtsp_transport;{transport}{FFMPEG_OPTS}"
        clog(f"open_stream: transport={transport}")
        cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
        if cap.isOpened():
            t0 = time.time()
            while is_running() and time.time() - t0 < 12:
                ok, _f = cap.read()
                if ok:
                    clog(f"open_stream {transport}: OK first frame in {time.time()-t0:.1f}s")
                    return cap
            clog(f"open_stream {transport}: opened but no frames in 12s")
        cap.release()
    if not is_running():
        return None
    try:
        clog("open_stream: trying FFmpeg engine fallback")
        src = FFmpegPipeSource(source)
        ok, _f = src.read()
        if ok and is_running():
            clog("open_stream ffmpeg-pipe: OK")
            return src
        src.release()
    except Exception as e:
        clog(f"open_stream ffmpeg-pipe failed: {e}")
    if not is_running():
        return None
    clog("open_stream: falling back to default backend")
    return cv2.VideoCapture(source)


def probe_rtsp(url, wait=10.0):
    """Step-by-step camera connection diagnosis for the Test button.
    Returns (ok, [(step_name, step_ok, detail), ...])."""
    import socket

    steps = []
    fixed = normalize_rtsp_url(url)
    clog(f"=== PROBE start: {redact_url(fixed)}")
    steps.append(("URL check", True,
                  f"Password ke special characters auto-fix kiye:\n    {fixed}" if fixed != url
                  else "URL format theek hai"))
    m = re.match(r"^rtsps?://(?:[^/]*@)?([^:/?#]+)(?::(\d+))?", fixed, re.IGNORECASE)
    if not m:
        steps[-1] = ("URL check", False, "URL 'rtsp://' se shuru hona chahiye")
        return False, steps
    host, port = m.group(1), int(m.group(2) or 554)
    try:
        socket.create_connection((host, port), timeout=3).close()
        clog(f"probe: {host}:{port} reachable")
        steps.append((f"Camera network ({host}:{port})", True, "Camera network par mil gaya"))
    except Exception as e:
        clog(f"probe: {host}:{port} UNREACHABLE ({e})")
        steps.append((f"Camera network ({host}:{port})", False,
                      "Camera tak pahunch nahi paa rahe. Check: IP sahi hai? Camera on hai? "
                      f"PC aur camera same network/WiFi par hain? ({e})"))
        return False, steps
    ok, detail = _try_capture(fixed, "tcp", wait)
    steps.append(("Video stream (TCP)", ok, detail))
    if ok:
        return True, steps
    ok, detail = _try_capture(fixed, "udp", wait)
    steps.append(("Video stream (UDP)", ok, detail))
    if ok:
        return True, steps
    ok, detail = _try_ffmpeg_pipe(fixed)
    steps.append(("Video stream (FFmpeg engine)", ok, detail))
    if ok:
        return True, steps
    steps.append(("Hint", False,
                  "Camera network par hai par video nahi mila — zyada tar username/password "
                  "ya stream path (stream1 vs stream2) galat hota hai. Yahi URL VLC me "
                  "(Media > Open Network Stream) test karein."))
    return False, steps


class SecurityEngine:
    def __init__(self, cfg=None, db=None, detector=None, plate_reader=None):
        self.cfg = cfg or config.load_config()
        self.db = db or EventDB()
        if detector is None:
            from detector import resolve_model_path

            path, self.model_tier = resolve_model_path(self.cfg.get("detector_model", "auto"))
            detector = VehicleDetector(
                model_path=path,
                conf=self.cfg.get("confidence", 0.4),
                allowed=self.cfg.get("vehicle_classes"),
            )
        else:
            self.model_tier = "custom"
        self.detector = detector
        self.tracker = CentroidTracker()
        self.plate_reader = plate_reader
        if self.cfg.get("enable_plate") and self.plate_reader is None:
            from plate_reader import PlateReader

            self.plate_reader = PlateReader()
        self._best_crops = {}
        # OCR models load lazily on the first crossing (async thread) — no heavy
        # torch work competes with YOLO right at engine start.

        self.frame_idx = 0
        self.last_dets = []
        self.last_detect_ms = None
        self.on_event = None  # optional callback(event_dict) for the GUI

        from whatsapp import WhatsAppNotifier

        self.notifier = WhatsAppNotifier(self.cfg, db=self.db)

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
    def use_fast_model(self):
        """Auto mode fallback: swap to yolov8n when the accurate model is too slow here."""
        from detector import resolve_model_path

        path, self.model_tier = resolve_model_path("fast")
        self.detector = VehicleDetector(
            model_path=path, conf=self.cfg.get("confidence", 0.4), allowed=self.cfg.get("vehicle_classes")
        )
        return self.detector.model_name

    def process_frame(self, frame, original=None):
        """
        frame: BGR image already resized to processing/display resolution.
        original: optional full-res frame for higher quality plate/snapshot.
        Returns (annotated_frame, events_this_frame)
        """
        h, w = frame.shape[:2]
        a, b = self.line_points(w, h)
        self.tracker.near_band = 0.10 * h
        skip = max(1, int(self.cfg.get("detect_frame_skip", 2)))

        if self.frame_idx % skip == 0:
            t0 = time.time()
            self.last_dets = self.detector.detect(frame)
            self.last_detect_ms = (time.time() - t0) * 1000
        self.frame_idx += 1

        crossings = self.tracker.update(self.last_dets, (a, b))
        if self.cfg.get("enable_plate") and self.plate_reader is not None:
            self._update_best_crops(frame, original, w, h)

        events = []
        for cr in crossings:
            direction = self._direction_for(cr["to_side"])
            image_path = self._save_snapshot(frame, cr, direction)
            eid = self.db.add_event(cr["label"], direction, "", image_path)
            ev = {
                "id": eid,
                "vehicle_type": cr["label"],
                "direction": direction,
                "plate": "",
                "image_path": image_path,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
            events.append(ev)
            if self.on_event:
                try:
                    self.on_event(ev)
                except Exception:
                    pass
            if self.cfg.get("enable_plate") and self.plate_reader is not None:
                # OCR is slow on CPU: run async so the frame loop never stalls.
                crops = []
                c = self._plate_crop(frame, original, cr["bbox"], w, h)
                if c is not None:
                    crops.append(c.copy())
                bc = self._best_crops.get(cr["track_id"])
                if bc is not None:
                    crops.append(bc[1])
                clog(f"plate OCR: track {cr['track_id']} {direction} queued")
                import threading

                threading.Thread(
                    target=self._ocr_and_notify,
                    args=(eid, cr["track_id"], ev, crops),
                    daemon=True,
                ).start()
            else:
                try:
                    self.notifier.notify(ev)
                except Exception:
                    pass

        annotated = self._annotate(frame, a, b)
        return annotated, events

    def _warm_plate_reader(self):
        ok = self.plate_reader.warmup()
        clog("plate OCR: models loaded, ready" if ok
             else f"plate OCR: model load FAILED ({self.plate_reader.last_error})")

    def _update_best_crops(self, frame, original, w, h):
        """Keep the biggest (closest) crop per active track so OCR gets the
        best possible view of the plate, not just the crossing frame."""
        live = set(self.tracker.tracks.keys())
        for tid in list(self._best_crops.keys()):
            if tid not in live:
                del self._best_crops[tid]
        src, sx, sy = frame, 1.0, 1.0
        if original is not None:
            oh, ow = original.shape[:2]
            sx, sy, src = ow / w, oh / h, original
        for tid, tr in self.tracker.tracks.items():
            if tr.counted:
                continue
            x1, y1, x2, y2 = tr.bbox
            area = max(0, x2 - x1) * max(0, y2 - y1)
            prev = self._best_crops.get(tid)
            if prev is not None and prev[0] >= area:
                continue
            cx1, cy1 = max(0, int(x1 * sx)), max(0, int(y1 * sy))
            cx2, cy2 = min(src.shape[1], int(x2 * sx)), min(src.shape[0], int(y2 * sy))
            crop = src[cy1:cy2, cx1:cx2]
            if crop.size:
                if crop.shape[1] > 1000:  # bound memory for 4K sources
                    s = 1000 / crop.shape[1]
                    crop = cv2.resize(crop, (1000, max(1, int(crop.shape[0] * s))))
                else:
                    crop = crop.copy()
                self._best_crops[tid] = (area, crop)

    def _ocr_and_notify(self, eid, tid, ev, crops):
        plate = ""
        for c in crops:
            try:
                plate = self.plate_reader.read(c)
            except Exception:
                plate = ""
            if plate:
                break
        if plate:
            try:
                self.db.update_event_plate(eid, plate)
            except Exception:
                pass
            ev = {**ev, "plate": plate}
        clog(f"plate OCR: track {tid} event {eid} -> '{plate or '(nahi mila)'}'")
        try:
            self.notifier.notify(ev)
        except Exception:
            pass

    def _plate_crop(self, frame, original, bbox, w, h):
        x1, y1, x2, y2 = bbox
        src, sx, sy = frame, 1.0, 1.0
        if original is not None:
            oh, ow = original.shape[:2]
            sx, sy, src = ow / w, oh / h, original
        cx1, cy1 = max(0, int(x1 * sx)), max(0, int(y1 * sy))
        cx2, cy2 = int(x2 * sx), int(y2 * sy)
        crop = src[cy1:cy2, cx1:cx2]
        return crop if crop.size else None

    def _save_snapshot(self, frame, cr, direction):
        ts = datetime.now()
        day_dir = os.path.join(config.SNAPSHOT_DIR, ts.strftime("%Y-%m-%d"))
        fname = f"{direction}_{cr['label']}_{ts.strftime('%H-%M-%S-%f')[:-3]}.jpg"
        path = os.path.join(day_dir, fname)

        img = frame.copy()
        x1, y1, x2, y2 = cr["bbox"]
        color = (0, 200, 0) if direction == "Entry" else (0, 140, 255)
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        label = f"{direction} - {cr['label'].upper()}  {ts.strftime('%d-%m-%Y %I:%M:%S %p')}"
        cv2.rectangle(img, (0, 0), (img.shape[1], 28), (0, 0, 0), -1)
        cv2.putText(img, label, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        try:
            os.makedirs(day_dir, exist_ok=True)
            # imencode + Python write: works with Unicode/Hindi paths where cv2.imwrite fails silently
            ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            if not ok:
                raise RuntimeError("jpeg encode failed")
            with open(path, "wb") as f:
                f.write(buf.tobytes())
            clog(f"event: {direction} {cr['label']} track {cr.get('track_id')} "
                 f"({cr.get('via', 'cross')}) -> {path}")
            return path
        except Exception as e:
            clog(f"event: {direction} {cr['label']} SNAPSHOT SAVE FAILED ({e}) path={path}")
            return ""

    def _annotate(self, frame, a, b):
        img = frame
        cv2.line(img, a, b, (0, 255, 255), 2)
        for tr in self.tracker.tracks.values():
            x1, y1, x2, y2 = tr.bbox
            col = (160, 160, 160) if tr.counted else (0, 200, 0)
            cv2.rectangle(img, (x1, y1), (x2, y2), col, 2)
            cv2.putText(
                img, tr.label + (" (counted)" if tr.counted else ""), (x1, max(12, y1 - 6)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1,
            )
            cv2.circle(img, self.tracker.ref_point(tr.bbox), 5, (0, 0, 255), -1)
        n = len(self.tracker.tracks)
        hud = f"AI: {n} vehicle{'s' if n != 1 else ''} tracked"
        if self.last_detect_ms is not None:
            hud += f"  |  {self.last_detect_ms:.0f} ms"
        hud += f"  |  {getattr(self.detector, 'model_name', '')}"
        cv2.putText(img, hud, (10, img.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (0, 0, 0), 3)
        cv2.putText(img, hud, (10, img.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (0, 255, 0) if n else (200, 200, 200), 1)
        return img
