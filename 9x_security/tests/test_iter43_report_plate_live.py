"""Iteration 43 (T1): live API checks for daily report, settings daily_report_*/pr_*, Plate Recognizer
key test (FAKE key only) + extra edge cases. Run against the running service on 127.0.0.1:8971."""
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta

import pytest
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import report  # noqa: E402

BASE = "http://127.0.0.1:8971"
DB = os.path.join(ROOT, "events.db")
MARK = "/tmp/TEST_iter43.jpg"
DAY = "2026-09-15"  # isolated past day for exact math
TODAY = datetime.now().strftime("%Y-%m-%d")
YDAY = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def _login():
    for pw in ("9xsecurity", "Temp123456"):
        r = requests.post(f"{BASE}/api/login", json={"username": "admin", "password": pw}, timeout=10)
        if r.status_code == 200:
            return r.json()["token"]
    pytest.skip("login failed")


@pytest.fixture(scope="module")
def tok():
    return _login()


@pytest.fixture(scope="module")
def H(tok):
    return {"X-Auth-Token": tok}


def _ins(c, day, t, vt, d, plate="", count=1, gate="Main Gate"):
    c.execute("INSERT INTO events(timestamp,date,time,vehicle_type,direction,plate,image_path,plate_status,plate_source,count,gate)"
              " VALUES(?,?,?,?,?,?,?,?,?,?,?)", (f"{day}T{t}", day, t, vt, d, plate, MARK, "", "", count, gate))


@pytest.fixture(scope="module", autouse=True)
def seed():
    c = sqlite3.connect(DB)
    for day in (DAY, TODAY, YDAY):
        _ins(c, day, "08:00:00", "truck", "Entry", "MH12AB1234")
        _ins(c, day, "08:05:00", "truck", "Exit", "")
        _ins(c, day, "09:00:00", "car", "Entry", "DL3CAB1234", gate="Back Gate")
        _ins(c, day, "09:10:00", "bus", "Exit")
        _ins(c, day, "09:20:00", "motorcycle", "Entry", gate="Back Gate")
        _ins(c, day, "09:30:00", "bicycle", "Exit", gate="Back Gate")
        _ins(c, day, "09:40:00", "person", "Entry", count=3)
        _ins(c, day, "09:50:00", "van", "Entry", gate="")
    c.commit()
    yield
    c.execute("DELETE FROM events WHERE image_path=?", (MARK,))
    c.commit()
    c.close()


# ---- report summary math
def test_report_summary_math(H):
    r = requests.get(f"{BASE}/api/report/daily", params={"date": DAY}, headers=H, timeout=30)
    assert r.status_code == 200, r.text
    j = r.json()
    s = j["summary"]
    assert s["date"] == DAY and s["events"] == 5 and s["vehicles_only"] is True  # person + 2 two-wheelers dropped
    g = s["gates"]
    assert set(g) == {"Main Gate", "Back Gate", "Gate"}
    assert g["Main Gate"]["truck"] == {"Entry": 1, "Exit": 1}
    assert g["Main Gate"]["person"] == {"Entry": 0, "Exit": 0}
    assert g["Back Gate"]["two_wheeler"] == {"Entry": 0, "Exit": 0}
    assert g["Gate"]["other"] == {"Entry": 1, "Exit": 0}
    assert (s["entry"], s["exit"]) == (3, 2)
    assert j["pdf_url"] == f"/api/report/daily.pdf?date={DAY}"
    assert os.path.isfile(j["pdf_path"])
    cap = j["caption"]
    assert "Total: Entry 3 / Exit 2" in cap and "tak ka hisaab" not in cap and "Sirf 4-wheeler" in cap
    assert "Tractor" not in cap and "Person" not in cap and "Two-wheeler" not in cap  # zero rows omitted


def test_report_today_partial_caption(H):
    r = requests.get(f"{BASE}/api/report/daily", headers=H, timeout=30)
    assert r.status_code == 200
    assert r.json()["summary"]["date"] == TODAY
    assert "Report: " in r.json()["caption"] and " se " in r.json()["caption"] and " tak" in r.json()["caption"]  # 24 h window (#42 rev.)


