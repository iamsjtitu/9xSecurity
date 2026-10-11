"""Iter35 — Second (Person/Bike) detection line.
Covers: API (/api/line2, /api/swap2, /api/swap, /api/state), per-camera isolation,
and extra scripted-engine scenarios on top of test_two_lines.py."""
import os
import sys
import time
import requests
import pytest

BASE = "http://127.0.0.1:8971"
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.dirname(HERE))  # /app/9x_security
import config  # noqa: E402

LINE_V = {"x1": 0.05, "y1": 0.5, "x2": 0.45, "y2": 0.5}
LINE_P = {"x1": 0.55, "y1": 0.5, "x2": 0.95, "y2": 0.5}


@pytest.fixture(scope="module")
def tok():
    r = requests.post(f"{BASE}/api/login", json={"username": "admin", "password": "9xsecurity"}, timeout=5)
    r.raise_for_status()
    return r.json()["token"]


def H(tok):
    return {"X-Auth-Token": tok}


def _state(tok):
    return requests.get(f"{BASE}/api/state", headers=H(tok), timeout=5).json()


def _diag(tok):
    return requests.get(f"{BASE}/api/diagnostics", headers=H(tok), timeout=5).json()


# --- Baseline capture / restore -----------------------------------------
@pytest.fixture(scope="module", autouse=True)
def restore_baseline(tok):
    s0 = _state(tok)
    original_active = s0.get("active_camera_id") or "cam1"
    original_line2 = s0.get("line2")
    original_dir2 = s0.get("entry_direction2")
    yield
    # cleanup: restore active camera + clear any line2 we set
    requests.post(f"{BASE}/api/cameras/{original_active}/activate", headers=H(tok), timeout=5)
    if original_line2:
        body = {**original_line2}
        requests.post(f"{BASE}/api/line2", headers=H(tok), json=body, timeout=5)
    else:
        requests.post(f"{BASE}/api/line2", headers=H(tok), json={"clear": True}, timeout=5)
    _ = original_dir2  # noqa


# ============== API tests ==============================================
def test_set_line2_returns_ok_and_inferred_direction(tok):
    # ensure baseline main line
    requests.post(f"{BASE}/api/line", headers=H(tok), json=LINE_V, timeout=5)
    requests.post(f"{BASE}/api/options", headers=H(tok),
                  json={"entry_direction": "pos"}, timeout=5)
    r = requests.post(f"{BASE}/api/line2", headers=H(tok), json=LINE_P, timeout=5)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["line2"]["x1"] == pytest.approx(0.55)
    assert data["entry_direction2"] == "pos"  # inferred from main direction (same orientation)
    # state reflects it
    s = _state(tok)
    assert s["line2"] is not None
    assert s["entry_direction2"] == "pos"


def test_set_line2_backwards_flips_inferred_direction(tok):
    rev = {"x1": 0.95, "y1": 0.5, "x2": 0.55, "y2": 0.5}
    r = requests.post(f"{BASE}/api/line2", headers=H(tok), json=rev, timeout=5)
    assert r.status_code == 200
    assert r.json()["entry_direction2"] == "neg"


def test_swap2_flips_only_person_direction(tok):
    # set line2 same-direction => entry_direction2 pos
    requests.post(f"{BASE}/api/line2", headers=H(tok), json=LINE_P, timeout=5)
    s_before = _state(tok)
    main_before = s_before["entry_direction"]
    r = requests.post(f"{BASE}/api/swap2", headers=H(tok), timeout=5)
    assert r.status_code == 200
    assert r.json()["entry_direction2"] == "neg"
    s = _state(tok)
    assert s["entry_direction"] == main_before   # main unchanged
    assert s["entry_direction2"] == "neg"


def test_swap_flips_both_lines_when_line2_exists(tok):
    # ensure line2 is set
    requests.post(f"{BASE}/api/line2", headers=H(tok), json=LINE_P, timeout=5)
    s_before = _state(tok)
    e, e2 = s_before["entry_direction"], s_before["entry_direction2"]
    r = requests.post(f"{BASE}/api/swap", headers=H(tok), timeout=5)
    assert r.status_code == 200
    s_after = _state(tok)
    assert s_after["entry_direction"] != e
    assert s_after["entry_direction2"] != e2


def test_clear_line2(tok):
    r = requests.post(f"{BASE}/api/line2", headers=H(tok), json={"clear": True}, timeout=5)
    assert r.status_code == 200
    assert r.json()["line2"] is None
    s = _state(tok)
    assert s["line2"] is None


def test_swap2_requires_line2(tok):
    requests.post(f"{BASE}/api/line2", headers=H(tok), json={"clear": True}, timeout=5)
    r = requests.post(f"{BASE}/api/swap2", headers=H(tok), timeout=5)
    assert r.status_code == 400
    body = r.json()
    assert "Person/Bike line" in (body.get("detail") or "") or "line banayein" in (body.get("detail") or "")


def test_bad_body_line2_returns_400(tok):
    r = requests.post(f"{BASE}/api/line2", headers=H(tok), json={"x1": "nope"}, timeout=5)
    assert r.status_code == 400


def test_diagnostics_exposes_line2_and_tracks_now(tok):
    requests.post(f"{BASE}/api/line2", headers=H(tok), json=LINE_P, timeout=5)
    d = _diag(tok)
    eng = d.get("engine") or {}
    assert "line2" in eng
    assert eng["line2"] is not None
    assert "entry_direction2" in eng
    assert "tracks_now" in eng
    # cleanup
    requests.post(f"{BASE}/api/line2", headers=H(tok), json={"clear": True}, timeout=5)


