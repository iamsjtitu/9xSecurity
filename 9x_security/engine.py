"""9x Security - Core processing engine (no GUI, unit-testable).

Ties together detection, tracking, line-crossing, snapshot saving and DB logging.
The GUI (main.py) feeds frames into `process_frame` and renders `annotated`.
"""
import os
import queue
import re
import threading
import time
from datetime import datetime
from urllib.parse import quote, unquote

import cv2
import numpy as np

import config
from database import EventDB
from detector import VehicleDetector
from tracker import CentroidTracker

OCR_MAX_QUEUE_WAIT_S = float(os.environ.get("OCR_MAX_QUEUE_WAIT_S", "20"))
MIN_PLATE_PX = 160  # below this plate width (camera px) OCR is guesswork: user's gate plates were 60-130px


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
# discardcorrupt: drop broken frames (packet loss) instead of showing green/smeared video.
FFMPEG_OPTS = "|timeout;5000000|analyzeduration;10000000|probesize;5000000|max_delay;500000|fflags;discardcorrupt"


class LatestFrameReader:
    """Decodes in a background thread and hands out only the NEWEST frame.
    Without this, a slow AI loop stalls the decoder → the camera/ffmpeg drops
    packets → HEVC 'Could not find ref with POC' errors, smeared frames and
    ever-growing lag. Used for live (RTSP) sources only, not for files."""

    def __init__(self, cap):
        import threading

        self.cap = cap
        self._lock = threading.Lock()
        self._frame = None
        self._seq = 0
        self._served = 0
        self._alive = True
        self.dropped = 0      # frames replaced before the AI consumed them
        self.decoded = 0
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self):
        while self._alive:
            try:
                ok, f = self.cap.read()
            except Exception:
                ok, f = False, None
            if not ok or f is None:
                time.sleep(0.01)
                continue
            with self._lock:
                if self._seq != self._served:
                    self.dropped += 1
                self._frame = f
                self._seq += 1
                self.decoded += 1

    def read(self, timeout=1.0):
        """Newest unseen frame; (False, None) after `timeout` s without one."""
        t0 = time.time()
        while self._alive:
            with self._lock:
                if self._seq != self._served:
                    self._served = self._seq
                    return True, self._frame
            if time.time() - t0 > timeout:
                return False, None
            time.sleep(0.004)
        return False, None

    def isOpened(self):
        return self._alive and self.cap.isOpened()

    def get(self, prop):
        try:
            return self.cap.get(prop)
        except Exception:
            return 0

    def release(self):
        self._alive = False
        try:
            self._t.join(timeout=2)
        except Exception:
            pass
        try:
            self.cap.release()
        except Exception:
            pass


def codec_name(cap):
    """'hevc' / 'h264' / '' from an OpenCV capture (FFmpegPipeSource → '')."""
    try:
        v = int(cap.get(cv2.CAP_PROP_FOURCC))
        if v <= 0:
            return ""
        return "".join(chr((v >> (8 * i)) & 0xFF) for i in range(4)).strip().lower()
    except Exception:
        return ""


HEVC_CODECS = ("hevc", "hvc1", "hev1", "h265")


