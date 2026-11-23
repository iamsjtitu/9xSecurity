"""HTTP/HTTPS video for cameras whose RTSP is unavailable (RTSP disabled, port blocked, IP-locked RTSP service):
  * JPEG snapshot polling — ONVIF GetSnapshotUri (almost every camera) or brand CGI; 2–5 fps, full resolution
  * MJPEG stream — multipart/x-mixed-replace from brand CGI (Dahua/CP Plus, Hikvision httpPreview, generic)
Sources mimic cv2.VideoCapture (read / isOpened / release / get) so the worker loop + LatestFrameReader are unchanged.
Digest and Basic auth, self-signed HTTPS accepted (cameras never have real certificates)."""
import re
import threading
import time
from urllib.parse import quote, unquote, urlparse, urlunparse

import cv2
import numpy as np
import requests
import urllib3
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# brand -> paths. CP Plus = Dahua OEM; Prama = Hikvision OEM; TP-Link VIGI relies on ONVIF snapshot.
MJPEG_PATHS = (
    ("cpplus/dahua", "/cgi-bin/mjpg/video.cgi?channel=1&subtype=1"),
    ("hikvision/prama", "/ISAPI/Streaming/channels/102/httpPreview"),
    ("generic", "/video.mjpg"), ("generic", "/mjpeg"), ("generic", "/videostream.cgi"),
    ("axis", "/axis-cgi/mjpg/video.cgi"),
)
SNAPSHOT_PATHS = (
    ("cpplus/dahua", "/cgi-bin/snapshot.cgi?channel=1"),
    ("hikvision/prama", "/ISAPI/Streaming/channels/101/picture"),
    ("onvif", "/onvif-http/snapshot?Profile_1"),
    ("generic", "/snapshot.jpg"), ("generic", "/jpg/image.jpg"), ("generic", "/image/jpeg.cgi"),
    ("generic", "/tmpfs/auto.jpg"), ("generic", "/cgi-bin/jpg/image.cgi"), ("axis", "/axis-cgi/jpg/image.cgi"),
)
UA = {"User-Agent": "9xSecurity"}


def is_http(url):
    return str(url or "").lower().startswith(("http://", "https://"))


def split_http(url):
    """-> (scheme, host, port, user, pw, path_with_query)."""
    u = urlparse(url)
    port = u.port or (443 if u.scheme.lower() == "https" else 80)
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    return u.scheme.lower(), u.hostname or "", port, unquote(u.username or ""), unquote(u.password or ""), path


def bare(url):
    """URL without user:pw@ (credentials go in the auth header)."""
    u = urlparse(url)
    netloc = u.hostname or ""
    if u.port:
        netloc += f":{u.port}"
    return urlunparse((u.scheme, netloc, u.path, u.params, u.query, u.fragment))


def with_http_creds(url, user, pw, host=None):
    u = urlparse(url)
    h = host or u.hostname or ""
    netloc = f"{h}:{u.port}" if u.port else h
    if user:
        netloc = f"{quote(user, safe='')}:{quote(pw, safe='')}@{netloc}"
    return urlunparse((u.scheme, netloc, u.path, u.params, u.query, ""))


def _auths(user, pw):
    return (HTTPDigestAuth(user, pw), HTTPBasicAuth(user, pw)) if user else (None,)


def _get(url, timeout=4.0, stream=False, session=None):
    """GET with the credentials from the URL: Digest first, then Basic. -> response (401 if both failed)."""
    _s, _h, _p, user, pw, _path = split_http(url)
    target = bare(url)
    req = (session or requests).get
    last = None
    for auth in _auths(user, pw):
        r = req(target, auth=auth, timeout=timeout, stream=stream, verify=False, headers=UA)
        if r.status_code != 401:
            if session is not None and auth is not None:
                session.auth = auth
            return r
        last = r
        r.close()
    return last