def test_report_yesterday_has_seed(H):
    s = requests.get(f"{BASE}/api/report/daily", params={"date": YDAY}, headers=H, timeout=30).json()["summary"]
    assert s["gates"]["Back Gate"]["car"]["Entry"] >= 1


def test_report_pdf_content(tok):
    import fitz
    r = requests.get(f"{BASE}/api/report/daily.pdf", params={"date": DAY, "t": tok}, timeout=30)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/pdf")
    assert r.content.startswith(b"%PDF-") and len(r.content) > 1500
    text = "".join(p.get_text() for p in fitz.open(stream=r.content, filetype="pdf"))
    for s in ("9x Security - Daily Report", "Gate: Main Gate", "MH12AB1234", "Not detected", "Detail (5 events)", "All gates",
              "Truck (2)", "Car (1)", "Bus (1)", "Only 4-wheelers"):
        assert s in text, s
    assert "Person" not in text and "Two-wheeler" not in text


def test_report_pdf_single_gate_omits_all_gates(tok):
    import fitz
    day = "2026-09-14"
    c = sqlite3.connect(DB)
    _ins(c, day, "10:00:00", "truck", "Entry")
    c.commit()
    # the report of `day` covers [day-1 @ T, day @ T): pin T so 10:00 is inside the window
    requests.post(f"{BASE}/api/settings", json={"daily_report_time": "21:00"}, headers={"X-Auth-Token": tok}, timeout=10)
    r = requests.get(f"{BASE}/api/report/daily.pdf", params={"date": day, "t": tok}, timeout=30)
    text = "".join(p.get_text() for p in fitz.open(stream=r.content, filetype="pdf"))
    assert "All gates" not in text and "Gate: Main Gate" in text


@pytest.mark.parametrize("bad", ["2026-13-01", "abc", "18-10-2026"])
def test_report_invalid_date(H, bad):
    assert requests.get(f"{BASE}/api/report/daily", params={"date": bad}, headers=H, timeout=10).status_code == 400


def test_report_unauth():
    assert requests.get(f"{BASE}/api/report/daily", timeout=10).status_code == 401
    assert requests.get(f"{BASE}/api/report/daily.pdf", timeout=10).status_code == 401


def test_report_send_no_wa_key(H):
    r = requests.post(f"{BASE}/api/report/send", json={"date": DAY}, headers=H, timeout=60)
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is False and j["detail"].startswith("API key khaali hai") and j["date"] == DAY
    assert j["pdf_path"].endswith(f"9x_Report_{DAY}.pdf")
    log = open(os.path.join(ROOT, "camera_log.txt"), encoding="utf-8", errors="ignore").read()[-20000:]
    assert re.search(rf"daily report {DAY} \(manual\) \[.*\]: Entry 3 / Exit 2 -> PROBLEM", log), log[-600:]


# ---- settings
def test_settings_fields_present(H):
    j = requests.get(f"{BASE}/api/settings", headers=H, timeout=10).json()
    for k in ("daily_report_enabled", "daily_report_time", "daily_report_last", "daily_report_next",
              "pr_enabled", "pr_api_key", "pr_api_key_set", "pr_mode"):
        assert k in j, k


def test_settings_bad_time_400(H):
    r = requests.post(f"{BASE}/api/settings", json={"daily_report_time": "9pm"}, headers=H, timeout=10)
    assert r.status_code == 400 and "Report ka time HH:MM" in r.text


def test_settings_out_of_range_time_not_500(H):
    # '25:99' passes the HH:MM regex — must not crash the save with a 500
    r = requests.post(f"{BASE}/api/settings", json={"daily_report_enabled": True, "daily_report_time": "25:99"}, headers=H, timeout=10)
    requests.post(f"{BASE}/api/settings", json={"daily_report_enabled": False, "daily_report_time": "21:00"}, headers=H, timeout=10)
    assert r.status_code in (200, 400), r.status_code


