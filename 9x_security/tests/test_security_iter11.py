"""Security hardening regression tests - iteration 11."""
import os
import sys
import time
import json
import shutil
import subprocess

import pytest
import requests

BASE = "http://127.0.0.1:8971"
sys.path.insert(0, "/app/9x_security")
import config as cfg_mod  # noqa: E402
import auth as auth_mod  # noqa: E402

CONFIG_PATH = cfg_mod.CONFIG_PATH if hasattr(cfg_mod, "CONFIG_PATH") else "/app/9x_security/config.json"
BACKUP = "/tmp/config_backup_iter11.json"


@pytest.fixture(scope="module", autouse=True)
def backup_and_restore():
    shutil.copy(CONFIG_PATH, BACKUP)
    yield
    shutil.copy(BACKUP, CONFIG_PATH)
    # ensure default password is restored
    c = cfg_mod.load_config()
    s, h = auth_mod.hash_password("9xsecurity")
    c["auth_salt"], c["auth_hash"] = s, h
    c["wa_api_key"] = ""
    c["gh_token"] = ""
    cfg_mod.save_config(c)


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE}/api/login",
                      json={"username": "admin", "password": "9xsecurity"})
    assert r.status_code == 200, r.text
    data = r.json()
    return data["token"]


# --- login / must_change --------------------------------------------------
def test_login_default_returns_must_change():
    r = requests.post(f"{BASE}/api/login",
                      json={"username": "admin", "password": "9xsecurity"})
    assert r.status_code == 200
    d = r.json()
    assert "token" in d and len(d["token"]) > 20
    assert d.get("must_change_password") is True


def test_login_wrong_password():
    r = requests.post(f"{BASE}/api/login",
                      json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


# --- settings secrets -----------------------------------------------------
def test_settings_secrets_masked(token):
    h = {"x-auth-token": token}
    # write fake secrets
    r = requests.post(f"{BASE}/api/settings", json={
        "wa_api_key": "SECRET_FAKE_KEY_123",
        "gh_token": "ghp_FAKE_TOKEN_XYZ",
    }, headers=h)
    assert r.status_code == 200

    # GET must not return them
    r = requests.get(f"{BASE}/api/settings", headers=h)
    assert r.status_code == 200
    s = r.json()
    assert s["wa_api_key"] == ""
    assert s["gh_token"] == ""
    assert s["wa_api_key_set"] is True
    assert s["gh_token_set"] is True

    # POST with empty secret must NOT wipe stored value
    r = requests.post(f"{BASE}/api/settings", json={
        "wa_api_key": "",
        "gh_token": "",
    }, headers=h)
    assert r.status_code == 200
    c = cfg_mod.load_config()
    assert c["wa_api_key"] == "SECRET_FAKE_KEY_123"
    assert c["gh_token"] == "ghp_FAKE_TOKEN_XYZ"

    # cleanup: overwrite with empty via a non-empty then explicit wipe by inspecting - use direct config
    c["wa_api_key"] = ""
    c["gh_token"] = ""
    cfg_mod.save_config(c)

    r = requests.get(f"{BASE}/api/settings", headers=h)
    s = r.json()
    assert s["wa_api_key_set"] is False
    assert s["gh_token_set"] is False


# --- logout / token expiry ------------------------------------------------
def test_logout_invalidates_token():
    r = requests.post(f"{BASE}/api/login",
                      json={"username": "admin", "password": "9xsecurity"})
    t = r.json()["token"]
    h = {"x-auth-token": t}
    r = requests.get(f"{BASE}/api/state", headers=h)
    assert r.status_code == 200
    r = requests.post(f"{BASE}/api/logout", headers=h)
    assert r.status_code == 200
    r = requests.get(f"{BASE}/api/state", headers=h)
    assert r.status_code == 401


def test_token_ttl_constant_present():
    # code inspection: TOKEN_TTL = 12 * 3600
    src = open("/app/9x_security/service.py").read()
    assert "TOKEN_TTL = 12 * 3600" in src
    assert "time.time() - ts > TOKEN_TTL" in src


def test_token_expiry_simulated():
    # Import service module and forcibly age a token
    import importlib
    if "service" in sys.modules:
        del sys.modules["service"]
    svc = importlib.import_module("service")
    tok = "abc" * 8
    svc._tokens[tok] = time.time() - svc.TOKEN_TTL - 1
    # Build a fake Request-like object
    class FakeReq:
        headers = {"x-auth-token": tok}
        query_params = {}
    with pytest.raises(Exception):
        svc._check(FakeReq())
    assert tok not in svc._tokens  # expired token popped


# --- CORS -----------------------------------------------------------------
def test_cors_evil_origin_rejected():
    r = requests.options(f"{BASE}/api/login", headers={
        "Origin": "https://evil.com",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    # starlette CORS returns 400 when origin not allowed
    assert r.status_code == 400 or "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_cors_null_origin_allowed():
    r = requests.options(f"{BASE}/api/login", headers={
        "Origin": "null",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "null"


# --- RTSP credential redaction in logs -----------------------------------
def test_camera_test_redacts_credentials(token):
    h = {"x-auth-token": token}
    r = requests.post(f"{BASE}/api/camera/test", headers=h,
                      json={"url": "rtsp://user:Pass@123@192.0.2.1:554/s"})
    assert r.status_code == 200
    time.sleep(0.5)
    with open("/app/9x_security/camera_log.txt") as f:
        # last 40 lines
        tail = "".join(f.readlines()[-40:])
    # Look at PROBE start lines only for credential redaction check
    probe_lines = [ln for ln in tail.split("\n") if "PROBE start" in ln]
    assert probe_lines, "no PROBE start line found"
    last_probe = probe_lines[-1]
    assert "****@192.0.2.1" in last_probe
    assert "user:" not in last_probe
    assert "Pass" not in last_probe


# --- whatsapp test fallback (masked UI sends empty) -----------------------
def test_whatsapp_test_graceful_with_empty(token):
    h = {"x-auth-token": token}
    r = requests.post(f"{BASE}/api/whatsapp/test", headers=h,
                      json={"wa_api_key": "", "wa_recipients": []})
    assert r.status_code == 200
    d = r.json()
    assert "ok" in d and d["ok"] is False
    assert "detail" in d


# --- electron / index.html static hardening ------------------------------
def test_electron_open_path_allowlist():
    src = open("/app/9x_security/electron/electron-main.js").read()
    assert "os.homedir()" in src
    assert "isDirectory()" in src
    assert "'blocked'" in src or '"blocked"' in src


def test_index_html_csp_and_no_google_fonts():
    src = open("/app/9x_security/electron/index.html").read()
    assert "Content-Security-Policy" in src
    assert "connect-src" in src and "http://127.0.0.1:8971" in src
    assert "fonts.googleapis" not in src
    assert "fonts.gstatic" not in src
