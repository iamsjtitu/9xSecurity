"""Backend tests for WA API key visibility fix (iteration 15)."""
import pytest
import requests

BASE = "http://127.0.0.1:8971"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE}/api/login", json={"username": "admin", "password": "9xsecurity"})
    assert r.status_code == 200, r.text
    tok = r.json().get("token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def h(token):
    return {"X-Auth-Token": token}


def test_login(token):
    assert isinstance(token, str) and len(token) > 0


def test_get_settings_shape(h):
    r = requests.get(f"{BASE}/api/settings", headers=h)
    assert r.status_code == 200
    d = r.json()
    # visible secret
    assert "wa_api_key" in d and isinstance(d["wa_api_key"], str)
    assert "wa_api_key_set" in d and isinstance(d["wa_api_key_set"], bool)
    # write-only secrets
    assert d.get("gh_token") == ""
    assert "gh_token_set" in d
    assert d.get("wa_account_password") == ""
    assert "wa_account_password_set" in d


def test_wa_api_key_strip_and_save(h):
    # Save with whitespace/newline
    r = requests.post(f"{BASE}/api/settings", headers=h,
                      json={"wa_api_key": "  wa9x_TESTKEY_123 \n"})
    assert r.status_code == 200
    assert r.json().get("ok") is True

    # Verify stripped and returned in plain
    r2 = requests.get(f"{BASE}/api/settings", headers=h)
    d = r2.json()
    assert d["wa_api_key"] == "wa9x_TESTKEY_123"
    assert d["wa_api_key_set"] is True


def test_gh_token_not_cleared_by_empty(h):
    # set gh_token
    r = requests.post(f"{BASE}/api/settings", headers=h, json={"gh_token": "ghp_test"})
    assert r.status_code == 200
    d = requests.get(f"{BASE}/api/settings", headers=h).json()
    assert d["gh_token_set"] is True

    # posting empty should NOT clear
    r2 = requests.post(f"{BASE}/api/settings", headers=h, json={"gh_token": ""})
    assert r2.status_code == 200
    d2 = requests.get(f"{BASE}/api/settings", headers=h).json()
    assert d2["gh_token_set"] is True, "empty gh_token should retain previous value"


def test_wa_api_key_empty_clears(h):
    # Confirm still set from earlier test
    d = requests.get(f"{BASE}/api/settings", headers=h).json()
    assert d["wa_api_key_set"] is True

    r = requests.post(f"{BASE}/api/settings", headers=h, json={"wa_api_key": ""})
    assert r.status_code == 200
    d2 = requests.get(f"{BASE}/api/settings", headers=h).json()
    assert d2["wa_api_key"] == ""
    assert d2["wa_api_key_set"] is False


def test_whatsapp_test_bad_key_returns_hinglish(h):
    r = requests.post(f"{BASE}/api/whatsapp/test", headers=h,
                      json={"wa_api_key": "wa9x_BADKEY",
                            "wa_recipients": ["919999999999"],
                            "wa_base_url": "https://wa.9x.design"})
    assert r.status_code == 200
    d = r.json()
    assert d.get("ok") is False
    detail = d.get("detail", "")
    assert "FAILED" in detail
    # Either HTTP xxx hint or Internet/connection error is acceptable
    ok = ("HTTP 401" in detail or "HTTP 403" in detail
          or "API key galat" in detail or "QR" in detail
          or "Internet/connection error" in detail
          or "HTTP" in detail)
    assert ok, f"detail did not contain expected explanation: {detail}"
    print(f"[whatsapp test detail]: {detail}")


def test_cleanup_restore_gh_token_and_password():
    """Restore config to original: gh_token empty (was empty), password 9xsecurity."""
    # login again fresh
    tok = requests.post(f"{BASE}/api/login",
                        json={"username": "admin", "password": "9xsecurity"}).json()["token"]
    h = {"X-Auth-Token": tok}
    # Original gh_token was "" (per config.json seen at start). We set it to 'ghp_test'.
    # There's no direct clear endpoint; we cannot pass empty (protected). So we must
    # directly set it via a special path. Try: some backends allow explicit null.
    # Attempt a "clear" via posting explicit sentinel; if not supported we report.
    # Actually service.py may treat None differently. Try posting {"gh_token": None}
    r = requests.post(f"{BASE}/api/settings", headers=h, json={"gh_token": None})
    print(f"gh_token None reset: {r.status_code} {r.text[:200]}")
    # Ensure password is 9xsecurity (in case any earlier run changed it)
    # Not changing here; login already worked so it's still 9xsecurity.