# ============== Per-camera isolation ====================================
def test_line2_is_per_camera_isolated(tok):
    # ensure cam1 active
    cams = requests.get(f"{BASE}/api/cameras", headers=H(tok), timeout=5).json().get("cameras", [])
    ids = [c["id"] for c in cams]
    assert "cam1" in ids and "cam2" in ids
    requests.post(f"{BASE}/api/cameras/cam1/activate", headers=H(tok), timeout=5)
    requests.post(f"{BASE}/api/line", headers=H(tok), json=LINE_V, timeout=5)
    requests.post(f"{BASE}/api/line2", headers=H(tok), json=LINE_P, timeout=5)
    time.sleep(0.4)

    # cfg file reflects per-camera line2 for cam1 (and NOT cam2)
    cfg = config.load_config()
    cam1 = next(c for c in cfg["cameras"] if c["id"] == "cam1")
    cam2 = next(c for c in cfg["cameras"] if c["id"] == "cam2")
    assert cam1.get("line2") is not None
    assert cam2.get("line2") in (None, {})

    # switch active to cam2 => its own line2 (None) shows in /api/state
    requests.post(f"{BASE}/api/cameras/cam2/activate", headers=H(tok), timeout=5)
    time.sleep(0.4)
    s = _state(tok)
    assert s["line2"] is None

    # switch back to cam1 => line2 reappears
    requests.post(f"{BASE}/api/cameras/cam1/activate", headers=H(tok), timeout=5)
    time.sleep(0.4)
    s = _state(tok)
    assert s["line2"] is not None

    # cleanup
    requests.post(f"{BASE}/api/line2", headers=H(tok), json={"clear": True}, timeout=5)


# ============== Extra scripted-engine edge scenarios ====================
# These extend test_two_lines.py with user-facing combinations.
import importlib  # noqa: E402

tc = importlib.import_module("test_categories")
tl = importlib.import_module("test_two_lines")


def test_person_and_car_cross_their_own_lines_simultaneously(tmp_path):
    """Each tracker fires: 1 vehicle + 1 person. Never doubled across lines."""
    e = tl._eng(tmp_path)
    car = tl._down("car", tl.XV, 120, 80)
    man = tl._down("person", tl.XP, 40, 100)
    ev = tc._drive(e, [c + m for c, m in zip(car, man)])
    cats = sorted(x["category"] for x in ev)
    assert cats == ["person", "vehicle"], ev
    tl._cleanup(ev)


def test_motorcycle_on_main_only_and_car_on_line2_only_both_ignored(tmp_path):
    """Motorcycle crosses only MAIN line (not line2) => 0 events.
    Car crosses only line2 (not main) => 0 events."""
    e = tl._eng(tmp_path)
    ev = tc._drive(e, tl._down("motorcycle", tl.XV, 70, 60))
    assert ev == [], ev
    e2 = tl._eng(tmp_path)
    ev2 = tc._drive(e2, tl._down("car", tl.XP, 120, 80))
    assert ev2 == [], ev2


def test_line2_drawn_backwards_same_entry_as_car(tmp_path):
    """Line2 drawn right->left on the right half gets entry_direction2 inferred so an object
    crossing TOP->BOTTOM is still labeled 'Entry' (matches the car on the main line)."""
    rev = {"x1": 0.95, "y1": 0.5, "x2": 0.55, "y2": 0.5}
    cfg = {"line": LINE_V, "entry_direction": "pos", "line2": rev}
    inferred = config.infer_entry_direction2(cfg)
    e = tc._engine(tmp_path, line=LINE_V, line2=rev, entry_direction2=inferred,
                   enable_person=True, enable_two_wheeler=True)
    ev = tc._drive(e, tl._down("person", tl.XP, 40, 100))
    assert len(ev) == 1 and ev[0]["direction"] == "Entry", ev
    tl._cleanup(ev)


def test_swap2_only_flips_person_not_car(tmp_path):
    """swap2 flips only the person/bike line direction. Car (main line) label unchanged."""
    e = tl._eng(tmp_path, entry_direction="pos", entry_direction2="neg")
    car = tl._down("car", tl.XV, 120, 80)
    man = tl._down("person", tl.XP, 40, 100)
    ev = tc._drive(e, [c + m for c, m in zip(car, man)])
    dmap = {x["category"]: x["direction"] for x in ev}
    assert dmap.get("vehicle") == "Entry"
    assert dmap.get("person") == "Exit"
    tl._cleanup(ev)


def test_bicycle_counts_only_on_line2(tmp_path):
    """Bicycle is a two_wheeler; must respect line2 just like motorcycle."""
    e = tl._eng(tmp_path)
    ev = tc._drive(e, tl._down("bicycle", tl.XP, 70, 60))
    cats = [x["category"] for x in ev]
    assert cats == ["two_wheeler"], ev
    tl._cleanup(ev)
    e2 = tl._eng(tmp_path)
    assert tc._drive(e2, tl._down("bicycle", tl.XV, 70, 60)) == []


def test_truck_and_bus_on_main_line_only(tmp_path):
    for lbl in ("truck", "bus"):
        e = tl._eng(tmp_path)
        ev = tc._drive(e, tl._down(lbl, tl.XV, 160, 100))
        assert [x["category"] for x in ev] == ["vehicle"], (lbl, ev)
        tl._cleanup(ev)
        e2 = tl._eng(tmp_path)
        assert tc._drive(e2, tl._down(lbl, tl.XP, 160, 100)) == [], lbl
