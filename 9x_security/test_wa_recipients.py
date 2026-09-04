"""Recipients typed on one line ('8598800000, 9166175477') must become separate numbers
— previously they were glued into one 20-digit 'number' and no message arrived."""
import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.dirname(__file__))
from whatsapp import WhatsAppNotifier, parse_recipients  # noqa: E402

BASE = f"http://127.0.0.1:{os.environ.get('ENGINE_PORT', '8971')}"
http = requests.Session()


def test_parse_recipients_splits_and_validates():
    nums, bad = parse_recipients(["8598800000, 9166175477"])
    assert nums == ["8598800000", "9166175477"] and bad == []
    nums, bad = parse_recipients(["+91 85988 00000", "919166175477;917205930002 / 918888877777"])
    assert nums == ["918598800000", "919166175477", "917205930002", "918888877777"] and bad == []
    nums, bad = parse_recipients("918598800000\n918598800000\n12345")
    assert nums == ["918598800000"] and bad == ["12345"]        # dedupe + too short flagged
    nums, bad = parse_recipients(["859880000091661754777205930002"])
    assert nums == [] and bad == ["859880000091661754777205930002"]  # glued numbers rejected
    assert parse_recipients([]) == ([], []) and parse_recipients(None) == ([], [])


def test_notifier_uses_split_numbers():
    n = WhatsAppNotifier({"wa_enabled": True, "wa_api_key": "k", "wa_recipients": ["8598800000, 9166175477"]})
    assert n.recipients == ["8598800000", "9166175477"]


@pytest.fixture(scope="module")
def token():
    try:
        r = http.post(f"{BASE}/api/login", json={"username": "admin", "password": "9xsecurity"}, timeout=5)
    except requests.RequestException:
        pytest.skip("engine service not running")
    if r.status_code != 200:
        pytest.skip("default login unavailable")
    return r.json()["token"]


def test_settings_api_normalizes_and_rejects_bad_recipients(token):
    h = {"X-Auth-Token": token}
    before = http.get(f"{BASE}/api/settings", headers=h, timeout=5).json()["wa_recipients"]
    try:
        r = http.post(f"{BASE}/api/settings", json={"wa_recipients": ["8598800000, 9166175477"]}, headers=h, timeout=5)
        assert r.status_code == 200, r.text
        got = http.get(f"{BASE}/api/settings", headers=h, timeout=5).json()["wa_recipients"]
        assert got == ["8598800000", "9166175477"]
        r = http.post(f"{BASE}/api/settings", json={"wa_recipients": ["8598800000", "12"]}, headers=h, timeout=5)
        assert r.status_code == 400 and "12" in r.json()["detail"]
        assert http.get(f"{BASE}/api/settings", headers=h, timeout=5).json()["wa_recipients"] == got  # unchanged
    finally:
        http.post(f"{BASE}/api/settings", json={"wa_recipients": before}, headers=h, timeout=5)
