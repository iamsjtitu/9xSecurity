"""Find the real RTSP stream path when the user only knows rtsp://user:pw@host:554/
(camera answers DESCRIBE 404) — what NVR / 'entry system' software does silently.

1. rtsp_describe(url): raw RTSP DESCRIBE (Basic/Digest auth) -> status code. 200 = path OK,
   401 = wrong credentials, 404 = path missing. Milliseconds, no decoder involved.
2. onvif_stream_uris(host, user, pw): GetProfiles + GetStreamUri via ONVIF (ptz._soap).
3. COMMON_PATHS: vendor defaults, each verified with rtsp_describe.
"""
import base64
import hashlib
import re
import socket
import time
from urllib.parse import quote, unquote, urlparse, urlunparse

# Brand picker: {ch} = channel number (NVR/DVR), {user}/{pw} = credentials (XMEye style)
BRANDS = [
    {"id": "hikvision", "name": "Hikvision / Prama / HiWatch / Honeywell", "port": 554,
     "main": "/Streaming/Channels/{ch}01", "sub": "/Streaming/Channels/{ch}02",
     "note": "NVR par channel number = camera ka number (1, 2, 3…)"},
    {"id": "dahua", "name": "Dahua / CP Plus / Imou / Amcrest", "port": 554,
     "main": "/cam/realmonitor?channel={ch}&subtype=0", "sub": "/cam/realmonitor?channel={ch}&subtype=1",
     "note": "CP Plus DVR/NVR par bhi yahi path chalta hai"},
    {"id": "tplink", "name": "TP-Link Tapo / VIGI", "port": 554, "main": "/stream1", "sub": "/stream2",
     "note": "Tapo app me 'Camera Account' banana padta hai (Settings > Advanced)"},
    {"id": "reolink", "name": "Reolink", "port": 554, "main": "/h264Preview_01_main", "sub": "/h264Preview_01_sub",
     "note": ""},
    {"id": "uniview", "name": "Uniview (UNV)", "port": 554, "main": "/media/video1", "sub": "/media/video2", "note": ""},
    {"id": "xmeye", "name": "Godrej / Zicom / XMEye DVR (generic Chinese)", "port": 554,
     "main": "/user={user}_password={pw}_channel={ch}_stream=0.sdp?real_stream",
     "sub": "/user={user}_password={pw}_channel={ch}_stream=1.sdp?real_stream",
     "note": "Password URL me do baar jata hai — ye normal hai"},
    {"id": "generic", "name": "Zebronics / Generic OEM (live/ch00_0)", "port": 554, "main": "/live/ch00_0",
     "sub": "/live/ch00_1", "note": ""},
    {"id": "hipcam", "name": "Hipcam / Wansview / cheap WiFi cam (/11)", "port": 554, "main": "/11", "sub": "/12", "note": ""},
    {"id": "axis", "name": "Axis", "port": 554, "main": "/axis-media/media.amp",
     "sub": "/axis-media/media.amp?resolution=640x480", "note": ""},
    {"id": "vivotek", "name": "Vivotek", "port": 554, "main": "/live.sdp", "sub": "/live2.sdp", "note": ""},
    {"id": "panasonic", "name": "Panasonic", "port": 554, "main": "/MediaInput/h264", "sub": "/MediaInput/h264/stream_2",
     "note": ""},
    {"id": "auto", "name": "Pata nahi / Auto-detect (ONVIF)", "port": 554, "main": "/", "sub": "/",
     "note": "Test dabane par app camera se asli path khud pooch lega"},
    {"id": "custom", "name": "Custom path (khud likhein)", "port": 554, "main": "/", "sub": "/",
     "note": "Camera ke manual/web page me diya RTSP path yahan likhein"},
]


def build_url(brand_id, ip, user, pw, port=None, channel=1, stream="main", custom_path=""):
    """rtsp URL from a brand template. Password is percent-encoded so '@' etc. are safe."""
    b = next((x for x in BRANDS if x["id"] == brand_id), None) or BRANDS[-2]
    if brand_id == "custom":
        path = custom_path.strip()
    else:
        path = b[stream if stream in ("main", "sub") else "main"]
        path = path.format(ch=int(channel or 1), user=quote(user, safe=""), pw=quote(pw, safe=""))
    if not path.startswith("/"):
        path = "/" + path
    prt = int(port or b.get("port") or 554)
    cred = f"{quote(user, safe='')}:{quote(pw, safe='')}@" if user else ""
    return f"rtsp://{cred}{ip.strip()}:{prt}{path}"