def _decode(buf):
    arr = np.frombuffer(buf, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img if img is not None and img.size else None


def _mjpeg_frames(resp, max_wait=6.0):
    """Yield decoded frames from a multipart/x-mixed-replace response (JPEG SOI..EOI scanning)."""
    buf = b""
    t0 = time.time()
    for chunk in resp.iter_content(chunk_size=16384):
        if not chunk:
            continue
        buf += chunk
        while True:
            a = buf.find(b"\xff\xd8")
            b = buf.find(b"\xff\xd9", a + 2) if a >= 0 else -1
            if a < 0 or b < 0:
                if a > 0:
                    buf = buf[a:]
                break
            frame = _decode(buf[a:b + 2])
            buf = buf[b + 2:]
            if frame is not None:
                t0 = time.time()
                yield frame
        if len(buf) > 8_000_000 or (time.time() - t0 > max_wait):
            return


def probe_http_url(url, timeout=5.0):
    """What does this URL serve? -> (kind, detail): kind 'snapshot' | 'mjpeg' | '' ; detail = HTTP status / content type."""
    try:
        r = _get(url, timeout=timeout, stream=True)
    except Exception as e:
        return "", f"{e.__class__.__name__}: {str(e)[:120]}"
    try:
        ct = (r.headers.get("Content-Type") or "").lower()
        if r.status_code != 200:
            return "", f"HTTP {r.status_code} {ct}".strip()
        if "multipart" in ct:
            for _frame in _mjpeg_frames(r, max_wait=timeout):
                return "mjpeg", ct
            return "", f"HTTP 200 {ct} — koi JPEG frame nahi aaya"
        if ct.startswith("image/") or ct == "application/octet-stream" or not ct:
            data = r.raw.read(6_000_000) if hasattr(r, "raw") else r.content
            if _decode(data) is not None:
                return "snapshot", ct or "image/jpeg"
            return "", f"HTTP 200 {ct} — JPEG decode nahi hua"
        return "", f"HTTP 200 {ct} — video/image nahi hai"
    finally:
        r.close()


class _HttpSource:
    kind = ""

    def __init__(self, url, max_fps=5.0):
        self.url = url
        self.max_fps = max_fps
        self._lock = threading.Lock()
        self._frame = None
        self._seq = 0
        self._served = 0
        self._alive = True
        self.last_ok = 0.0
        self.errors = 0
        self.fps = 0.0
        self._times = []
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _push(self, frame):
        now = time.time()
        with self._lock:
            self._frame = frame
            self._seq += 1
        self.last_ok = now
        self.errors = 0
        self._times = [t for t in self._times if now - t < 5.0] + [now]
        self.fps = round(len(self._times) / 5.0, 1)

    def read(self, timeout=3.0):
        t0 = time.time()
        while self._alive:
            with self._lock:
                if self._seq != self._served:
                    self._served = self._seq
                    return True, self._frame
            if time.time() - t0 > timeout:
                return False, None
            time.sleep(0.005)
        return False, None

    def isOpened(self):  # noqa: N802 (cv2 API)
        return self._alive and (self.last_ok > 0 and time.time() - self.last_ok < 30 or self.errors < 10)

    def get(self, prop):
        if prop == cv2.CAP_PROP_FPS:
            return self.fps
        if prop == cv2.CAP_PROP_FRAME_WIDTH and self._frame is not None:
            return self._frame.shape[1]
        if prop == cv2.CAP_PROP_FRAME_HEIGHT and self._frame is not None:
            return self._frame.shape[0]
        return 0

    def release(self):
        self._alive = False
        try:
            self._t.join(timeout=3)
        except Exception:
            pass

    def _loop(self):
        raise NotImplementedError


class SnapshotSource(_HttpSource):
    """Polls a JPEG URL as fast as the camera answers (capped at max_fps). Digest challenge is answered once per session."""
    kind = "snapshot"

    def _loop(self):
        session = requests.Session()
        min_dt = 1.0 / max(0.5, self.max_fps)
        while self._alive:
            t0 = time.time()
            try:
                r = _get(self.url, timeout=6.0, session=session)
                try:
                    frame = _decode(r.content) if r.status_code == 200 else None
                finally:
                    r.close()
                if frame is not None:
                    self._push(frame)
                else:
                    self.errors += 1
            except Exception:
                self.errors += 1
            if self.errors and self.errors % 5 == 0:
                time.sleep(2.0)  # camera busy / IP lock: do not hammer
            dt = time.time() - t0
            if dt < min_dt:
                time.sleep(min_dt - dt)
        session.close()


class MjpegSource(_HttpSource):
    """Reads a multipart/x-mixed-replace stream; reconnects when the camera drops it."""
    kind = "mjpeg"

    def _loop(self):
        while self._alive:
            try:
                r = _get(self.url, timeout=(5.0, 10.0), stream=True)
                if r.status_code != 200:
                    r.close()
                    self.errors += 1
                    time.sleep(2.0)
                    continue
                try:
                    for frame in _mjpeg_frames(r, max_wait=10.0):
                        if not self._alive:
                            break
                        self._push(frame)
                finally:
                    r.close()
            except Exception:
                self.errors += 1
                time.sleep(1.0)


def open_http(url, log=lambda m: None, first_frame_wait=8.0):
    """Open an http(s) URL as a video source (auto: MJPEG stream or JPEG polling). -> source or None."""
    kind, detail = probe_http_url(url)
    log(f"http: {kind or 'no video'} ({detail})")
    if not kind:
        return None
    src = MjpegSource(url) if kind == "mjpeg" else SnapshotSource(url)
    t0 = time.time()
    while time.time() - t0 < first_frame_wait:
        if src.last_ok:
            return src
        time.sleep(0.1)
    src.release()
    log("http: source opened but no frame arrived")
    return None


def onvif_snapshot_uris(host, user, pw, timeout=4):
    """Snapshot URIs of the camera's media profiles via ONVIF GetSnapshotUri (host forced to ours)."""
    import ptz

    profiles = '<GetProfiles xmlns="http://www.onvif.org/ver10/media/wsdl"/>'
    out = []
    for port in ptz._PORTS:
        for path in ptz._PATHS:
            svc = f"http://{host}:{port}/onvif/{path}"
            try:
                r = ptz._soap(svc, user, pw, profiles, timeout=timeout)
            except Exception:
                break
            tokens = re.findall(r'Profiles[^>]*token="([^"]+)"', r.text)
            if not tokens:
                continue
            for tok in tokens[:2]:
                body = (f'<GetSnapshotUri xmlns="http://www.onvif.org/ver10/media/wsdl"><ProfileToken>{tok}</ProfileToken>'
                        "</GetSnapshotUri>")
                try:
                    r2 = ptz._soap(svc, user, pw, body, timeout=timeout)
                except Exception:
                    continue
                m = re.search(r"<[^>]*\bUri>\s*(https?://[^<\s]+)", r2.text)
                if m:
                    out.append(with_http_creds(m.group(1).replace("&amp;", "&"), user, pw, host=host))
            if out:
                return out
    return out


def _port_open(host, port, timeout=1.5):
    import socket

    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


def discover_http(host, user, pw, log=lambda m: None, budget_s=40.0):
    """Find an HTTP video URL for the camera. -> (url_with_creds, kind, how) or ('', '', reason).
    Order: MJPEG CGI (10+ fps when the camera has an MJPEG sub-stream) -> ONVIF GetSnapshotUri -> snapshot CGI;
    http (80) and https (443) are both tried when open. reason: 'no-http-port' | 'not-found' | 'auth'."""
    t0 = time.time()
    schemes = [s for s, p in (("http", 80), ("https", 443)) if _port_open(host, p)]
    if not schemes:
        log("http-discover: port 80/443 band — camera HTTP nahi de raha")
        return "", "", "no-http-port"
    saw_401 = False

    def attempt(url, label):
        nonlocal saw_401
        kind, detail = probe_http_url(url, timeout=4.0)
        log(f"http-discover: {label} -> {kind or detail}")
        if detail.startswith("HTTP 401"):
            saw_401 = True
        return kind

    for scheme in schemes:
        for brand, path in MJPEG_PATHS:
            if time.time() - t0 > budget_s:
                break
            url = with_http_creds(f"{scheme}://{host}{path}", user, pw)
            if attempt(url, f"mjpeg {brand} {path.split('?')[0]}") == "mjpeg":
                return url, "mjpeg", f"cgi:{brand}"
    try:
        for uri in onvif_snapshot_uris(host, user, pw):
            if time.time() - t0 > budget_s:
                break
            if attempt(uri, f"onvif snapshot {urlparse(uri).path}") == "snapshot":
                return uri, "snapshot", "onvif"
    except Exception as e:
        log(f"http-discover: onvif error {e}")
    for scheme in schemes:
        for brand, path in SNAPSHOT_PATHS:
            if time.time() - t0 > budget_s:
                break
            url = with_http_creds(f"{scheme}://{host}{path}", user, pw)
            if attempt(url, f"snapshot {brand} {path.split('?')[0]}") == "snapshot":
                return url, "snapshot", f"cgi:{brand}"
    return "", "", ("auth" if saw_401 else "not-found")


def probe_steps(url, wait=8.0):
    """Test-button steps for an http(s) URL: reachable, what it serves, first frame. -> (ok, steps, url)."""
    steps = []
    scheme, host, port, _u, _p, _path = split_http(url)
    if not _port_open(host, port, timeout=3):
        steps.append((f"Camera network ({host}:{port})", False, "Camera tak pahunch nahi paa rahe (IP / port / power check)"))
        return False, steps, url
    steps.append((f"Camera network ({host}:{port})", True, "Camera network par mil gaya"))
    kind, detail = probe_http_url(url)
    if not kind:
        steps.append(("HTTP video", False, f"Is URL par video/image nahi mila ({detail}). Camera ka snapshot/MJPEG path check karein "
                                           "ya URL khaali chhod kar 'URL banao' se khud dhundhne dein"))
        return False, steps, url
    steps.append(("HTTP video", True, f"{'MJPEG stream' if kind == 'mjpeg' else 'JPEG snapshot (polling)'} mil gaya ({detail})"))
    src = open_http(url, first_frame_wait=wait)
    if src is None:
        steps.append(("Video frame", False, f"{wait:.0f} s me koi frame nahi aaya"))
        return False, steps, url
    try:
        t0 = time.time()
        n = 0
        while time.time() - t0 < 3.0:
            ok, f = src.read(timeout=1.0)
            n += 1 if ok else 0
        fps = n / 3.0
        h, w = (src._frame.shape[:2] if src._frame is not None else (0, 0))
        steps.append(("Video frame", True, f"{w}x{h}, ~{fps:.1f} frame/sec ({'MJPEG' if kind == 'mjpeg' else 'snapshot polling'})"))
    finally:
        src.release()
    return True, steps, url
