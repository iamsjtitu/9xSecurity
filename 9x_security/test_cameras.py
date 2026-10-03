"""Part 2 of the USER (12-09) request: several SAVED cameras, ONE active at a time (dropdown),
Gate Name per camera (goes into the WhatsApp caption + event row). Line / ignore zones /
entry direction are remembered PER camera."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import config  # noqa: E402
import database  # noqa: E402
from whatsapp import WhatsAppNotifier  # noqa: E402


def _cfg(**over):
    cfg = {**config.DEFAULTS, "line": dict(config.DEFAULTS["line"]), "cameras": [], "ignore_zones": [], **over}
    return cfg


def test_old_install_migrates_to_camera_1():
    cfg = config.sync_cameras(_cfg(rtsp_url="rtsp://a/1", line={"x1": 0.2, "y1": 0.3, "x2": 0.8, "y2": 0.3}))
    assert cfg["active_camera_id"] == "cam1"
    cam = config.active_camera(cfg)
    assert cam["name"] == "Camera 1" and cam["rtsp_url"] == "rtsp://a/1" and cam["line"]["x1"] == 0.2
    assert config.gate_name(cfg) == ""
    # no url + no cameras => nothing created
    assert config.sync_cameras(_cfg())["cameras"] == [] and config.sync_cameras(_cfg())["active_camera_id"] == ""


def test_top_level_edits_are_stored_in_active_camera():
    cfg = config.sync_cameras(_cfg(rtsp_url="rtsp://a/1"))
    cfg["line"] = {"x1": 0.1, "y1": 0.9, "x2": 0.9, "y2": 0.9}
    cfg["ignore_zones"] = [{"x1": 0, "y1": 0, "x2": 0.2, "y2": 0.2}]
    cfg["entry_direction"] = "neg"
    config.sync_cameras(cfg)  # what save_config does
    cam = config.active_camera(cfg)
    assert cam["line"]["y1"] == 0.9 and len(cam["ignore_zones"]) == 1 and cam["entry_direction"] == "neg"


def test_activate_keeps_each_cameras_line_and_zones():
    cfg = config.sync_cameras(_cfg(rtsp_url="rtsp://a/1", line={"x1": 0.1, "y1": 0.2, "x2": 0.9, "y2": 0.2}))
    cfg["cameras"].append({"id": "cam2", "name": "Back", "gate": "Back Gate", "rtsp_url": "rtsp://b/2",
                           "rtsp_url_main": "", "line": {"x1": 0.1, "y1": 0.7, "x2": 0.9, "y2": 0.7},
                           "entry_direction": "neg", "ignore_zones": [{"x1": 0, "y1": 0, "x2": 0.3, "y2": 0.3}]})
    cam = config.activate_camera(cfg, "cam2")
    assert cam["name"] == "Back"
    assert cfg["rtsp_url"] == "rtsp://b/2" and cfg["line"]["y1"] == 0.7 and cfg["entry_direction"] == "neg"
    assert len(cfg["ignore_zones"]) == 1 and config.gate_name(cfg) == "Back Gate"
    cfg["line"]["y1"] = 0.75  # user re-draws the line on camera 2
    config.activate_camera(cfg, "cam1")  # back to camera 1: its own line returns
    assert cfg["rtsp_url"] == "rtsp://a/1" and cfg["line"]["y1"] == 0.2 and cfg["ignore_zones"] == []
    assert next(c for c in cfg["cameras"] if c["id"] == "cam2")["line"]["y1"] == 0.75
    import pytest
    with pytest.raises(ValueError):
        config.activate_camera(cfg, "nope")


def test_removed_active_camera_falls_back_to_first_saved():
    cfg = config.sync_cameras(_cfg(rtsp_url="rtsp://a/1"))
    cfg["cameras"].append({"id": "cam2", "name": "Back", "gate": "", "rtsp_url": "rtsp://b/2", "rtsp_url_main": "",
                           "line": dict(config.DEFAULTS["line"]), "entry_direction": "pos", "ignore_zones": []})
    config.activate_camera(cfg, "cam2")
    cfg["cameras"] = [c for c in cfg["cameras"] if c["id"] != "cam2"]
    cfg["active_camera_id"] = ""
    config.sync_cameras(cfg)
    assert cfg["active_camera_id"] == "cam1" and cfg["rtsp_url"] == "rtsp://a/1"


def test_new_camera_id_and_gate_in_db_and_caption(tmp_path):
    cfg = config.sync_cameras(_cfg(rtsp_url="rtsp://a/1"))
    assert config.new_camera_id(cfg) == "cam2"
    db = database.EventDB(db_path=str(tmp_path / "g.db"))
    eid = db.add_event("car", "Entry", "", "", gate="Main Gate")
    row = db.get_events()[0]
    assert row["id"] == eid and row["gate"] == "Main Gate" and row["count"] == 1
    n = WhatsAppNotifier({"wa_enabled": True, "wa_base_url": "x", "wa_api_key": "k", "wa_recipients": ["919"]})
    cap = n._caption({"category": "vehicle", "direction": "Entry", "vehicle_type": "car",
                      "gate": "Main Gate", "timestamp": "2026-09-12T15:14:09"})
    assert cap.splitlines()[-1] == "Gate: Main Gate" and "Number" not in cap