def test_settings_enable_baseline_and_next(H):
    now = datetime.now()
    future = (now + timedelta(minutes=30))
    t = future.strftime("%H:%M")
    r = requests.post(f"{BASE}/api/settings", json={"daily_report_enabled": True, "daily_report_time": t}, headers=H, timeout=10)
    assert r.status_code == 200
    j = requests.get(f"{BASE}/api/settings", headers=H, timeout=10).json()
    if future.date() == now.date():
        assert j["daily_report_last"] == (now.date() - timedelta(days=1)).isoformat()
        assert j["daily_report_next"].startswith("aaj ")
    past = (now - timedelta(minutes=30))
    if past.date() == now.date():
        requests.post(f"{BASE}/api/settings", json={"daily_report_time": past.strftime("%H:%M")}, headers=H, timeout=10)
        j = requests.get(f"{BASE}/api/settings", headers=H, timeout=10).json()
        assert j["daily_report_last"] == now.date().isoformat()
        assert j["daily_report_next"].startswith("kal ")
    requests.post(f"{BASE}/api/settings", json={"daily_report_enabled": False}, headers=H, timeout=10)
    assert requests.get(f"{BASE}/api/settings", headers=H, timeout=10).json()["daily_report_next"] == ""


def test_settings_pr_roundtrip_and_restore(H):
    r = requests.post(f"{BASE}/api/settings", json={"pr_enabled": True, "pr_api_key": "  FAKEKEY123 \n", "pr_mode": "bogus"}, headers=H, timeout=10)
    assert r.status_code == 200
    j = requests.get(f"{BASE}/api/settings", headers=H, timeout=10).json()
    assert j["pr_enabled"] is True and j["pr_api_key"] == "FAKEKEY123" and j["pr_api_key_set"] is True and j["pr_mode"] == "always"
    requests.post(f"{BASE}/api/settings", json={"pr_mode": "fallback"}, headers=H, timeout=10)
    assert requests.get(f"{BASE}/api/settings", headers=H, timeout=10).json()["pr_mode"] == "fallback"
    requests.post(f"{BASE}/api/settings", json={"pr_enabled": False, "pr_api_key": "", "pr_mode": "always"}, headers=H, timeout=10)
    j = requests.get(f"{BASE}/api/settings", headers=H, timeout=10).json()
    assert j["pr_enabled"] is False and j["pr_api_key"] == "" and j["pr_api_key_set"] is False and j["pr_mode"] == "always"


# ---- plate recognizer key test (FAKE key only)
def test_plate_cloud_fake_key(H):
    j = requests.post(f"{BASE}/api/plate/cloud/test", json={"pr_api_key": "FAKEKEY123"}, headers=H, timeout=30).json()
    assert j["valid"] is False
    assert j["message"].startswith("HTTP 403: API key galat hai"), j


def test_plate_cloud_empty_key(H):
    j = requests.post(f"{BASE}/api/plate/cloud/test", json={}, headers=H, timeout=30).json()
    assert j == {"valid": False, "message": "API key khaali hai"}


def test_plate_cloud_unauth():
    assert requests.post(f"{BASE}/api/plate/cloud/test", json={}, timeout=10).status_code == 401


# ---- unit edges
def test_next_run_and_due_days_edges():
    now = datetime(2026, 10, 18, 20, 0)
    cfg = {"daily_report_enabled": True, "daily_report_time": "21:00", "daily_report_last": "2026-10-17"}
    assert report.next_run(cfg, now) == "aaj 09:00 PM"
    assert report.due_days(cfg, now) == []
    assert report.due_days(cfg, datetime(2026, 10, 18, 21, 0)) == ["2026-10-18"]
    cfg["daily_report_last"] = "2026-10-15"  # PC off for days: yesterday caught up, today when due
    assert report.due_days(cfg, datetime(2026, 10, 18, 22, 0)) == ["2026-10-17", "2026-10-18"]
    assert report.due_days({**cfg, "daily_report_time": "25:99"}, now) == []


def test_mark_baseline_invalid_hour_does_not_raise():
    cfg = {"daily_report_time": "25:99"}
    report.mark_baseline(cfg, datetime(2026, 10, 18, 20, 0))  # should not raise ValueError
