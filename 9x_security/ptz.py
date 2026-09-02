"""Minimal ONVIF PTZ client (optical zoom) using hand-rolled SOAP over requests.

No zeep/wsdl deps (PyInstaller friendly). Credentials/host are taken from the
configured RTSP URL. Discovery result is cached per camera.
"""
import base64
import hashlib
import os
import re
import threading
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse
from xml.sax.saxutils import escape

import requests

_PORTS = (2020, 80, 8000, 8899, 5000)
_PATHS = ("media_service", "device_service", "Media")
_cache = {}
_lock = threading.Lock()


def creds_from_rtsp(url):
    from engine import normalize_rtsp_url

    u = urlparse(normalize_rtsp_url(url or ""))
    return u.hostname, unquote(u.username or ""), unquote(u.password or "")


def _security_header(user, pw):
    nonce = os.urandom(16)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + pw.encode()).digest()).decode()
    return (
        '<wsse:Security s:mustUnderstand="1" '
        'xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" '
        'xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
        f"<wsse:UsernameToken><wsse:Username>{escape(user)}</wsse:Username>"
        '<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
        f"{digest}</wsse:Password>"
        '<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
        f"{base64.b64encode(nonce).decode()}</wsse:Nonce>"
        f"<wsu:Created>{created}</wsu:Created></wsse:UsernameToken></wsse:Security>"
    )


def _soap(url, user, pw, body, timeout=4):
    envelope = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
        f"<s:Header>{_security_header(user, pw)}</s:Header>"
        f"<s:Body>{body}</s:Body></s:Envelope>"
    )
    return requests.post(
        url,
        data=envelope.encode(),
        headers={"Content-Type": "application/soap+xml; charset=utf-8"},
        timeout=timeout,
    )


def _discover(host, user, pw):
    """Find (service_base_url, profile_token) via GetProfiles. Cached per camera."""
    key = (host, user, pw)
    with _lock:
        if key in _cache:
            return _cache[key]
    found = None
    get_profiles = '<GetProfiles xmlns="http://www.onvif.org/ver10/media/wsdl"/>'
    for port in _PORTS:
        for path in _PATHS:
            svc = f"http://{host}:{port}/onvif/{path}"
            try:
                r = _soap(svc, user, pw, get_profiles, timeout=3)
            except Exception:
                break  # port unreachable: skip remaining paths on it
            m = re.search(r'Profiles[^>]*token="([^"]+)"', r.text)
            if m:
                found = (f"http://{host}:{port}/onvif", m.group(1))
                break
        if found:
            break
    with _lock:
        _cache[key] = found
    return found


def reset_cache():
    with _lock:
        _cache.clear()


def zoom(rtsp_url, direction="in", action="start"):
    """Start/stop continuous optical zoom. Returns (ok, supported, detail)."""
    host, user, pw = creds_from_rtsp(rtsp_url)
    if not host:
        return False, False, "Camera URL me host nahi mila — pehle RTSP URL set karein"
    disc = _discover(host, user, pw)
    if not disc:
        return False, False, (
            "Camera ONVIF/PTZ support nahi karta (ya ONVIF band hai) — Digital Zoom use karein"
        )
    base, token = disc
    if action == "stop":
        body = (
            '<Stop xmlns="http://www.onvif.org/ver20/ptz/wsdl">'
            f"<ProfileToken>{token}</ProfileToken><PanTilt>false</PanTilt><Zoom>true</Zoom></Stop>"
        )
    else:
        vx = "0.5" if direction == "in" else "-0.5"
        body = (
            '<ContinuousMove xmlns="http://www.onvif.org/ver20/ptz/wsdl">'
            f"<ProfileToken>{token}</ProfileToken>"
            f'<Velocity><Zoom x="{vx}" xmlns="http://www.onvif.org/ver10/schema"/></Velocity>'
            "</ContinuousMove>"
        )
    last = None
    for path in ("ptz_service", "PTZ", "media_service", "device_service"):
        try:
            r = _soap(f"{base}/{path}", user, pw, body, timeout=4)
        except Exception as e:
            last = str(e)
            continue
        if r.ok and "Fault" not in r.text:
            return True, True, "ok"
        last = f"HTTP {r.status_code}"
        if "NoPTZProfile" in r.text or "ActionNotSupported" in r.text or "NotSupported" in r.text:
            return False, False, "Camera me optical PTZ zoom nahi hai — Digital Zoom use karein"
    return False, True, f"PTZ request fail ({last})"
