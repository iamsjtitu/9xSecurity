"""9x Security - Core processing engine (no GUI, unit-testable).

Ties together detection, tracking, line-crossing, snapshot saving and DB logging.
The GUI (main.py) feeds frames into `process_frame` and renders `annotated`.
"""
import math
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
from detector import VehicleDetector, category_of
from tracker import CentroidTracker, _iou as _box_iou

OCR_MAX_QUEUE_WAIT_S = float(os.environ.get("OCR_MAX_QUEUE_WAIT_S", "20"))
DEDUPE_S = float(os.environ.get("CROSS_DEDUPE_S", "20"))  # window for same-vehicle (track identity) de-dup
SAME_VEHICLE_S = float(os.environ.get("SAME_VEHICLE_S", "3"))  # two YOLO boxes on ONE vehicle cross within this
TWIN_APPEAR_S = 6.0  # a 2nd box born ON a crossing vehicle may 'appear at the line' this long after the 1st box crossed
PERSON_GROUP_S = float(os.environ.get("PERSON_GROUP_S", "2.5"))  # people crossing within this = one 'N persons' alert
GROUP_MAX_S = 15.0       # a person group never waits longer than this since its first member (steady traffic)
GROUP_RECENT_S = 8.0     # an alerted person/two-wheeler is remembered this long: a late twin box of it is merged, not re-alerted
SAME_MEMBER_IOU = 0.2    # a new crossing overlapping a member (or where it has moved to) this much = same object
SAME_MEMBER_DIST = 0.5   # ... or its centre within this many box-sizes of the member's predicted centre
SAME_SPOT_S = 1.0        # the plain 'same spot' test only within this: later, the same spot is the NEXT one in line
PERSON_MIN_ASPECT = 1.3  # a standing/walking human box is at least this much taller than wide (dog/cat ~0.8-1.2)
PERSON_SMALL_H = 0.08    # a 'person' shorter than this fraction of the frame needs a confident detection...
PERSON_SMALL_CONF = 0.45  # ... (far dogs/cats/bags at night come as hesitant small 'person' boxes)
ZOOM_ANIMAL_CONF = 0.30   # zoomed second look: an animal class at least this sure ...
ZOOM_ANIMAL_RATIO = 0.7   # ... and at least this fraction of the zoomed 'person' score => it IS an animal
ZOOM_EVERY_S = 1.0        # a 'person' track approaching the line gets a zoomed look this often
ZOOM_PERSON_MIN = 0.25    # the zoomed looks must have seen at least this much 'person' in total before a person alert ...
PERSON_SURE_CONF = 0.70   # ... unless the full-frame detections themselves averaged this confident (zoom blind spot)
PERSON_VEHICLE_S = float(os.environ.get("PERSON_VEHICLE_S", "6"))  # person crossing within this of a vehicle at the same spot = rider/driver
RIDER_GROW = 0.25       # vehicle box grown by this fraction when testing 'person ON/WITH the vehicle'
RIDER_MIN_OVERLAP = 0.2  # >= this fraction of the person's box inside the grown vehicle box
RIDER_SHAPE = 1.8        # a seated rider's box is shorter than this many widths (a walker is 2.5-3.5x)
RIDER_SPEED = 1.5        # ... and moves faster than this many body-heights per second (a walker ~0.8)
MOVING_S = 4.0           # a vehicle track counts as 'moving' this long after its last real move
SAME_OBJECT_IOU = 0.4    # a box in the OTHER tracker overlapping this much = the same object (YOLO car<->motorcycle flip)
TWIN_ALERT_S = 30.0      # ... and it was alerted this recently -> no second alert for it on the other line
LINE2_COLOR = (255, 0, 255)  # person/bike line (magenta); the main vehicle line stays yellow


