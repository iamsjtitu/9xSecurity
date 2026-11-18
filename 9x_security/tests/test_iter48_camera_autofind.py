"""Camera Auto Find: camera's DHCP IP changes -> software finds the same camera on the LAN and updates the URL."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camera_find  # noqa: E402
import config  # noqa: E402
import net_scan  # noqa: E402
import service  # noqa: E402

OLD = "rtsp://admin:Admin%40123@192.168.1.7:554/Streaming/Channels/101"

WIN_ARP = """
Interface: 192.168.1.5 --- 0x8
  Internet Address      Physical Address      Type
  192.168.1.1           c8-3a-35-11-22-33     dynamic
  192.168.1.23          a4-5e-60-aa-bb-cc     dynamic
  192.168.1.255         ff-ff-ff-ff-ff-ff     static
  224.0.0.22            01-00-5e-00-00-16     static
"""
LINUX_NEIGH = """192.168.1.1 dev eth0 lladdr c8:3a:35:11:22:33 REACHABLE
192.168.1.23 dev eth0 lladdr a4:5e:60:aa:bb:cc STALE
192.168.1.77 dev eth0  FAILED
"""


def test_arp_table_parses_windows_and_linux(monkeypatch):
    class R:
        def __init__(self, out):
            self.stdout = out

    monkeypatch.setattr(camera_find.sys, "platform", "win32")
    monkeypatch.setattr(camera_find.subprocess, "run", lambda *a, **k: R(WIN_ARP))
    assert camera_find.arp_table() == {"192.168.1.1": "c8:3a:35:11:22:33", "192.168.1.23": "a4:5e:60:aa:bb:cc"}
    monkeypatch.setattr(camera_find.sys, "platform", "linux")
    monkeypatch.setattr(camera_find.subprocess, "run", lambda *a, **k: R(LINUX_NEIGH))
    assert camera_find.arp_table() == {"192.168.1.1": "c8:3a:35:11:22:33", "192.168.1.23": "a4:5e:60:aa:bb:cc"}
    monkeypatch.setattr(camera_find.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no arp")))
    assert camera_find.arp_table() == {}


def test_probe_match_includes_endpoint_uuid():
    xml = ('<e:Envelope><e:Header><w:MessageID>uuid:1111</w:MessageID></e:Header><e:Body><d:ProbeMatches><d:ProbeMatch>'
           '<w:EndpointReference><w:Address>urn:uuid:2419d68a-2dd2-21b2-a205-a45e60aabbcc</w:Address></w:EndpointReference>'
           '<d:Scopes>onvif://www.onvif.org/name/IPC onvif://www.onvif.org/hardware/DS-2CD1023</d:Scopes>'
           '<d:XAddrs>http://192.168.1.23/onvif/device_service</d:XAddrs></d:ProbeMatch></d:ProbeMatches></e:Body></e:Envelope>')
    m = net_scan.parse_probe_match(xml)
    assert m["ip"] == "192.168.1.23" and m["uuid"] == "2419d68a-2dd2-21b2-a205-a45e60aabbcc" and m["hardware"] == "DS-2CD1023"


@pytest.fixture
def lan(monkeypatch):
    """A fake LAN: hosts with RTSP open, their MACs, ONVIF answers and how each answers OUR url."""
    L = {"open": ["192.168.1.23", "192.168.1.40"], "arp": {}, "onvif": {}, "describe": {}, "serial": {}, "fp": {}, "log": []}
    monkeypatch.setattr(camera_find, "local_subnets", lambda extra=(): ["192.168.1"])
    monkeypatch.setattr(camera_find, "port_sweep", lambda subnets, port, timeout: list(L["open"]))
    monkeypatch.setattr(camera_find, "ws_discovery", lambda t=2.5: dict(L["onvif"]))
    monkeypatch.setattr(camera_find, "arp_table", lambda: dict(L["arp"]))
    monkeypatch.setattr(camera_find, "rtsp_describe", lambda url, timeout=3.0: (L["describe"].get(url.split("@")[1].split(":")[0], 0), ""))
    monkeypatch.setattr(camera_find, "onvif_device_info", lambda ip, u, p, timeout=4: L["serial"].get(ip, {}))
    monkeypatch.setattr(camera_find, "rtsp_fingerprint", lambda ip, port=554, timeout=2.0: L["fp"].get(ip, {"server": "", "realm": ""}))
    return L


def test_relocate_by_mac(lan):
    lan["arp"] = {"192.168.1.1": "c8:3a:35:11:22:33", "192.168.1.23": "a4:5e:60:aa:bb:cc"}
    lan["describe"] = {"192.168.1.23": 401}  # password changed too: still the same device
    url, how = camera_find.relocate(OLD, {"mac": "a4:5e:60:aa:bb:cc"}, log=lan["log"].append)
    assert how == "mac" and url == "rtsp://admin:Admin%40123@192.168.1.23:554/Streaming/Channels/101"
    assert any("MAC a4:5e:60:aa:bb:cc ab 192.168.1.23" in m for m in lan["log"])


def test_relocate_by_onvif_uuid_then_serial(lan):
    lan["onvif"] = {"192.168.1.40": {"uuid": "abc-1", "name": "IPC"}}
    url, how = camera_find.relocate(OLD, {"uuid": "abc-1"}, log=lan["log"].append)
    assert how == "onvif-uuid" and "192.168.1.40" in url
    lan["onvif"] = {}
    lan["serial"] = {"192.168.1.23": {"serial": "SN777"}, "192.168.1.40": {"serial": "OTHER"}}
    url, how = camera_find.relocate(OLD, {"serial": "SN777"}, log=lan["log"].append)
    assert how == "serial" and "192.168.1.23" in url


def test_relocate_by_credentials_single_match_and_excludes_other_cameras(lan):
    lan["describe"] = {"192.168.1.23": 200, "192.168.1.40": 401}  # only .23 accepts our user/pw + path
    url, how = camera_find.relocate(OLD, {}, log=lan["log"].append)
    assert how == "creds" and "192.168.1.23" in url and url.endswith("/Streaming/Channels/101")
    # .23 is already Camera 2's IP -> must not steal it
    url, how = camera_find.relocate(OLD, {}, exclude_ips={"192.168.1.23"}, log=lan["log"].append)
    assert (url, how) == ("", "not-found")


def test_relocate_ambiguous_uses_server_fingerprint_or_gives_up(lan):
    lan["describe"] = {"192.168.1.23": 200, "192.168.1.40": 200}
    lan["fp"] = {"192.168.1.23": {"server": "Hipcam RealServer/V1.0", "realm": "Hipcam"}, "192.168.1.40": {"server": "Dahua", "realm": "Login"}}
    url, how = camera_find.relocate(OLD, {"server": "Dahua", "realm": "Login"}, log=lan["log"].append)
    assert how == "creds+server" and "192.168.1.40" in url
    url, how = camera_find.relocate(OLD, {}, log=lan["log"].append)
    assert url == "" and how == "ambiguous:192.168.1.23,192.168.1.40"


def test_relocate_never_returns_old_ip(lan):
    lan["open"] = ["192.168.1.7"]
    lan["describe"] = {"192.168.1.7": 200}
    assert camera_find.relocate(OLD, {}, log=lan["log"].append) == ("", "not-found")


# ---------------- worker integration ----------------
@pytest.fixture
def cfgfile(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    cfg = {**config.DEFAULTS, "cameras": [
        {"id": "cam1", "name": "Gate 1", "rtsp_url": OLD, "monitor": True, "fingerprint": {"mac": "a4:5e:60:aa:bb:cc", "ip": "192.168.1.7"}},
        {"id": "cam2", "name": "Gate 2", "rtsp_url": "rtsp://admin:pw@192.168.1.40:554/x", "monitor": True},
    ], "active_camera_id": "cam1", "rtsp_url": OLD, "wa_enabled": False}
    config.save_config(cfg)
    return cfg


def test_worker_auto_find_saves_new_url_and_keeps_other_camera(cfgfile, monkeypatch):
    seen = {}

    def fake_relocate(url, fp, exclude_ips, log):
        seen.update(url=url, fp=fp, exclude=set(exclude_ips))
        return "rtsp://admin:Admin%40123@192.168.1.23:554/Streaming/Channels/101", "mac"

    monkeypatch.setattr(camera_find, "relocate", fake_relocate)
    w = service.Worker("cam1")
    w._unreach_since = 1.0
    new = w.auto_find(OLD)
    assert new.endswith("@192.168.1.23:554/Streaming/Channels/101")
    assert seen["fp"]["mac"] == "a4:5e:60:aa:bb:cc" and seen["exclude"] == {"192.168.1.40"}  # Camera 2's IP protected
    saved = config.load_config()
    assert saved["cameras"][0]["rtsp_url"] == new and saved["rtsp_url"] == new  # active camera: top-level too
    assert saved["cameras"][1]["rtsp_url"] == "rtsp://admin:pw@192.168.1.40:554/x"
    assert w._unreach_since == 0.0 and "naye IP 192.168.1.23" in w.status and "mac" in w.status


def test_worker_auto_find_not_found_keeps_url_and_explains(cfgfile, monkeypatch):
    monkeypatch.setattr(camera_find, "relocate", lambda url, fp, exclude_ips, log: ("", "ambiguous:192.168.1.23,192.168.1.40"))
    w = service.Worker("cam1")
    w.open_detail = "timed out"
    assert w.auto_find(OLD) == ""
    assert config.load_config()["cameras"][0]["rtsp_url"] == OLD
    assert w.status.startswith("ERROR: Camera 192.168.1.7:554 jawab nahi de raha") and "2 cameras yahi password" in w.status
    monkeypatch.setattr(camera_find, "relocate", lambda url, fp, exclude_ips, log: ("", "not-found"))
    w.auto_find(OLD)
    assert w.status.endswith("LAN scan: camera nahi mila")


def test_stream_once_triggers_auto_find_after_45s_silent(cfgfile, monkeypatch):
    monkeypatch.setattr(service, "rtsp_port_reachable", lambda src, timeout=2.5: (False, "timed out"))
    monkeypatch.setattr(service.time, "sleep", lambda s: None)
    clock = {"t": 1000.0}
    monkeypatch.setattr(service.time, "time", lambda: clock["t"])
    calls = []
    w = service.Worker("cam1")
    monkeypatch.setattr(w, "auto_find", lambda source: calls.append(source) or "")
    w._gen = 0
    w._alive = lambda g: True
    w._open = lambda *a, **k: None
    cfg = w.cam_cfg()
    w._stream_once(0, cfg)          # t=1000: silent since now, no scan yet
    clock["t"] = 1030.0
    w._stream_once(0, cfg)          # 30 s: still waiting (short power blip)
    assert calls == []
    clock["t"] = 1046.0
    w._stream_once(0, cfg)          # 46 s silent -> scan
    assert calls == [service.normalize_rtsp_url(OLD)]
    clock["t"] = 1100.0
    w._stream_once(0, cfg)          # not again before 10 min
    assert len(calls) == 1
    clock["t"] = 1046.0 + 601
    w._stream_once(0, cfg)
    assert len(calls) == 2


def test_fingerprint_saved_per_camera_when_stream_starts(cfgfile, monkeypatch):
    monkeypatch.setattr(camera_find, "fingerprint", lambda url, log=None: {"ip": "192.168.1.7", "port": 554, "at": int(time.time()),
                                                                               "mac": "a4:5e:60:aa:bb:cc", "serial": "SN1"})
    w = service.Worker("cam1")
    w.collect_fingerprint(OLD)
    saved = config.load_config()
    assert saved["cameras"][0]["fingerprint"]["serial"] == "SN1" and saved["cameras"][0]["rtsp_url"] == OLD
    assert "fingerprint" not in saved["cameras"][1]


def test_autofind_endpoint(cfgfile, monkeypatch):
    from fastapi.testclient import TestClient

    tok = "t-autofind"
    service._tokens[tok] = time.time()
    c = TestClient(service.app)
    c.headers["X-Auth-Token"] = tok
    monkeypatch.setattr(camera_find, "relocate", lambda url, fp, exclude_ips, log: ("rtsp://admin:Admin%40123@192.168.1.23:554/Streaming/Channels/101", "creds"))
    j = c.post("/api/camera/autofind", json={}).json()
    assert j["ok"] is True and j["url"].endswith("@192.168.1.23:554/Streaming/Channels/101") and "naye IP" in j["status"]
    assert c.post("/api/camera/autofind", json={}, headers={"X-Auth-Token": "bad"}).status_code == 401
