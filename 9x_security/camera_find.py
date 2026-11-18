"""Camera Auto Find: a camera's host stops answering (DHCP handed it a new IP, camera rebooted on another lease)
-> find the SAME camera on the LAN and give back its URL with the new IP. Identity, strongest first:
  1. MAC address (OS neighbour cache after a sweep touched every host)
  2. ONVIF endpoint uuid (WS-Discovery) / device serial (GetDeviceInformation with our credentials)
  3. our user/password + stream path answer RTSP 200 on exactly ONE new host (tie-break: RTSP server/realm)
A fingerprint {mac, uuid, serial, server, realm, ...} is collected while the camera works and saved per camera."""
import re
import subprocess
import sys
import time

from net_scan import local_subnets, port_sweep, rtsp_fingerprint, ws_discovery
from rtsp_discover import rtsp_describe, split_url, with_creds

_MAC_RE = re.compile(r"(\d+\.\d+\.\d+\.\d+)[^\n]*?([0-9a-f]{2}(?:[-:][0-9a-f]{2}){5})", re.I)
_BAD_MACS = ("ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00")


def arp_table():
    """{ip: 'aa:bb:cc:dd:ee:ff'} from the OS neighbour cache (Windows `arp -a`, Linux `ip neigh`)."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=10, creationflags=0x08000000).stdout
        else:
            out = subprocess.run(["ip", "neigh"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return {}
    table = {}
    for ip, mac in _MAC_RE.findall(out or ""):
        mac = mac.lower().replace("-", ":")
        if mac not in _BAD_MACS and not ip.startswith(("224.", "239.", "255.")):
            table[ip] = mac
    return table


def _tag(xml, name):
    m = re.search(rf"<[^>]*\b{name}>\s*([^<]*)<", xml or "")
    return m.group(1).strip() if m else ""


def onvif_device_info(host, user, pw, timeout=4):
    """{manufacturer, model, serial} via ONVIF GetDeviceInformation (needs the camera's credentials), or {}."""
    import ptz

    body = '<GetDeviceInformation xmlns="http://www.onvif.org/ver10/device/wsdl"/>'
    for port in ptz._PORTS:
        try:
            r = ptz._soap(f"http://{host}:{port}/onvif/device_service", user, pw, body, timeout=timeout)
        except Exception:
            continue
        if r.status_code != 200:
            continue
        info = {"manufacturer": _tag(r.text, "Manufacturer"), "model": _tag(r.text, "Model"), "serial": _tag(r.text, "SerialNumber")}
        if any(info.values()):
            return info
    return {}


def _same_lan(host):
    return ".".join(host.split(".")[:3]) in local_subnets()


def fingerprint(url, log=lambda m: None):
    """Identity of the camera behind `url` while it IS reachable -> {ip, port, at, mac, server, realm, serial, model,
    manufacturer, uuid, name, hardware} (only the keys we could learn). Saved into the camera's config."""
    host, port, user, pw, _path = split_url(url)
    fp = {"ip": host, "port": port, "at": int(time.time())}
    if _same_lan(host):  # on a routed path the neighbour entry would be the gateway's MAC
        mac = arp_table().get(host, "")
        if mac:
            fp["mac"] = mac
    rf = rtsp_fingerprint(host, port)
    for k in ("server", "realm"):
        if rf.get(k):
            fp[k] = rf[k]
    try:
        fp.update({k: v for k, v in onvif_device_info(host, user, pw).items() if v})
    except Exception as e:
        log(f"fingerprint: onvif info error {e}")
    try:
        ov = ws_discovery(2.0).get(host) or {}
        for k in ("uuid", "name", "hardware"):
            if ov.get(k):
                fp[k] = ov[k]
    except Exception as e:
        log(f"fingerprint: ws-discovery error {e}")
    learned = [k for k in ("mac", "serial", "uuid", "server", "realm") if fp.get(k)]
    log("fingerprint: " + (", ".join(learned) if learned else "kuch nahi mila (no MAC/ONVIF/RTSP server info)"))
    return fp


def relocate(url, fp=None, exclude_ips=(), log=lambda m: None, sweep_timeout=0.6):
    """Find the camera from `url` at its NEW IP. -> (new_url, how) or ('', reason).
    how: 'mac' | 'onvif-uuid' | 'serial' | 'creds' | 'creds+server'; reason: 'not-found' | 'ambiguous:<ips>'."""
    fp = dict(fp or {})
    host, port, user, pw, _path = split_url(url)
    exclude = {host, *[ip for ip in exclude_ips if ip]}
    subnets = local_subnets([host])
    log(f"autofind: camera {host}:{port} jawab nahi de raha — scanning {subnets}")
    hosts = [h for h in port_sweep(subnets, port, sweep_timeout) if h not in exclude]
    try:
        onvif = ws_discovery(2.5)
    except Exception:
        onvif = {}
    log(f"autofind: port {port} open on {len(hosts)} other host(s), onvif devices {len(onvif)}")

    def at(ip):
        return with_creds(url, user, pw, host=ip, port=port)

    codes = {}

    def answers(ip):  # RTSP status of OUR path+creds on that host (cached)
        if ip not in codes:
            codes[ip] = rtsp_describe(at(ip), timeout=3.0)[0]
        return codes[ip]

    if fp.get("mac"):  # the sweep just touched every host: neighbour cache is fresh
        for ip, mac in arp_table().items():
            if mac == fp["mac"] and ip not in exclude and (ip in hosts or answers(ip) != 0):
                log(f"autofind: MAC {mac} ab {ip} par hai")
                return at(ip), "mac"
    if fp.get("uuid"):
        for ip, ov in onvif.items():
            if ip not in exclude and ov.get("uuid") == fp["uuid"]:
                log(f"autofind: ONVIF device {fp['uuid']} ab {ip} par hai")
                return at(ip), "onvif-uuid"
    if fp.get("serial"):
        for ip in hosts:
            try:
                if onvif_device_info(ip, user, pw).get("serial") == fp["serial"]:
                    log(f"autofind: serial {fp['serial']} ab {ip} par hai")
                    return at(ip), "serial"
            except Exception:
                continue
    ok = [ip for ip in hosts if answers(ip) == 200]
    if len(ok) == 1:
        log(f"autofind: hamara user/password + path sirf {ok[0]} par chala")
        return at(ok[0]), "creds"
    if len(ok) > 1:
        same = [ip for ip in ok if fp.get("server") and rtsp_fingerprint(ip, port).get("server") == fp.get("server")
                and rtsp_fingerprint(ip, port).get("realm", "") == fp.get("realm", "")]
        if len(same) == 1:
            log(f"autofind: {len(ok)} cameras password maante hain; RTSP server/realm sirf {same[0]} se match")
            return at(same[0]), "creds+server"
        log(f"autofind: {len(ok)} cameras ({', '.join(ok)}) yahi password maante hain — khud chunna hoga")
        return "", "ambiguous:" + ",".join(ok)
    log("autofind: camera LAN par nahi mila")
    return "", "not-found"