def draw_line2(img, a, b):
    """Person/bike line on a frame: magenta + a small tag so it is never confused with the yellow line."""
    cv2.line(img, a, b, LINE2_COLOR, 2)
    mx, my = (a[0] + b[0]) // 2, (a[1] + b[1]) // 2
    cv2.putText(img, "PERSON / BIKE", (mx + 6, max(12, my - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, "PERSON / BIKE", (mx + 6, max(12, my - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, LINE2_COLOR, 1, cv2.LINE_AA)


def line2_px(cfg, w, h):
    """Pixel endpoints of cfg['line2'] or None when not drawn."""
    ln = cfg.get("line2")
    if not ln:
        return None
    try:
        return ((int(float(ln["x1"]) * w), int(float(ln["y1"]) * h)), (int(float(ln["x2"]) * w), int(float(ln["y2"]) * h)))
    except (KeyError, TypeError, ValueError):
        return None


def _point_in_box(pt, box, grow=0.0):
    x1, y1, x2, y2 = box
    gx, gy = grow * (x2 - x1), grow * (y2 - y1)
    return x1 - gx <= pt[0] <= x2 + gx and y1 - gy <= pt[1] <= y2 + gy


def _overlap_frac(inner, outer, grow=0.0):
    """Fraction of `inner` box area that lies inside `outer` (grown by `grow` per side)."""
    ix1, iy1, ix2, iy2 = inner
    ox1, oy1, ox2, oy2 = outer
    gx, gy = grow * (ox2 - ox1), grow * (oy2 - oy1)
    w = min(ix2, ox2 + gx) - max(ix1, ox1 - gx)
    h = min(iy2, oy2 + gy) - max(iy1, oy1 - gy)
    area = max(1, (ix2 - ix1) * (iy2 - iy1))
    return (max(0, w) * max(0, h)) / area
MIN_PLATE_PX = 160


def _segment_hits_box(a, b, box):
    """Does line segment a-b pass through (or touch) the bbox?"""
    x1, y1, x2, y2 = box
    if x1 <= a[0] <= x2 and y1 <= a[1] <= y2 or x1 <= b[0] <= x2 and y1 <= b[1] <= y2:
        return True
    from tracker import _hits_segment
    corners = [((x1, y1), (x2, y1)), ((x2, y1), (x2, y2)), ((x2, y2), (x1, y2)), ((x1, y2), (x1, y1))]
    return any(_hits_segment(p, q, a, b, margin=0.0) for p, q in corners)  # below this plate width (camera px) OCR is guesswork: user's gate plates were 60-130px


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
    Returns (ok, [(step_name, step_ok, detail), ...], effective_url) — effective_url differs
    from the input when the stream path was auto-detected."""
    import socket

    from rtsp_discover import discover_stream_url, rtsp_describe

    steps = []
    fixed = normalize_rtsp_url(url)
    clog(f"=== PROBE start: {redact_url(fixed)}")
    steps.append(("URL check", True,
                  f"Password ke special characters auto-fix kiye:\n    {fixed}" if fixed != url
                  else "URL format theek hai"))
    m = re.match(r"^rtsps?://(?:[^/]*@)?([^:/?#]+)(?::(\d+))?", fixed, re.IGNORECASE)
    if not m:
        steps[-1] = ("URL check", False, "URL 'rtsp://' se shuru hona chahiye")
        return False, steps, fixed
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
        return False, steps, fixed

    # RTSP handshake: tells apart wrong password (401) from wrong stream path (404) in ms
    code, first = rtsp_describe(fixed)
    clog(f"probe: DESCRIBE -> {code} {first}")
    auth_failed = False
    if code == 200:
        steps.append(("RTSP handshake", True, "Camera ne username/password aur stream path accept kiya (200 OK)"))
    elif code == 401:
        auth_failed = True
        steps.append(("RTSP handshake", False,
                      "Camera ne 401 Unauthorized diya — username/password galat lag raha hai. "
                      "Fir bhi FFmpeg se video try kar rahe hain..."))
    elif code:
        steps.append(("RTSP handshake", False,
                      f"Camera par ye stream path nahi hai ({first.strip()}) — camera ka asli stream path chahiye"))
        found, why = discover_stream_url(fixed, log=clog)
        if found:
            clog(f"probe: stream path auto-detected via {why}: {redact_url(found)}")
            steps.append(("Stream path auto-detect", True,
                          f"Camera se asli stream path mil gaya ({'ONVIF' if why == 'onvif' else 'known vendor path'}):\n"
                          f"    {found}\nURL apne aap update kar diya — Connect dabayein."))
            fixed = found
        elif why == "auth":
            steps.append(("Stream path auto-detect", False, "Username/password galat (camera ne 401 diya)"))
            return False, steps, fixed
        else:
            steps.append(("Stream path auto-detect", False,
                          "Camera ka stream path nahi mil paya (ONVIF band hai ya unknown brand). Camera ke "
                          "web page/manual me 'RTSP URL' dekh kar path lagayein — e.g. Hikvision/Prama: "
                          "/Streaming/Channels/101, Dahua/CP Plus: /cam/realmonitor?channel=1&subtype=0, "
                          "TP-Link: /stream1, Reolink: /h264Preview_01_main"))
            return False, steps, fixed
    else:
        steps.append(("RTSP handshake", False,
                      f"Camera ne RTSP jawab nahi diya ({first}) — camera settings me RTSP enable hai? "
                      "Port 554 sahi hai? Fir bhi video try kar rahe hain..."))

    ok, detail = _try_capture(fixed, "tcp", wait)
    steps.append(("Video stream (TCP)", ok, detail))
    if ok:
        return True, steps, fixed
    ok, detail = _try_capture(fixed, "udp", wait)
    steps.append(("Video stream (UDP)", ok, detail))
    if ok:
        return True, steps, fixed
    ok, detail = _try_ffmpeg_pipe(fixed)
    steps.append(("Video stream (FFmpeg engine)", ok, detail))
    if ok:
        return True, steps, fixed
    if auth_failed:
        steps.append(("Hint", False,
                      "Camera ne 401 diya aur video bhi nahi mila — username/password galat hai. Camera ke web page "
                      "par yahi user/password se login karke check karein (kuch cameras me RTSP ka alag "
                      "'ONVIF/RTSP user' hota hai)."))
        return False, steps, fixed
    steps.append(("Hint", False,
                  "Camera network par hai par video nahi mila — zyada tar username/password "
                  "ya stream path (stream1 vs stream2) galat hota hai. Yahi URL VLC me "
                  "(Media > Open Network Stream) test karein."))
    return False, steps, fixed


def auto_fix_stream_url(source, log=clog):
    """Called when a live RTSP open failed: if the camera says 404 for this path, find the real
    stream URL (ONVIF / vendor paths). -> (fixed_url or '', reason)."""
    from rtsp_discover import discover_stream_url, rtsp_describe

    code, first = rtsp_describe(source)
    log(f"svc: RTSP DESCRIBE -> {code} {first}")
    if code == 200:
        return "", "path-ok"
    if code == 401:
        return "", "auth"
    if code == 0:
        return "", "no-answer"
    found, why = discover_stream_url(source, log=log)
    if found:
        log(f"svc: stream path auto-detected via {why}: {redact_url(found)}")
    return found, why


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
                allowed=config.detect_classes(self.cfg),
            )
        else:
            self.model_tier = "custom"
        self.detector = detector
        # two trackers, two lines: car/truck/bus cross the MAIN (yellow) line; person + two-wheeler
        # (+ animals as context) cross the person/bike line — or the main line when none is drawn.
        # Every object lives in exactly one tracker, so it can never be counted on both lines.
        self.tracker = CentroidTracker()
        # merge_twins only here: a cycle often carries two live boxes; a queued CAR behind another must stay countable
        self.tracker2 = CentroidTracker(merge_twins=True)
        self.plate_reader = plate_reader
        if self.cfg.get("enable_plate") and self.plate_reader is None:
            from plate_reader import PlateReader

            self.plate_reader = PlateReader()
        self._best_crops = {}
        self._ocr_q = None  # single OCR worker queue (created on first crossing)
        self._recent_cross = {1: [], 2: []}  # per tracker: (ts, to_side, bbox, tid) of counted crossings for de-duplication
        self._groups = {}  # (category, direction) -> pending group (people crossing together)
        self._group_recent = []  # persons/two-wheelers already alerted: a late twin box of one of them is merged, not re-alerted
        self._person_drop_log_ts = 0.0
        self._zoom_model_logged = False
        self._recent_vehicle_cross = []  # (ts, direction, bbox, label) — riders/drivers are not lone persons
        self._motion = {}  # (tracker no, track id) -> [last_move_ts, ref_point]
        self._det_ts = time.time()
        self._reject_log_ts = {}  # track id -> last time a rejected crossing was logged
        self.possible_misses = 0  # tracks that changed side without a counted crossing (diagnostics)
        self.line_hints = []
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

    def has_line2(self):
        return bool(self.cfg.get("line2"))

    def line2_points(self, w, h):
        """Person/bike line in pixels; the main line when none is drawn."""
        ln = self.cfg.get("line2")
        if not ln:
            return self.line_points(w, h)
        return (int(ln["x1"] * w), int(ln["y1"] * h)), (int(ln["x2"] * w), int(ln["y2"] * h))

    def _tracker(self, which):
        return self.tracker2 if which == 2 else self.tracker

    def _live_tracks(self):
        for which, trk in ((1, self.tracker), (2, self.tracker2)):
            for tid, tr in trk.tracks.items():
                yield (which, tid), tr

    def _direction_for(self, to_side, which=1):
        """Map crossing target side sign to Entry/Exit per configuration (per line)."""
        ed = config.entry_direction2(self.cfg) if (which == 2 and self.has_line2()) else self.cfg.get("entry_direction", "pos")
        entry_sign = 1 if ed == "pos" else -1
        return "Entry" if to_side == entry_sign else "Exit"

    # ---- main per-frame pipeline ------------------------------------------
    def use_fast_model(self):
        """Auto mode fallback: swap to yolov8n when the accurate model is too slow here."""
        from detector import resolve_model_path

        path, self.model_tier = resolve_model_path("fast")
        self.detector = VehicleDetector(
            model_path=path, conf=self.cfg.get("confidence", 0.4), allowed=config.detect_classes(self.cfg)
        )
        return self.detector.model_name

    def _zones_px(self, w, h):
        out = []
        for z in self.cfg.get("ignore_zones") or []:
            try:
                x1, x2 = sorted((float(z["x1"]) * w, float(z["x2"]) * w))
                y1, y2 = sorted((float(z["y1"]) * h, float(z["y2"]) * h))
                out.append((int(x1), int(y1), int(x2), int(y2)))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def _drop_ignored(self, dets, w, h):
        """Ignore zones (parking): a detection whose bottom-centre lies inside a zone is
        not tracked at all — parked vehicles there can never produce Entry/Exit."""
        zones = self._zones_px(w, h)
        if not zones or not dets:
            return dets
        keep = []
        for d in dets:
            x1, y1, x2, y2 = d["bbox"]
            px, py = (x1 + x2) // 2, y2
            if any(zx1 <= px <= zx2 and zy1 <= py <= zy2 for zx1, zy1, zx2, zy2 in zones):
                continue
            keep.append(d)
        return keep

    def _sanitize_persons(self, dets, frame_h):
        """A standing/walking human is clearly taller than wide. A 'person' box that is squat
        (dog, cat, cow, lying object: h < 1.3 w), tinier than 4% of the frame, or small AND
        hesitant (far animal at night) can never become a person alert."""
        out = []
        now = time.time()
        for d in dets:
            if d["label"] == "person":
                x1, y1, x2, y2 = d["bbox"]
                w, h = max(1, x2 - x1), y2 - y1
                conf = float(d.get("conf", 1.0))
                why = ""
                if h < 0.04 * frame_h:
                    why = "bahut chhota"
                elif h < PERSON_MIN_ASPECT * w:
                    why = f"janwar jaisa aakar ({w}x{h}, insaan ki lambai chaudai se {PERSON_MIN_ASPECT}x hoti hai)"
                elif h < PERSON_SMALL_H * frame_h and conf < PERSON_SMALL_CONF:
                    why = f"chhota aur ansure ({w}x{h}, conf {conf:.2f})"
                if why:
                    if now - self._person_drop_log_ts > 30:
                        self._person_drop_log_ts = now
                        clog(f"person box ignored: {why} — dog/cat/bag, insaan nahi (box {d['bbox']})")
                    continue
            out.append(d)
        return out

    def _log_rejects(self):
        """camera_log: why a side flip was NOT counted (rate-limited per track) + possible misses."""
        now = time.time()
        for which, trk in ((1, self.tracker), (2, self.tracker2)):
            for r in trk.rejects:
                tid = r["track_id"]
                if r["kind"] == "possible-miss":
                    self.possible_misses += 1
                    clog(f"POSSIBLE MISS: track {tid} {r['label']} line ke doosri taraf pahuncha par count nahi hua — "
                         f"{r['reason']} (box {r['bbox']})")
                    continue
                if r["kind"] == "resumed":
                    clog(f"track {tid} = wahi gaadi jo pehle count ho chuki hai (purana track {r['from']}, line par khadi/"
                         f"doosra box) — dobara count nahi hogi (box {r['bbox']})")
                    continue
                # a standing vehicle wobbles every frame: explain it once a minute, other rejects every 10 s
                key = (which, tid)
                if now - self._reject_log_ts.get(key, 0) < (60 if r["kind"] == "jitter" else 10):
                    continue
                self._reject_log_ts[key] = now
                clog(f"crossing rejected: track {tid} {r['label']} {self._direction_for(r['to_side'], which)} — "
                     f"{r['reason']} (box {r['bbox']})")
        for key in [k for k in self._reject_log_ts if k[1] not in self._tracker(k[0]).tracks]:
            del self._reject_log_ts[key]

    def _geom_hints(self, a, b, w, h, prefix=""):
        hints = []
        m = 0.04
        if any(not (m * w <= p[0] <= (1 - m) * w and m * h <= p[1] <= (1 - m) * h) for p in (a, b)):
            hints.append(f"{prefix}Line picture ke kinare tak jaa rahi hai — kinare par kati hui (aadhi dikhti) gaadi ka sahi "
                         "point nahi milta, crossing miss ya galat ho sakti hai; line ke dono sire kinare se kam se kam 5% andar rakhein")
        if math.hypot(b[0] - a[0], b[1] - a[1]) < 0.12 * w:
            hints.append(f"{prefix}Line bahut chhoti hai — gate ki poori chaudai par khichein taaki har gaadi ise kaate")
        return hints

    def _update_line_hints(self, a, b, w, h, a2=None, b2=None):
        """Live placement advice for the yellow line (shown under 'Draw Detection Line')."""
        hints = self._geom_hints(a, b, w, h)
        if self.has_line2() and a2 is not None:
            hints += self._geom_hints(a2, b2, w, h, "Person/Bike line: ")
        now = time.time()
        for tr in self.tracker.tracks.values():
            x1, y1, x2, y2 = tr.bbox
            still = getattr(tr, "_first_seen", None)
            if still is None:
                tr._first_seen, tr._first_bbox = now, tr.bbox
                continue
            moved = abs((x1 + x2) // 2 - (tr._first_bbox[0] + tr._first_bbox[2]) // 2) + abs(y2 - tr._first_bbox[3])
            if now - still > 8 and moved < 0.1 * max(x2 - x1, y2 - y1) and _segment_hits_box(a, b, tr.bbox):
                hints.append(f"Ek khadi gaadi ({tr.label}) line ke upar hai — use Ignore Zone se cover karein ya line hatayein")
                break
        self.line_hints = hints[:3]

    def _dedupe_crossings(self, crossings, which=1):
        """One photo per vehicle — but never swallow the NEXT vehicle. A same-direction
        crossing at the same spot within DEDUPE_S is dropped ONLY when track identity proves
        it is the same physical vehicle: the earlier track is still alive and still sits on
        this crossing (two YOLO boxes on one long vehicle), or it aged out right here and a
        new track was re-created at the same spot. A car/JCB that drove on (its track moved
        away or aged out past the line) must never block the vehicle following it through the
        gate. NOTE: every crossing bbox sits on the gate line, so two different vehicles always
        overlap here — overlap+timing alone can never tell them apart, only track identity can."""
        now = time.time()
        recent = [r for r in self._recent_cross[which] if now - r[0] < DEDUPE_S]
        self._recent_cross[which] = recent
        out = []
        for cr in crossings:
            why = self._duplicate_reason(cr, now, self._tracker(which), recent)
            if why:
                clog(f"crossing ignored: duplicate ({why}) of a vehicle counted <{DEDUPE_S:.0f}s ago (track {cr['track_id']})")
                continue
            recent.append((now, cr["to_side"], cr["bbox"], cr["track_id"]))
            out.append(cr)
        return out

    def _duplicate_reason(self, cr, now, trk, recent):
        newtr = trk.tracks.get(cr["track_id"])
        for ts, side, box, tid in recent:
            if side != cr["to_side"] or tid == cr["track_id"]:
                continue
            lost = trk.lost.get(tid)
            # Same vehicle that stopped on the line, was lost, and re-appeared there with a NEW
            # track id — its crossing can fire a few frames later, after it has moved off the
            # earlier box, so match on where the new track was BORN, not where it is now.
            if (cr.get("via") == "appeared-at-line" and newtr is not None and lost is not None
                    and _point_in_box(newtr.start_ref, lost[0], 0.25)):
                return "track re-created where the last one was lost"
            live = trk.tracks.get(tid)
            # 2nd YOLO box BORN on a vehicle that was just crossing (long truck: cab + body): its own
            # 'appeared-at-line' fires 1-5 s later and further in, so match where it was born and
            # check that the first box is still glued to it now
            if (cr.get("via") == "appeared-at-line" and newtr is not None and live is not None
                    and now - ts <= TWIN_APPEAR_S and _point_in_box(newtr.start_ref, box, 0.25)
                    and _box_iou(live.bbox, cr["bbox"]) > 0.3):
                return "second box born on the same vehicle"
            if _box_iou(box, cr["bbox"]) <= 0.25:
                continue  # crossed at a different place -> different vehicle
            if live is not None:
                # earlier vehicle still tracked: same body only if its box still sits on the new
                # crossing (two YOLO boxes on one tractor-trolley) AND the boxes crossed within
                # SAME_VEHICLE_S of each other. Queued cars in a front-view camera overlap in the
                # picture too, but the second one crosses seconds later -> it is the NEXT vehicle.
                if now - ts <= SAME_VEHICLE_S and _box_iou(live.bbox, cr["bbox"]) > 0.3:
                    return "second box on the same vehicle"
                continue
            # earlier track aged out right here and a new one was born at the same spot: only a
            # vehicle that APPEARED at the line can be that remnant — a track that was followed
            # from the other side and crossed here ('cross') is the vehicle behind it.
            if lost is not None and cr.get("via") == "appeared-at-line" and _box_iou(lost[0], cr["bbox"]) > 0.3:
                return "track re-created at the same spot"
        return ""

    def process_frame(self, frame, original=None):
        """
        frame: BGR image already resized to processing/display resolution.
        original: optional full-res frame for higher quality plate/snapshot.
        Returns (annotated_frame, events_this_frame)
        """
        h, w = frame.shape[:2]
        a, b = self.line_points(w, h)
        a2, b2 = self.line2_points(w, h)
        self.tracker.near_band = self.tracker2.near_band = 0.10 * h
        skip = max(1, int(self.cfg.get("detect_frame_skip", 2)))

        if self.frame_idx % skip == 0:
            t0 = time.time()
            self.last_dets = self.detector.detect(frame)
            self.last_detect_ms = (time.time() - t0) * 1000
            self._det_ts = t0  # tracker speed/matching maths use the DETECTION time (skip frames re-feed stale boxes)
        self.frame_idx += 1

        self.last_dets = self._sanitize_persons(self._drop_ignored(self.last_dets, w, h), h)
        # car/truck/bus -> main line; person/two-wheeler/animal -> person/bike line (= main line when not drawn)
        dets_v = [d for d in self.last_dets if category_of(d["label"]) == "vehicle"]
        dets_p = [d for d in self.last_dets if category_of(d["label"]) != "vehicle"]
        crossings = []
        for which, trk, dets, ln in ((1, self.tracker, dets_v, (a, b)), (2, self.tracker2, dets_p, (a2, b2))):
            crs = trk.update(dets, ln, now=self._det_ts, frame_size=(w, h))
            for cr in crs:
                cr["which"] = which
            crossings += self._dedupe_crossings(crs, which)
        self._log_rejects()
        self._update_motion(time.time())
        self._zoom_patrol(frame, original, w, h, time.time())
        self._update_line_hints(a, b, w, h, a2, b2)
        if self.cfg.get("enable_plate") and self.plate_reader is not None:
            self._update_best_crops(frame, original, w, h)

        events = []
        now = time.time()
        self._recent_vehicle_cross = [r for r in self._recent_vehicle_cross if now - r[0] < PERSON_VEHICLE_S]
        for cr in crossings:
            live = self._tracker(cr["which"]).tracks.get(cr["track_id"])
            if live is not None:
                cr["label"] = live.label  # latest vote (a zoomed look this frame may have made it an animal)
            cat = category_of(cr["label"])
            direction = self._direction_for(cr["to_side"], cr["which"])
            if cat in ("vehicle", "two_wheeler"):
                # remembered even when this category is OFF: a rider/driver must never count as a person
                self._recent_vehicle_cross.append((now, direction, cr["bbox"], cr["label"]))
            if not self._category_enabled(cr, cat):
                continue
            if not config.category_counts_now(self.cfg, cat):
                clog(f"crossing ignored: {cat} schedule ke bahar (track {cr['track_id']})")
                continue
            if cat == "person":
                why = self._with_vehicle(cr)
                if why:
                    clog(f"person ignored: {why} — gaadi/bike ke saath hai, akela paidal nahi (track {cr['track_id']})")
                    continue
                why = self._animal_on_zoom(frame, original, cr, w, h)
                if why:
                    clog(f"person ignored: {why} — janwar hai, insaan nahi (track {cr['track_id']})")
                    continue
                self._queue_group(cat, frame, cr, direction, now)
            else:
                why = self._same_object_alerted(cr, now)
                if why:
                    clog(f"crossing ignored: {why} (track {cr['track_id']} {cr['label']})")
                    continue
                self._mark_twins(cr, now)
                if cat == "two_wheeler":
                    # photo RIGHT at the crossing (user 12-10) — but the same cycle seen again as another
                    # track (twin box, ID swap, re-born track) within seconds is never a second photo
                    why = self._seen_again(cat, cr, direction, now)
                    if why:
                        clog(f"two-wheeler crossing merged: {why} — dobara photo nahi (track {cr['track_id']} {cr['label']})")
                        continue
                events += self._emit_crossing(frame, original, cr, cat, w, h)
        events += self.flush_pending(now=now)

        annotated = self._annotate(frame, a, b, a2, b2)
        return annotated, events

    def _same_object_alerted(self, cr, now):
        """YOLO may call one auto/tractor 'car' in some frames and 'motorcycle' in others: then
        BOTH trackers follow it. The first alert marks the twin track in the other tracker, so
        the same object never gets a second alert on the other line."""
        tr = self._tracker(cr["which"]).tracks.get(cr["track_id"])
        twin = getattr(tr, "twin_alert", None)
        if twin and now - twin[0] < TWIN_ALERT_S:
            return f"same object: {twin[1]} ke roop me {now - twin[0]:.0f}s pehle alert ho chuka (doosri line)"
        return ""

    def _mark_twins(self, cr, now):
        other = self._tracker(2 if cr["which"] == 1 else 1)
        for tr in other.tracks.values():
            if _box_iou(tr.bbox, cr["bbox"]) >= SAME_OBJECT_IOU:
                tr.twin_alert = (now, cr["label"])

    def _with_vehicle(self, cr):
        """Rider / driver / passenger test: the person's box sits on a MOVING vehicle or two-wheeler
        track (a parked truck beside a walker must not hide him), or a vehicle crossed at this very
        spot moments before (or after — re-checked at flush)."""
        now = time.time()
        # the rider himself when YOLO never boxed the bike (user 11-10: bike + rider = two 'person'
        # boxes): a SEATED shape (short, wide box) moving at bike speed is nobody walking
        own = self.tracker2.tracks.get(cr["track_id"])
        x1, y1, x2, y2 = cr["bbox"]
        w, h = max(1, x2 - x1), max(1, y2 - y1)
        if own is not None and own.vel is not None and h < RIDER_SHAPE * w:
            speed = math.hypot(own.vel[0], own.vel[1]) / h
            if speed > RIDER_SPEED:
                return f"baitha hua aur tez ({speed:.1f} body-height/s, box {w}x{h}) — bike/gaadi par sawaar"
        for key, tr in self._live_tracks():
            if category_of(tr.label) not in ("vehicle", "two_wheeler") or not self._is_moving(key, now):
                continue
            if _overlap_frac(cr["bbox"], tr.bbox, RIDER_GROW) >= RIDER_MIN_OVERLAP:
                return f"{tr.label} (track {key[1]}) ke upar/saath"
        direction = self._direction_for(cr["to_side"], cr.get("which", 2))
        for ts, dirn, box, label in self._recent_vehicle_cross:
            if dirn == direction and _overlap_frac(cr["bbox"], box, RIDER_GROW) >= RIDER_MIN_OVERLAP:
                return f"{label} ne {now - ts:.1f}s pehle yahin cross kiya"
        return ""

    def _update_motion(self, now):
        """Per-track 'last moved' timestamp (ref point moved >= 15% of box height)."""
        live = dict(self._live_tracks())
        for key in [k for k in self._motion if k not in live]:
            del self._motion[key]
        for key, tr in live.items():
            ref = CentroidTracker.ref_point(tr.bbox)
            prev = self._motion.get(key)
            if prev is None:
                self._motion[key] = [now, ref]
                continue
            h = max(1, tr.bbox[3] - tr.bbox[1])
            if math.hypot(ref[0] - prev[1][0], ref[1] - prev[1][1]) >= 0.15 * h:
                prev[0], prev[1] = now, ref

    def _is_moving(self, key, now):
        m = self._motion.get(key)
        return m is not None and now - m[0] < MOVING_S

    def _zoom_look(self, frame, original, bbox, w, h):
        """Run detector.second_opinion on the sharpest source we have (full-res frame if any)."""
        look = getattr(self.detector, "second_opinion", None)
        if look is None:
            return None
        x1, y1, x2, y2 = bbox
        src, box = frame, bbox
        if original is not None:  # full-res crop = even clearer animal
            oh, ow = original.shape[:2]
            sx, sy = ow / w, oh / h
            src, box = original, (int(x1 * sx), int(y1 * sy), int(x2 * sx), int(y2 * sy))
        try:
            op = look(src, box)
        except Exception as ex:  # a failed second look must never block a real person alert
            clog(f"zoom check failed: {ex}")
            return None
        if op and not self._zoom_model_logged:
            self._zoom_model_logged = True
            clog(f"zoom (doosri nazar) model: {op.get('model') or getattr(self.detector, 'model_name', '?')}")
        return op

    @staticmethod
    def _zoom_says_animal(op):
        return bool(op) and op["animal"] >= ZOOM_ANIMAL_CONF and op["animal"] >= ZOOM_ANIMAL_RATIO * op["person"]

    def _zoom_record(self, tr, op):
        """Add one zoomed look to the track's evidence and decide its verdict from ALL looks so far:
        animal when the summed animal confidence is >= ZOOM_ANIMAL_CONF and >= ZOOM_ANIMAL_RATIO x
        the summed person confidence. The verdict overrides frame votes (a dog called 'person' in
        every frame stays a dog), yet a track that later shows a clear person on several looks
        (the track jumped from the dog to a man) is a person again. Returns True when animal."""
        if not op:
            return False
        if tr is None:
            return self._zoom_says_animal(op)
        tr.zoom_looks += 1
        tr.zoom_animal += op["animal"]
        tr.zoom_person += op["person"]
        if op["animal"] > tr.zoom_best:
            tr.zoom_best, tr.zoom_label = op["animal"], op["animal_label"]
        animal = bool(tr.zoom_label) and tr.zoom_animal >= ZOOM_ANIMAL_CONF and tr.zoom_animal >= ZOOM_ANIMAL_RATIO * tr.zoom_person
        tr.verdict = tr.zoom_label if animal else None
        return animal

    def _zoom_patrol(self, frame, original, w, h, now):
        """Every ZOOM_EVERY_S, every 'person' track near the line gets a zoomed second look while it
        is still approaching — several looks before the crossing, so one unlucky frame cannot let
        a dog through."""
        if not self.cfg.get("enable_person"):
            return
        band = 1.5 * self.tracker2.near_band
        for tid, tr in self.tracker2.tracks.items():
            # 'person' tracks — and tracks already judged animal, so clear person looks can still correct them
            if tr.disappeared or (category_of(tr.label) != "person" and not tr.verdict) or abs(tr.dist) > band:
                continue
            if now - getattr(tr, "zoom_ts", 0.0) < ZOOM_EVERY_S:
                continue
            tr.zoom_ts = now
            op = self._zoom_look(frame, original, tr.bbox, w, h)
            had = tr.verdict
            if self._zoom_record(tr, op) and not had:
                clog(f"track {tid} 'person' nahi — zoom karke dekha: {op['animal_label']} {op['animal']:.2f} "
                     f"vs person {op['person']:.2f} (kul {tr.zoom_animal:.2f} vs {tr.zoom_person:.2f}) — janwar, "
                     f"alert nahi hoga (box {tr.bbox})")
            elif had and not tr.verdict:
                clog(f"track {tid} zoom par ab saaf insaan dikha (kul person {tr.zoom_person:.2f} vs janwar "
                     f"{tr.zoom_animal:.2f}) — person maana jayega")

    def _animal_on_zoom(self, frame, original, cr, w, h):
        """Last zoomed look right before a person alert (user 17-10: dogs at night sent as PERSON —
        tall and narrow when walking towards the camera, so the shape test cannot catch them).
        Fail closed: when the zoomed looks never saw a person either (yolov8n is blind to a far
        night dog even zoomed) a hesitant full-frame 'person' is no alert — a real walker is a
        clear person on the zoomed crop or a confident one on the full frame."""
        op = self._zoom_look(frame, original, cr["bbox"], w, h)
        tr = self.tracker2.tracks.get(cr["track_id"])
        if self._zoom_record(tr, op):
            label = tr.zoom_label if tr is not None else op["animal_label"]
            return f"zoom karke dekha: {label} {op['animal']:.2f} vs person {op['person']:.2f}"
        if tr is not None and tr.zoom_looks and tr.zoom_person < ZOOM_PERSON_MIN and tr.person_conf < PERSON_SURE_CONF:
            return (f"zoom par {tr.zoom_looks} baar dekha, insaan kahin nahi dikha (person {tr.zoom_person:.2f}, "
                    f"janwar {tr.zoom_animal:.2f}; poore frame par bhi sirf {tr.person_conf:.2f} sure) — "
                    f"chhota/dhundhla janwar ya saaya, insaan confirm nahi")
        return ""

    def _member(self, cat, cr, direction, now):
        tr = self.tracker2.tracks.get(cr["track_id"])
        return {"cr": cr, "ts": now, "bbox": cr["bbox"], "vel": getattr(tr, "vel", None), "cat": cat,
                "direction": direction, "start_ref": getattr(tr, "start_ref", None),
                "born_ts": getattr(tr, "born_ts", now)}

    def _seen_again(self, cat, cr, direction, now):
        """Already alerted (last GROUP_RECENT_S) as another track? Remembers the new one otherwise."""
        self._group_recent = [m for m in self._group_recent if now - m["ts"] < GROUP_RECENT_S]
        member = self._member(cat, cr, direction, now)
        same = self._same_member([m for m in self._group_recent if m["cat"] == cat and m["direction"] == direction],
                                 member, now)
        if not same:
            self._group_recent.append(member)
        return same

    def _queue_group(self, cat, frame, cr, direction, now):
        """People pass a gate in a loose group (0.5-3 s apart). Hold the alert a quiet time after
        the LAST one so the group becomes ONE alert with the right count — and merge boxes that
        are the same person again (a track re-created after an ID swap, a second box on one
        walker): they must never inflate the count or re-alert."""
        member = self._member(cat, cr, direction, now)
        self._group_recent = [m for m in self._group_recent if now - m["ts"] < GROUP_RECENT_S]
        g = self._groups.setdefault((cat, direction), {"crs": [], "frame": None, "last": now, "first": now})
        same = self._same_member(g["crs"], member, now) or self._same_member(
            [m for m in self._group_recent if m["cat"] == cat and m["direction"] == direction], member, now)
        if same:
            clog(f"{cat} crossing merged: {same} — dobara count/alert nahi (track {cr['track_id']} {cr['label']})")
            return  # the photo stays the real members' crossing frame
        g["crs"].append(member)
        g["frame"] = frame.copy()
        g["last"] = now

    @staticmethod
    def _same_member(members, new, now):
        """Is `new` the same object as one already in `members`? Same track, same spot, or the spot
        where that member has MOVED TO since (its box shifted by its own speed x time): two cycles
        side by side stay apart, one cycle seen again a few seconds later lands on its prediction."""
        bx1, by1, bx2, by2 = new["bbox"]
        ncx, ncy = (bx1 + bx2) / 2, (by1 + by2) / 2
        nsize = max(bx2 - bx1, by2 - by1, 1)
        for m in members:
            if m["cr"]["track_id"] == new["cr"]["track_id"]:
                return f"same track {m['cr']['track_id']}"
            if now - m["ts"] <= SAME_SPOT_S and _box_iou(m["bbox"], new["bbox"]) > SAME_MEMBER_IOU:
                return "same spot as a box counted this very second"
            # a second box BORN on that member around its crossing ('appeared at the line' later)
            if (new["cr"].get("via") == "appeared-at-line" and new.get("start_ref") is not None
                    and new.get("born_ts", now) <= m["ts"] + 1.0 and _point_in_box(new["start_ref"], m["bbox"], 0.25)):
                return f"doosra box jo {m['cr']['label']} track {m['cr']['track_id']} par hi bana tha"
            vel = m["vel"]
            if vel is None:
                continue
            dt = max(0.0, now - m["ts"])
            dx, dy = vel[0] * dt, vel[1] * dt
            x1, y1, x2, y2 = m["bbox"]
            pred = (int(x1 + dx), int(y1 + dy), int(x2 + dx), int(y2 + dy))
            pcx, pcy = (x1 + x2) / 2 + dx, (y1 + y2) / 2 + dy
            size = max(x2 - x1, y2 - y1, nsize)
            if (_box_iou(pred, new["bbox"]) > SAME_MEMBER_IOU
                    or math.hypot(ncx - pcx, ncy - pcy) <= SAME_MEMBER_DIST * size):
                return f"wahi {m['cr']['label']} jo {dt:.1f}s pehle count hua (aage badh gaya)"
        return ""

    def flush_pending(self, now=None, force=False):
        """Emit person groups whose quiet time is over (or all of them when force=True, e.g. the
        worker is stopping)."""
        now = time.time() if now is None else now
        out = []
        for key in list(self._groups):
            g = self._groups[key]
            cat, direction = key
            if force or now - g["last"] >= PERSON_GROUP_S or now - g["first"] >= GROUP_MAX_S:
                del self._groups[key]
                crs = []
                for m in g["crs"]:
                    live = self.tracker2.tracks.get(m["cr"]["track_id"])
                    if live is not None and category_of(live.label) == "animal":  # a zoomed look during the hold
                        clog(f"person ignored: zoom ne {live.label} bataya — janwar hai (track {m['cr']['track_id']})")
                        continue
                    # a vehicle may have crossed right AFTER the person was queued (driver sits ahead of the wheels)
                    why = self._with_vehicle(m["cr"]) if cat == "person" else ""
                    if why:
                        clog(f"person ignored: {why} — gaadi/bike ke saath hai (track {m['cr']['track_id']})")
                    else:
                        crs.append(m)
                if crs:
                    self._group_recent += crs
                    out += self._emit_group(g["frame"], [m["cr"] for m in crs], direction, cat)
        return out

    def _category_enabled(self, cr, cat):
        if cat == "animal":
            return False  # detected only as context: a dog/cow is never an alert
        if cat == "person":
            return bool(self.cfg.get("enable_person"))
        if cat == "two_wheeler":
            return bool(self.cfg.get("enable_two_wheeler"))
        return cr["label"] in (self.cfg.get("vehicle_classes") or ["car", "truck", "bus"])

    def _emit_crossing(self, frame, original, cr, cat, w, h):
        """Vehicle / two-wheeler crossing -> one event (+ async plate OCR for vehicles)."""
        direction = self._direction_for(cr["to_side"], cr.get("which", 1))
        image_path = self._save_snapshot(frame, cr, direction)
        ocr_on = bool(cat == "vehicle" and self.cfg.get("enable_plate") and self.plate_reader is not None)
        gate = config.gate_name(self.cfg)
        eid = self.db.add_event(cr["label"], direction, "", image_path, plate_status="pending" if ocr_on else "", gate=gate)
        ev = {
            "id": eid,
            "vehicle_type": cr["label"],
            "category": cat,
            "count": 1,
            "bbox": [int(v) for v in cr["bbox"]],
            "gate": gate,
            "direction": direction,
            "plate": "",
            "plate_status": "pending" if ocr_on else "",
            "plate_source": "",
            "image_path": image_path,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        if self.on_event:
            try:
                self.on_event(ev)
            except Exception:
                pass
        if ocr_on:
            crops = []
            c = self._plate_crop(frame, original, cr["bbox"], w, h)
            if c is not None:
                crops.append(c.copy())
            for _area, bc, _ts in self._best_crops.pop(cr["track_id"], []):
                crops.append(bc)
            clog(f"plate OCR: track {cr['track_id']} {direction} queued ({len(crops)} crops)")
            self._queue_ocr(eid, cr["track_id"], ev, crops)
        else:
            try:
                self.notifier.notify(ev)
            except Exception:
                pass
        return [ev]

    def _emit_group(self, frame, group, direction, cat):
        """A group of people / two-wheelers -> a single alert with the count (all boxes on the photo)."""
        lead = group[-1]
        img = frame.copy()
        color = (0, 200, 0) if direction == "Entry" else (0, 140, 255)
        for cr in group[:-1]:
            x1, y1, x2, y2 = cr["bbox"]
            cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        image_path = self._save_snapshot(img, lead, direction)
        count = len(group)
        label = "person" if cat == "person" else lead["label"]
        gate = config.gate_name(self.cfg)
        eid = self.db.add_event(label, direction, "", image_path, count=count, gate=gate)
        ev = {
            "id": eid,
            "vehicle_type": label,
            "category": cat,
            "count": count,
            "bbox": [int(v) for v in lead["bbox"]],
            "gate": gate,
            "direction": direction,
            "plate": "",
            "plate_status": "",
            "plate_source": "",
            "image_path": image_path,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        if self.on_event:
            try:
                self.on_event(ev)
            except Exception:
                pass
        try:
            self.notifier.notify(ev)
        except Exception:
            pass
        return [ev]

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

    def _annotate(self, frame, a, b, a2=None, b2=None):
        img = frame
        for zx1, zy1, zx2, zy2 in self._zones_px(img.shape[1], img.shape[0]):
            ov = img[zy1:zy2, zx1:zx2]
            if ov.size:
                ov[:] = cv2.addWeighted(ov, 0.7, np.full_like(ov, (60, 60, 220)), 0.3, 0)
            cv2.rectangle(img, (zx1, zy1), (zx2, zy2), (80, 80, 230), 2)
            cv2.putText(img, "IGNORE", (zx1 + 6, zy1 + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (80, 80, 230), 1, cv2.LINE_AA)
        cv2.line(img, a, b, (0, 255, 255), 2)
        if self.has_line2() and a2 is not None:
            draw_line2(img, a2, b2)
        for _key, tr in self._live_tracks():
            x1, y1, x2, y2 = tr.bbox
            animal = category_of(tr.label) == "animal"
            col = (160, 160, 160) if (tr.counted or animal) else (0, 200, 0)
            cv2.rectangle(img, (x1, y1), (x2, y2), col, 2)
            cv2.putText(
                img, tr.label + (" (counted)" if tr.counted else " (animal, ignored)" if animal else ""),
                (x1, max(12, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1,
            )
            cv2.circle(img, CentroidTracker.ref_point(tr.bbox), 5, (0, 0, 255), -1)
        n = len(self.tracker.tracks) + len(self.tracker2.tracks)
        hud = f"AI: {n} vehicle{'s' if n != 1 else ''} tracked"
        if self.last_detect_ms is not None:
            hud += f"  |  {self.last_detect_ms:.0f} ms"
        hud += f"  |  {getattr(self.detector, 'model_name', '')}"
        cv2.putText(img, hud, (10, img.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (0, 0, 0), 3)
        cv2.putText(img, hud, (10, img.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (0, 255, 0) if n else (200, 200, 200), 1)
        return img
