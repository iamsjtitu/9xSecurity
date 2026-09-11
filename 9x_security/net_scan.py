"""LAN camera scan so the user never types an IP: (1) ONVIF WS-Discovery multicast probe
(name/hardware/XAddr, no password needed), (2) TCP sweep of the local /24 subnets for the
RTSP port, (3) RTSP fingerprint (Server header / auth realm) -> brand guess for the URL builder."""
import re
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote, urlparse

from rtsp_discover import _rtsp_request

WS_ADDR = ("239.255.255.250", 3702)
_PROBE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
    'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
    'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
    'xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
    '<e:Header><w:MessageID>uuid:{mid}</w:MessageID>'
    '<w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>'
    '<w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2004/08/discovery/Probe</w:Action></e:Header>'
    '<e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body></e:Envelope>'
)

# (needle in lower-cased 'server | realm | onvif name | hardware') -> BRANDS id
_BRAND_HINTS = [
    ("hikvision", "hikvision"), ("ds-2", "hikvision"), ("ip camera", "hikvision"), ("prama", "hikvision"),
    ("hiwatch", "hikvision"), ("honeywell", "hikvision"),
    ("dahua", "dahua"), ("login to ", "dahua"), ("ipc-h", "dahua"), ("dh-", "dahua"), ("cp plus", "dahua"),
    ("cp-", "dahua"), ("imou", "dahua"), ("amcrest", "dahua"),
    ("tp-link", "tplink"), ("tplink", "tplink"), ("tapo", "tplink"), ("vigi", "tplink"),
    ("reolink", "reolink"), ("rlc-", "reolink"),
    ("uniview", "uniview"), ("unv", "uniview"), ("ipc2", "uniview"),
    ("axis", "axis"), ("vivotek", "vivotek"), ("panasonic", "panasonic"),
    ("hipcam", "hipcam"), ("h264dvr", "xmeye"), ("xmeye", "xmeye"), ("xiongmai", "xmeye"),
]


def guess_brand(*texts):
    blob = " | ".join(t for t in texts if t).lower()
    for needle, brand in _BRAND_HINTS:
        if needle in blob:
            return brand
    return ""


def local_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    return sorted(ip for ip in ips if not ip.startswith(("127.", "169.254.")))


def local_subnets(extra_hosts=()):
    """/24 prefixes ('192.168.1') of this PC's interfaces + any extra hosts (e.g. the saved camera IP)."""
    hosts = list(local_ips()) + [h for h in extra_hosts if h and re.match(r"^\d+\.\d+\.\d+\.\d+$", h)]
    return sorted({".".join(h.split(".")[:3]) for h in hosts if not h.startswith(("127.", "169.254."))})


def parse_probe_match(xml, from_ip=""):
    """-> {ip, xaddr, name, hardware} from a WS-Discovery ProbeMatch (or None)."""
    m = re.search(r"<[^>]*XAddrs>\s*([^<]+)<", xml)
    if not m:
        return None
    xaddr = m.group(1).split()[0]
    ip = urlparse(xaddr).hostname or from_ip
    scopes = re.search(r"<[^>]*Scopes>\s*([^<]+)<", xml)
    name = hardware = ""
    for sc in (scopes.group(1).split() if scopes else []):
        low = sc.lower()
        if "/name/" in low:
            name = unquote(sc.split("/name/", 1)[1]).replace("_", " ")
        elif "/hardware/" in low:
            hardware = unquote(sc.split("/hardware/", 1)[1])
    return {"ip": ip, "xaddr": xaddr, "name": name, "hardware": hardware}


def ws_discovery(timeout=2.5):
    """ONVIF WS-Discovery on every local interface. -> {ip: {xaddr, name, hardware}}."""
    found = {}
    ifaces = local_ips() or ["0.0.0.0"]
    socks = []
    for ip in ifaces:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            if ip != "0.0.0.0":
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
            s.bind((ip, 0))
            s.settimeout(0.3)
            s.sendto(_PROBE.format(mid=uuid.uuid4()).encode(), WS_ADDR)
            socks.append(s)
        except Exception:
            continue
    end = time.time() + timeout
    while time.time() < end and socks:
        for s in socks:
            try:
                data, addr = s.recvfrom(65535)
            except socket.timeout:
                continue
            except Exception:
                continue
            info = parse_probe_match(data.decode("utf-8", "replace"), addr[0])
            if info:
                found.setdefault(info["ip"], info)
    for s in socks:
        s.close()
    return found


def _port_open(ip, port, timeout):
    try:
        socket.create_connection((ip, port), timeout=timeout).close()
        return True
    except Exception:
        return False


def port_sweep(subnets, port=554, timeout=0.6, workers=128):
    hosts = [f"{sn}.{i}" for sn in subnets for i in range(1, 255)]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        hits = ex.map(lambda h: h if _port_open(h, port, timeout) else "", hosts)
    return [h for h in hits if h]


def rtsp_fingerprint(ip, port=554, timeout=2.0):
    """Unauthenticated DESCRIBE on '/': -> {status, server, realm}. 401 => camera wants a password (normal)."""
    try:
        code, text = _rtsp_request(ip, port, f"rtsp://{ip}:{port}/", timeout=timeout)
    except Exception as e:
        return {"status": 0, "server": "", "realm": "", "error": str(e)}
    server = re.search(r"^Server:\s*([^\r\n]+)", text, re.I | re.M)
    realm = re.search(r'realm="([^"]*)"', text, re.I)
    return {"status": code, "server": server.group(1).strip() if server else "", "realm": realm.group(1) if realm else ""}


def scan_network(subnets=None, port=554, extra_hosts=(), ws_timeout=2.5, sweep_timeout=0.6, log=lambda m: None):
    """-> {subnets, cameras: [{ip, port, onvif, name, hardware, brand, auth_needed, server, realm}], seconds}"""
    t0 = time.time()
    subnets = list(subnets) if subnets else local_subnets(extra_hosts)
    log(f"scan: subnets={subnets} port={port}")
    onvif = ws_discovery(ws_timeout) if ws_timeout else {}
    log(f"scan: onvif devices={len(onvif)}")
    open_hosts = set(port_sweep(subnets, port, sweep_timeout)) if subnets else set()
    log(f"scan: rtsp port open on {len(open_hosts)} host(s)")
    ips = sorted(open_hosts | set(onvif), key=lambda s: [int(x) for x in s.split(".")] if re.match(r"^\d+(\.\d+){3}$", s) else [0])
    cams = []
    for ip in ips:
        fp = rtsp_fingerprint(ip, port) if ip in open_hosts else {"status": 0, "server": "", "realm": ""}
        ov = onvif.get(ip, {})
        cams.append({
            "ip": ip, "port": port, "rtsp_open": ip in open_hosts, "onvif": bool(ov),
            "name": ov.get("name", ""), "hardware": ov.get("hardware", ""),
            "brand": guess_brand(fp.get("server"), fp.get("realm"), ov.get("name"), ov.get("hardware")) or ("auto" if ov else ""),
            "auth_needed": fp.get("status") == 401, "server": fp.get("server", ""), "realm": fp.get("realm", ""),
        })
    return {"subnets": subnets, "cameras": cams, "seconds": round(time.time() - t0, 1)}
