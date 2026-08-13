"""9x Security - Configuration handling (persisted to config.json)."""
import json
import os

APP_NAME = "9x Security"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SNAPSHOT_DIR = os.path.join(BASE_DIR, "snapshots")
DB_PATH = os.path.join(BASE_DIR, "events.db")
MODEL_PATH = os.path.join(BASE_DIR, "yolov8n.pt")

# Processing / display resolution (16:9). Detection & line are handled here.
DISPLAY_WIDTH = 960
DISPLAY_HEIGHT = 540

DEFAULTS = {
    "rtsp_url": "",
    # Detection line stored in normalized coordinates (0..1) => resolution independent
    "line": {"x1": 0.1, "y1": 0.5, "x2": 0.9, "y2": 0.5},
    # Which crossing direction counts as ENTRY.
    # cross product sign flips from negative->positive = "pos", positive->negative = "neg"
    "entry_direction": "pos",
    "confidence": 0.40,
    "detect_frame_skip": 2,          # run detector every N frames (CPU friendly)
    "enable_plate": True,
    "vehicle_classes": ["car", "truck", "bus"],
}


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            cfg.update(saved)
        except Exception:
            pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        return True
    except Exception:
        return False


os.makedirs(SNAPSHOT_DIR, exist_ok=True)
