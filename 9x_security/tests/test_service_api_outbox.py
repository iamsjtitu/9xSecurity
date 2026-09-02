"""Integration tests against running service.py at 127.0.0.1:8971.

Covers: login, /api/outbox (auth + shape), /api/state (outbox_pending),
/api/counts (outbox_pending), settings save/get (secrets masked),
events endpoint regression, and the background _outbox_loop delivering
a seeded pending row when a local mock provider is reachable.
"""
import os
import json
import time
import shutil
import http.server
import threading
import socket
import sqlite3
import subprocess
import sys
import tempfile

import pytest
import requests

BASE = "http://127.0.0.1:8971"
CFG_PATH = "/app/9x_security/config.json"
DB_PATH = "/app/9x_security/events.db"

USER = "admin"
DEFAULT_PW = "9xsecurity"
TEMP_PW = "NxTest@2026"


def _login(pw):
    r = requests.post(f"{BASE}/api/login", json={"username": USER, "password": pw}, timeout=5)
    return r


@pytest.fixture(scope="session")
def token():
    r = _login(DEFAULT_PW)
    if r.status_code == 200 and r.json().get("must_change_password"):
        tok = r.json()["token"]
        # set temp password to clear must_change flag
        rr = requests.post(f"{BASE}/api/settings", json={"new_password": TEMP_PW},
                           headers={"X-Auth-Token": tok}, timeout=5)
        assert rr.status_code == 200, rr.text
        r = _login(TEMP_PW)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _hdr(tok):
    return {"X-Auth-Token": tok}


# ---- Auth ---------------------------------------------------------------
def test_outbox_requires_auth():
    r = requests.get(f"{BASE}/api/outbox", timeout=5)
    assert r.status_code == 401

def test_state_requires_auth():
    r = requests.get(f"{BASE}/api/state", timeout=5)
    assert r.status_code == 401

def test_counts_requires_auth():
    r = requests.get(f"{BASE}/api/counts", timeout=5)
    assert r.status_code == 401


# ---- Endpoint shape -----------------------------------------------------
def test_outbox_shape(token):
    r = requests.get(f"{BASE}/api/outbox", headers=_hdr(token), timeout=5)
    assert r.status_code == 200
    data = r.json()
    assert "pending" in data and "items" in data
    assert isinstance(data["pending"], int)
    assert isinstance(data["items"], list)

def test_state_has_outbox_pending(token):
    r = requests.get(f"{BASE}/api/state", headers=_hdr(token), timeout=5)
    assert r.status_code == 200
    assert "outbox_pending" in r.json()

def test_counts_has_outbox_pending(token):
    r = requests.get(f"{BASE}/api/counts", headers=_hdr(token), timeout=5)
    assert r.status_code == 200
    assert "outbox_pending" in r.json()


# ---- Regression ---------------------------------------------------------
def test_events_endpoint(token):
    r = requests.get(f"{BASE}/api/events?limit=5", headers=_hdr(token), timeout=5)
    assert r.status_code == 200
    body = r.json()
    # accept list or {items: [...]}
    assert isinstance(body, (list, dict))

def test_settings_get_masks_secrets(token):
    r = requests.get(f"{BASE}/api/settings", headers=_hdr(token), timeout=5)
    assert r.status_code == 200
    s = r.json()
    # secrets must not be sent back verbatim
    assert "auth_hash" not in s or s.get("auth_hash") in (None, "", "***")
    assert "auth_salt" not in s or s.get("auth_salt") in (None, "", "***")

def test_settings_save_roundtrip(token):
    # save a benign field then verify
    r = requests.post(f"{BASE}/api/settings", json={"retention_days": 9},
                      headers=_hdr(token), timeout=5)
    assert r.status_code == 200
    r2 = requests.get(f"{BASE}/api/settings", headers=_hdr(token), timeout=5)
    assert r2.json().get("retention_days") == 9
    # restore
    requests.post(f"{BASE}/api/settings", json={"retention_days": 7},
                  headers=_hdr(token), timeout=5)


# ---- Direct DB insertion updates counts (badge feed) --------------------
def test_direct_db_outbox_reflected_in_counts(token):
    # use engine's own EventDB so schema/paths line up
    sys.path.insert(0, "/app/9x_security")
    from database import EventDB
    db = EventDB(db_path=DB_PATH)
    baseline = db.outbox_count()
    rid = db.outbox_add("910000000000", "TEST_badge", "")
    try:
        # poll /api/state (badge source) up to 3s
        deadline = time.time() + 3.5
        seen = None
        while time.time() < deadline:
            r = requests.get(f"{BASE}/api/state", headers=_hdr(token), timeout=3)
            seen = r.json().get("outbox_pending")
            if seen is not None and seen >= baseline + 1:
                break
            time.sleep(0.2)
        assert seen >= baseline + 1, f"expected pending >= {baseline+1}, got {seen}"
        # /api/outbox lists it
        items = requests.get(f"{BASE}/api/outbox", headers=_hdr(token), timeout=3).json()["items"]
        assert any(it.get("recipient") == "910000000000" for it in items)
    finally:
        # cleanup
        try:
            db.conn.execute("DELETE FROM outbox WHERE id=?", (rid,))
            db.conn.commit()
        except Exception:
            pass
        db.close()