def substream_url(url):
    """Best-effort sub-stream (lighter, usually H.264) URL for common camera brands.
    Returns '' when the URL already looks like a sub-stream or the brand is unknown."""
    u = (url or "").strip()
    if not u:
        return ""
    lower = u.lower()
    # Hikvision / Ezviz ISAPI: /Streaming/Channels/101 -> 102 (x01 main -> x02 sub)
    m = re.search(r"(/streaming/channels/)(\d+?)(01)(\b|/|\?|$)", lower)
    if m:
        s, e = m.start(3), m.end(3)
        return u[:s] + "02" + u[e:]
    if re.search(r"/streaming/channels/\d*02(\b|/|\?|$)", lower):
        return ""
    # Hikvision legacy: /h264/ch1/main/av_stream -> /h264/ch1/sub/av_stream
    if "/main/av_stream" in lower:
        i = lower.index("/main/av_stream")
        return u[:i] + "/sub/av_stream" + u[i + len("/main/av_stream"):]
    # Dahua / CP Plus / Amcrest: cam/realmonitor?channel=1&subtype=0 -> subtype=1
    if "realmonitor" in lower:
        if re.search(r"subtype=1(\b|&|$)", lower):
            return ""
        if re.search(r"subtype=\d", lower):
            return re.sub(r"(?i)subtype=\d", "subtype=1", u)
        return u + ("&" if "?" in u else "?") + "subtype=1"
    # Reolink: h264Preview_01_main -> h264Preview_01_sub
    if "preview_" in lower and "_main" in lower:
        return re.sub(r"(?i)_main", "_sub", u, count=1)
    # Uniview: /media/video1 -> /media/video2
    m = re.search(r"/media/video1(\b|/|\?|$)", lower)
    if m:
        return u[:m.start()] + "/media/video2" + u[m.start() + len("/media/video1"):]
    # TP-Link Tapo / generic: /stream1 -> /stream2
    m = re.search(r"/stream1(\b|/|\?|$)", lower)
    if m:
        return u[:m.start()] + "/stream2" + u[m.start() + len("/stream1"):]
    # generic '.../main' or 'main' token
    m = re.search(r"(?<![a-z])main(?![a-z])", lower)
    if m and "sub" not in lower:
        return u[:m.start()] + "sub" + u[m.end():]
    return ""


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
                "-fflags", "+discardcorrupt",
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
        self._ocr_q = None  # single OCR worker queue (created on first crossing)
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
            ocr_on = bool(self.cfg.get("enable_plate") and self.plate_reader is not None)
            eid = self.db.add_event(cr["label"], direction, "", image_path, plate_status="pending" if ocr_on else "")
            ev = {
                "id": eid,
                "vehicle_type": cr["label"],
                "direction": direction,
                "plate": "",
                "plate_status": "pending" if ocr_on else "",
                "plate_source": "",
                "image_path": image_path,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
            events.append(ev)
            if self.on_event:
                try:
                    self.on_event(ev)
                except Exception:
                    pass
            if ocr_on:
                # OCR is slow on CPU: run async so the frame loop never stalls.
                crops = []
                c = self._plate_crop(frame, original, cr["bbox"], w, h)
                if c is not None:
                    crops.append(c.copy())
                for _area, bc, _ts in self._best_crops.pop(cr["track_id"], []):
                    crops.append(bc)  # fresh crops for the next crossing of this track
                clog(f"plate OCR: track {cr['track_id']} {direction} queued ({len(crops)} crops)")
                self._queue_ocr(eid, cr["track_id"], ev, crops)
            else:
                try:
                    self.notifier.notify(ev)
                except Exception:
                    pass

        annotated = self._annotate(frame, a, b)
        return annotated, events

    def _queue_ocr(self, eid, tid, ev, crops):
        """ONE OCR worker for the whole engine: several vehicles crossing together
        must not start several torch jobs at once (that saturates the CPU and the
        UI/HTTP server stalls for minutes)."""
        if self._ocr_q is None:
            self._ocr_q = queue.Queue()
            threading.Thread(target=self._ocr_worker, daemon=True).start()
        self._ocr_q.put((time.time(), eid, tid, ev, crops))

    def _ocr_worker(self):
        while True:
            queued, eid, tid, ev, crops = self._ocr_q.get()
            wait = time.time() - queued
            if wait > OCR_MAX_QUEUE_WAIT_S:
                # alert must not wait behind a long OCR backlog: send it as "Not detected"
                clog(f"plate OCR: track {tid} event {eid} skipped (waited {wait:.0f}s in queue)")
                crops = []
            try:
                self._ocr_and_notify(eid, tid, ev, crops)
            except Exception as e:
                clog(f"plate OCR: worker error {e}")

    def _warm_plate_reader(self):
        ok = self.plate_reader.warmup()
        clog("plate OCR: models loaded, ready" if ok
             else f"plate OCR: model load FAILED ({self.plate_reader.last_error})")

    def _update_best_crops(self, frame, original, w, h):
        """Keep the 3 biggest (closest) crops per active track, spaced >=0.4s apart,
        so OCR can vote across several views of the plate."""
        live = set(self.tracker.tracks.keys())
        for tid in list(self._best_crops.keys()):
            if tid not in live:
                del self._best_crops[tid]
        src, sx, sy = frame, 1.0, 1.0
        if original is not None:
            oh, ow = original.shape[:2]
            sx, sy, src = ow / w, oh / h, original
        now = time.time()
        for tid, tr in self.tracker.tracks.items():
            x1, y1, x2, y2 = tr.bbox
            area = max(0, x2 - x1) * max(0, y2 - y1)
            if area < 40 * 40:
                continue
            lst = self._best_crops.setdefault(tid, [])
            if lst and now - lst[-1][2] < 0.4:
                continue
            if len(lst) >= 3 and area <= min(e[0] for e in lst):
                continue
            cx1, cy1 = max(0, int(x1 * sx)), max(0, int(y1 * sy))
            cx2, cy2 = min(src.shape[1], int(x2 * sx)), min(src.shape[0], int(y2 * sy))
            crop = src[cy1:cy2, cx1:cx2]
            if not crop.size:
                continue
            if crop.shape[1] > 1600:  # bound memory for 4K sources (never shrink 1080p/1440p plates)
                s = 1600 / crop.shape[1]
                crop = cv2.resize(crop, (1600, max(1, int(crop.shape[0] * s))))
            else:
                crop = crop.copy()
            lst.append((area, crop, now))
            lst.sort(key=lambda e: -e[0])
            del lst[3:]

    def _ocr_and_notify(self, eid, tid, ev, crops):
        plate, detail = "", "OCR unavailable"
        try:
            if hasattr(self.plate_reader, "read_many"):
                plate, detail = self.plate_reader.read_many(crops, budget_s=float(self.cfg.get("ocr_budget_s", 8)))
            else:  # simple readers (tests/plugins): first non-empty wins
                for c in crops:
                    plate = self.plate_reader.read(c) or ""
                    if plate:
                        break
                detail = "single-read"
        except Exception as e:
            detail = f"error: {e}"
        try:
            self.db.update_event_plate(eid, plate, source="ocr", status="done")
        except Exception:
            pass
        ev = {**ev, "plate": plate, "plate_status": "done", "plate_source": "ocr" if plate else ""}
        clog(f"plate OCR: track {tid} event {eid} -> '{plate or 'Not detected'}' ({detail}, {len(crops)} crops)")
        if not plate and getattr(self.plate_reader, "last_trace", None):
            clog("plate OCR raw reads: " + " | ".join(self.plate_reader.last_trace)[:400])
            px = getattr(self.plate_reader, "last_plate_px", 0)
            if 0 < px < MIN_PLATE_PX:
                clog(f"plate OCR hint: plate sirf ~{px}px chaudi hai (chahiye >= {MIN_PLATE_PX}px) — camera gate ke "
                     "aur paas/zoom karein ya camera ka main stream 2560x1440 par set karein")
        if self.on_event:
            try:
                self.on_event(ev)  # same id: UI updates the capture toast with the number
            except Exception:
                pass
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
