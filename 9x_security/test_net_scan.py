"""Camera Scan: LAN discovery so the user never types an IP (ONVIF WS-Discovery + RTSP port
sweep + brand fingerprint). Verified with the in-process FakeCam (Digest realm 'IP Camera')."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import net_scan as ns  # noqa: E402
from test_rtsp_discover import FakeCam  # noqa: E402

HIK_MATCH = """<?xml version="1.0" encoding="UTF-8"?><env:Envelope xmlns:env="http://www.w3.org/2003/05/soap-envelope">
<env:Body><d:ProbeMatches xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"><d:ProbeMatch>
<wsa:EndpointReference xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"><wsa:Address>urn:uuid:1</wsa:Address></wsa:EndpointReference>
<d:Types>dn:NetworkVideoTransmitter tds:Device</d:Types>
<d:Scopes>onvif://www.onvif.org/type/video_encoder onvif://www.onvif.org/Profile/Streaming onvif://www.onvif.org/name/HIKVISION%20DS-2CD1023G0-I onvif://www.onvif.org/hardware/DS-2CD1023G0-I onvif://www.onvif.org/location/city/hangzhou</d:Scopes>
<d:XAddrs>http://192.168.1.64/onvif/device_service</d:XAddrs><d:MetadataVersion>10</d:MetadataVersion>
</d:ProbeMatch></d:ProbeMatches></env:Body></env:Envelope>"""

DAHUA_MATCH = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body><d:ProbeMatches xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"><d:ProbeMatch>
<d:Scopes>onvif://www.onvif.org/location/country/china onvif://www.onvif.org/name/IPC-HDW1230T1 onvif://www.onvif.org/hardware/IPC-HDW1230T1 onvif://www.onvif.org/Profile/Streaming</d:Scopes>
<d:XAddrs>http://192.168.1.108/onvif/device_service http://[fe80::1]/onvif/device_service</d:XAddrs>
</d:ProbeMatch></d:ProbeMatches></s:Body></s:Envelope>"""


@pytest.fixture(scope="module")
def cam():
    s = FakeCam()
    s.start()
    yield s
    s.stop()


def test_parse_probe_match_hikvision_and_dahua():
    h = ns.parse_probe_match(HIK_MATCH)
    assert h == {"ip": "192.168.1.64", "xaddr": "http://192.168.1.64/onvif/device_service",
                 "name": "HIKVISION DS-2CD1023G0-I", "hardware": "DS-2CD1023G0-I"}
    d = ns.parse_probe_match(DAHUA_MATCH)
    assert d["ip"] == "192.168.1.108" and d["name"] == "IPC-HDW1230T1"
    assert ns.parse_probe_match("<x/>") is None


def test_guess_brand_from_fingerprints():
    assert ns.guess_brand("", "IP Camera(C1234)") == "hikvision"
    assert ns.guess_brand("Rtsp Server/2.0", "Login to 4G0A1B2C") == "dahua"
    assert ns.guess_brand("", "", "HIKVISION DS-2CD1023G0-I") == "hikvision"
    assert ns.guess_brand("", "", "IPC-HDW1230T1", "IPC-HDW1230T1") == "dahua"
    assert ns.guess_brand("", "TP-LINK IP-Camera") == "tplink"
    assert ns.guess_brand("Hipcam RealServer/V1.0") == "hipcam"
    assert ns.guess_brand("H264DVR 1.0") == "xmeye"
    assert ns.guess_brand("", "", "", "") == ""


def test_local_subnets_includes_saved_camera_subnet():
    subs = ns.local_subnets(extra_hosts=["192.168.1.28", "not-an-ip", ""])
    assert "192.168.1" in subs
    assert not any(s.startswith("127.") for s in subs)


def test_scan_finds_fake_camera_and_fingerprints_it(cam):
    r = ns.scan_network(subnets=["127.0.0"], port=cam.port, ws_timeout=0, sweep_timeout=0.3, log=print)
    ips = [c["ip"] for c in r["cameras"]]
    assert "127.0.0.1" in ips, r
    c = r["cameras"][ips.index("127.0.0.1")]
    assert c["rtsp_open"] and c["auth_needed"] and c["realm"] == "IP Camera"
    assert c["brand"] == "hikvision"   # realm 'IP Camera' -> Hikvision family
    assert c["onvif"] is False and r["seconds"] < 20


def test_scan_empty_subnet_returns_no_cameras():
    r = ns.scan_network(subnets=["127.0.0"], port=1, ws_timeout=0, sweep_timeout=0.2)  # port 1: nothing listens
    assert r["cameras"] == [] and r["subnets"] == ["127.0.0"]