COMMON_PATHS = [
    "/Streaming/Channels/101",                    # Hikvision, Prama, many OEM NVR/cams
    "/cam/realmonitor?channel=1&subtype=0",       # Dahua, CP Plus
    "/stream1",                                   # TP-Link Tapo / VIGI
    "/h264Preview_01_main",                       # Reolink
    "/media/video1",                              # Uniview
    "/live/ch00_0",                               # generic Chinese OEM (Zebronics etc.)
    "/h264/ch1/main/av_stream",                   # legacy Hikvision
    "/onvif1",                                    # cheap wifi cams
    "/live", "/live/main", "/video1", "/videoMain", "/11", "/ch0_0.h264", "/live.sdp",
    "/axis-media/media.amp", "/MediaInput/h264", "/1", "/0", "/ch01.264", "/av0_0", "/profile1",
    "/user={user}_password={pw}_channel=1_stream=0.sdp?real_stream",  # XMEye (Godrej/Zicom clones)
]


def split_url(url):
    """-> (host, port, user, pw, path_with_query) from a normalized rtsp URL."""
    u = urlparse(url)
    path = u.path or ""
    if u.query:
        path += "?" + u.query
    return u.hostname or "", u.port or 554, unquote(u.username or ""), unquote(u.password or ""), path


def with_path(url, path):
    """Same host/creds, different stream path (path may include ?query)."""
    u = urlparse(url)
    p, _, q = path.partition("?")
    return urlunparse((u.scheme, u.netloc, p, "", q, ""))


def with_creds(uri, user, pw, host=None, port=None):
    """Inject user/pw (percent-encoded) into a bare rtsp URI returned by ONVIF; optionally force host/port."""
    u = urlparse(uri)
    h = host or u.hostname or ""
    prt = port or u.port or 554
    netloc = f"{h}:{prt}"
    if user:
        netloc = f"{quote(user, safe='')}:{quote(pw, safe='')}@{netloc}"
    return urlunparse(("rtsp", netloc, u.path, "", u.query, ""))


def has_stream_path(url):
    _h, _p, _u, _pw, path = split_url(url)
    return path not in ("", "/")


def _digest(user, pw, realm, nonce, method, uri, qop=None, algorithm="", cnonce="9x9x9x9x", nc="00000001"):
    ha1 = hashlib.md5(f"{user}:{realm}:{pw}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
    alg = f', algorithm={algorithm}' if algorithm else ""
    if qop:
        resp = hashlib.md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}".encode()).hexdigest()
        return (f'Digest username="{user}", realm="{realm}", nonce="{nonce}", uri="{uri}", '
                f'response="{resp}", qop={qop}, nc={nc}, cnonce="{cnonce}"{alg}')
    resp = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
    return f'Digest username="{user}", realm="{realm}", nonce="{nonce}", uri="{uri}", response="{resp}"{alg}'


def _rtsp_exchange(sock, uri, auth=None, cseq=1):
    """One DESCRIBE round-trip on an already open socket. -> (status_code, headers_text)."""
    req = (f"DESCRIBE {uri} RTSP/1.0\r\nCSeq: {cseq}\r\nUser-Agent: 9xSecurity\r\n"
           f"Accept: application/sdp\r\n" + (f"Authorization: {auth}\r\n" if auth else "") + "\r\n")
    sock.sendall(req.encode())
    data = b""
    while b"\r\n\r\n" not in data and len(data) < 65536:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    text = data.decode("latin-1", "replace")
    m = re.match(r"RTSP/1\.\d\s+(\d{3})", text)
    return (int(m.group(1)) if m else 0), text


def _rtsp_request(host, port, uri, auth=None, cseq=1, timeout=3.0):
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        return _rtsp_exchange(s, uri, auth, cseq)


