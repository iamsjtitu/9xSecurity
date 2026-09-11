"""Iteration 27 - Setup Wizard backend API tests"""
import json
import time
import requests

BASE = "http://127.0.0.1:8971"
CFG_PATH = "/app/9x_security/config.json"


def _login():
    r = requests.post(f"{BASE}/api/login", json={"username": "admin", "password": "9xsecurity"})
    if r.status_code == 200 and r.json().get("token"):
        return r.json()["token"]
    # Handle must-change: reset via direct settings if a temp password path is somehow set
    raise AssertionError(f"login failed: {r.status_code} {r.text}")


def _hdr(tok):
    return {"X-Auth-Token": tok, "Content-Type": "application/json"}


def test_state_has_setup_done_bool():
    tok = _login()
    st = requests.get(f"{BASE}/api/state", headers=_hdr(tok)).json()
    assert "setup_done" in st and isinstance(st["setup_done"], bool), st


def test_options_setup_done_false_with_rtsp_stays_true():
    tok = _login()
    # ensure some rtsp_url present
    requests.post(f"{BASE}/api/camera/disconnect", headers=_hdr(tok))
    requests.post(f"{BASE}/api/camera/connect", headers=_hdr(tok),
                  json={"url": "rtsp://u:p@192.0.2.1:554/s"})
    time.sleep(1)
    requests.post(f"{BASE}/api/camera/disconnect", headers=_hdr(tok))
    r = requests.post(f"{BASE}/api/options", headers=_hdr(tok), json={"setup_done": False})
    assert r.status_code == 200, r.text
    st = requests.get(f"{BASE}/api/state", headers=_hdr(tok)).json()
    assert st.get("rtsp_url"), f"expected rtsp_url set: {st.get('rtsp_url')}"
    assert st["setup_done"] is True, f"setup_done should be True when rtsp_url set: {st}"


def test_first_run_reset_and_setup_done_false():
    tok = _login()
    requests.post(f"{BASE}/api/camera/disconnect", headers=_hdr(tok))
    time.sleep(0.5)
    d = json.load(open(CFG_PATH))
    d["rtsp_url"] = ""
    d["setup_done"] = False
    d["line"] = None
    json.dump(d, open(CFG_PATH, "w"), indent=2)
    # give backend chance to reload - state reads from cfg
    time.sleep(0.5)
    st = requests.get(f"{BASE}/api/state", headers=_hdr(tok)).json()
    assert st["setup_done"] is False, st
    assert st.get("line") in (None, {}), f"line should be null: {st.get('line')}"


def test_entry_direction_options():
    tok = _login()
    r = requests.post(f"{BASE}/api/options", headers=_hdr(tok), json={"entry_direction": "neg"})
    assert r.status_code == 200, r.text
    st = requests.get(f"{BASE}/api/state", headers=_hdr(tok)).json()
    assert st.get("entry_direction") == "neg", st

    r = requests.post(f"{BASE}/api/options", headers=_hdr(tok), json={"entry_direction": "bogus"})
    # invalid ignored (should still be 200 but no change) OR 400 - accept either but state must remain 'neg'
    st = requests.get(f"{BASE}/api/state", headers=_hdr(tok)).json()
    assert st.get("entry_direction") == "neg", f"bogus should be ignored: {st}"

    r = requests.post(f"{BASE}/api/options", headers=_hdr(tok), json={"entry_direction": "pos"})
    assert r.status_code == 200, r.text
    st = requests.get(f"{BASE}/api/state", headers=_hdr(tok)).json()
    assert st.get("entry_direction") == "pos", st


if __name__ == "__main__":
    for fn in [test_state_has_setup_done_bool,
               test_options_setup_done_false_with_rtsp_stays_true,
               test_first_run_reset_and_setup_done_false,
               test_entry_direction_options]:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception as e:
            print("FAIL", fn.__name__, e)