# ---- _outbox_loop end-to-end delivery with mock provider ---------------
class _MockHandler(http.server.BaseHTTPRequestHandler):
    hits = []
    def do_POST(self):
        _MockHandler.hits.append(self.path)
        try:
            self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        except Exception:
            pass
        self.send_response(200); self.end_headers()
        self.wfile.write(b'{"success":true}')
    def log_message(self, *a): pass


@pytest.mark.timeout(90)
def test_outbox_loop_delivers_via_mock(token):
    """Start mock provider, restart service.py with OUTBOX_RETRY_SECONDS=3
    pointing wa_base_url to mock, seed outbox row, expect delivery in ~40s."""
    # backup config
    with open(CFG_PATH) as f: cfg_backup = f.read()

    # find free port and start mock
    _MockHandler.hits = []
    srv = http.server.HTTPServer(("127.0.0.1", 0), _MockHandler)
    port = srv.server_port
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()

    # find current service.py pid (dev-mode)
    def _find_svc():
        try:
            out = subprocess.check_output(["pgrep", "-f", "service.py"], text=True).strip().splitlines()
            return [int(x) for x in out if x]
        except subprocess.CalledProcessError:
            return []

    orig_pids = _find_svc()

    # write settings via API so config includes mock + wa_enabled
    r = requests.post(f"{BASE}/api/settings", json={
        "wa_enabled": True,
        "wa_base_url": f"http://127.0.0.1:{port}",
        "wa_api_key": "TEST_KEY",
        "wa_recipients": ["919999999999"],
        "wa_send_image": True,
    }, headers=_hdr(token), timeout=5)
    assert r.status_code == 200

    # seed pending row in DB
    sys.path.insert(0, "/app/9x_security")
    from database import EventDB
    db = EventDB(db_path=DB_PATH)
    rid = db.outbox_add("919999999999", "TEST_loop_delivery", "")
    db.close()

    # restart service.py with OUTBOX_RETRY_SECONDS=3
    for pid in orig_pids:
        try: os.kill(pid, 15)
        except Exception: pass
    time.sleep(2)
    env = {**os.environ, "OUTBOX_RETRY_SECONDS": "3"}
    proc = subprocess.Popen([sys.executable, "service.py"], cwd="/app/9x_security",
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        # wait for service to come up
        for _ in range(30):
            try:
                if requests.get(f"{BASE}/api/health", timeout=1).status_code == 200:
                    break
            except Exception: pass
            time.sleep(0.5)

        # login (password may still be TEMP_PW since we changed it earlier and config persisted)
        r = _login(TEMP_PW)
        if r.status_code != 200:
            r = _login(DEFAULT_PW)
        assert r.status_code == 200, r.text
        tok = r.json()["token"]

        # wait up to 40s for loop to deliver
        delivered = False
        deadline = time.time() + 40
        while time.time() < deadline:
            if _MockHandler.hits:
                # give loop time to delete from db
                time.sleep(1.5)
                rr = requests.get(f"{BASE}/api/outbox", headers=_hdr(tok), timeout=3).json()
                if rr["pending"] == 0:
                    delivered = True
                    break
            time.sleep(1)
        assert _MockHandler.hits, "mock provider was never hit by outbox loop"
        assert delivered, "outbox rows not cleared after mock delivered"
    finally:
        # stop the ephemeral service and restore original
        try: proc.terminate(); proc.wait(timeout=5)
        except Exception:
            try: proc.kill()
            except Exception: pass
        srv.shutdown()
        # restore config.json
        with open(CFG_PATH, "w") as f: f.write(cfg_backup)
        # cleanup any leftover outbox rows created by this test
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("DELETE FROM outbox WHERE recipient IN ('919999999999','910000000000')")
            conn.commit(); conn.close()
        except Exception:
            pass
        # restart original service.py so environment is clean for later tests
        subprocess.Popen([sys.executable, "service.py"], cwd="/app/9x_security",
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(20):
            try:
                if requests.get(f"{BASE}/api/health", timeout=1).status_code == 200:
                    break
            except Exception: pass
            time.sleep(0.5)


# ---- Restore admin password to default at the very end ------------------
def test_zzz_restore_default_password(token):
    """Runs last (alphabetical). Resets admin password back to '9xsecurity'."""
    # We may be running against a restarted service in prior test -> re-login
    r = _login(TEMP_PW)
    if r.status_code != 200:
        r = _login(DEFAULT_PW)
        # already default -> nothing to do
        if r.status_code == 200 and not r.json().get("must_change_password"):
            return
    tok = r.json()["token"]
    rr = requests.post(f"{BASE}/api/settings", json={"new_password": DEFAULT_PW},
                       headers=_hdr(tok), timeout=5)
    assert rr.status_code == 200, rr.text
    # verify
    r2 = _login(DEFAULT_PW)
    assert r2.status_code == 200