def rtsp_describe(url, timeout=3.0):
    """-> (status_code, detail). 200 = stream path OK, 401 = bad user/password,
    404/400/… = no such path, 0 = no/invalid RTSP answer (socket error text in detail).
    The challenge and the authenticated retry share ONE connection (cameras/servers bind the
    nonce to the connection — a second socket gets 401 even with the right password)."""
    host, port, user, pw, path = split_url(url)
    uri = f"rtsp://{host}:{port}{path or '/'}"
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            code, text = _rtsp_exchange(s, uri)
            if code == 401 and user:
                m = re.search(r'WWW-Authenticate:\s*Digest\s+([^\r\n]+)', text, re.I)
                if m:
                    params = dict(re.findall(r'(\w+)="?([^",]*)"?', m.group(1)))
                    qop = "auth" if "auth" in params.get("qop", "") else None
                    auth = _digest(user, pw, params.get("realm", ""), params.get("nonce", ""), "DESCRIBE", uri, qop,
                                   params.get("algorithm", ""))
                else:
                    auth = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
                try:
                    code, text = _rtsp_exchange(s, uri, auth=auth, cseq=2)
                except (ConnectionError, socket.timeout, OSError):
                    code = 0
                if code == 0:  # server closed the connection after the 401 -> retry on a fresh one
                    code, text = _rtsp_request(host, port, uri, auth=auth, cseq=2, timeout=timeout)
        return code, text.splitlines()[0] if text else ""
    except Exception as e:
        return 0, str(e)


def onvif_stream_uris(host, user, pw, timeout=4):
    """RTSP URIs of the camera's media profiles (main first), or [] when ONVIF is unavailable."""
    import ptz

    profiles = '<GetProfiles xmlns="http://www.onvif.org/ver10/media/wsdl"/>'
    out = []
    for port in ptz._PORTS:
        for path in ptz._PATHS:
            svc = f"http://{host}:{port}/onvif/{path}"
            try:
                r = ptz._soap(svc, user, pw, profiles, timeout=timeout)
            except Exception:
                break  # port unreachable: skip remaining paths on it
            tokens = re.findall(r'Profiles[^>]*token="([^"]+)"', r.text)
            if not tokens:
                continue
            for tok in tokens[:2]:
                body = ('<GetStreamUri xmlns="http://www.onvif.org/ver10/media/wsdl">'
                        '<StreamSetup><Stream xmlns="http://www.onvif.org/ver10/schema">RTP-Unicast</Stream>'
                        '<Transport xmlns="http://www.onvif.org/ver10/schema"><Protocol>RTSP</Protocol></Transport>'
                        f'</StreamSetup><ProfileToken>{tok}</ProfileToken></GetStreamUri>')
                try:
                    r2 = ptz._soap(svc, user, pw, body, timeout=timeout)
                except Exception:
                    continue
                m = re.search(r"<[^>]*\bUri>\s*(rtsp://[^<\s]+)", r2.text)
                if m:
                    out.append(m.group(1).replace("&amp;", "&"))
            if out:
                return out
    return out


def discover_stream_url(url, log=lambda m: None, budget_s=25.0):
    """Try to turn a path-less/404 RTSP URL into a working one.
    -> (found_url or '', reason). reason: 'onvif' | 'common-path' | 'auth' (bad password) | 'none'."""
    t0 = time.time()
    host, port, user, pw, _path = split_url(url)
    # 1) ONVIF: the camera tells us its own stream URI (most reliable)
    try:
        uris = onvif_stream_uris(host, user, pw)
    except Exception as e:
        uris = []
        log(f"discover: onvif error {e}")
    for uri in uris:
        cand = with_creds(uri, user, pw, host=host, port=urlparse(uri).port or port)
        code, _ = rtsp_describe(cand)
        log(f"discover: onvif uri {urlparse(uri).path} -> {code}")
        if code == 200:
            return cand, "onvif"
        if code == 401:
            return cand, "onvif-unverified"  # camera named this URI itself; only our auth check failed
    # 2) common vendor paths, verified with a real DESCRIBE. 'auth' only when EVERY answer was 401
    codes = []
    for p in COMMON_PATHS:
        if time.time() - t0 > budget_s:
            log("discover: time budget over")
            break
        p = p.format(user=user, pw=pw)
        cand = with_path(url, p)
        code, _ = rtsp_describe(cand, timeout=2.5)
        log(f"discover: try {p} -> {code}")
        if code == 200:
            return cand, "common-path"
        if code == 0:
            break  # camera stopped answering: do not hammer it
        codes.append(code)
    if codes and all(c == 401 for c in codes):
        return "", "auth"
    return "", "none"
