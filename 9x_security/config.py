"""9x Security - Configuration handling (persisted to config.json)."""
import json
import os
import sys

APP_NAME = "9x Security"
if getattr(sys, "frozen", False):
    # PyInstaller build: user data lives next to the exe (survives updates);
    # bundled resources (model) live in _internal (sys._MEIPASS).
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
    _RES_DIR = getattr(sys, "_MEIPASS", BASE_DIR)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    _RES_DIR = BASE_DIR
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SNAPSHOT_DIR = os.path.join(BASE_DIR, "snapshots")
DB_PATH = os.path.join(BASE_DIR, "events.db")
MODEL_PATH = os.path.join(_RES_DIR, "yolov8n.pt")

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
    # ---- WhatsApp (wa.9x.design) alerts ----
    "wa_enabled": False,
    "wa_base_url": "https://wa.9x.design",
    "wa_api_key": "",
    "wa_recipients": [],          # ["919876543210", ...]
    "wa_send_image": True,        # False => text-only alert
    # ---- Timing / schedule ----
    "wa_schedule_enabled": False,     # True => WhatsApp alerts only between wa_start-wa_end
    "wa_start": "18:00",
    "wa_end": "06:00",
    "capture_schedule_enabled": False,  # True => detection/capture only in window (video always on)
    "capture_start": "18:00",
    "capture_end": "06:00",
    # ---- Storage ----
    "auto_delete_enabled": True,   # auto-delete old events + snapshots
    "retention_days": 7,   # events + snapshots older than this are auto-deleted
    # ---- wa.9x.design account credentials (stored for reference) ----
    "wa_account_email": "",
    "wa_account_password": "",
    # ---- App login (local, PBKDF2 hashed) ----
    "auth_user": "admin",
    "auth_salt": "",
    "auth_hash": "",
    # ---- Auto-update (GitHub Releases) ----
    "github_repo": "",
    "gh_token": "",
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


def in_time_window(start, end, now=None):
    """'HH:MM' strings. start > end means an overnight window (e.g. 18:00-06:00).
    Equal start/end means always on."""
    from datetime import datetime as _dt

    try:
        t = now or _dt.now()
        cur = t.hour * 60 + t.minute
        sh, sm = (int(x) for x in str(start).split(":"))
        eh, em = (int(x) for x in str(end).split(":"))
        s, e = sh * 60 + sm, eh * 60 + em
    except Exception:
        return True
    if s == e:
        return True
    if s < e:
        return s <= cur < e
    return cur >= s or cur < e


def ensure_std_streams():
    """PyInstaller --windowed builds have no console: stdout/stderr are None and
    any library print/log (torch/ultralytics/tqdm) crashes the thread.
    Redirect them to app_log.txt so the app never dies from a print."""
    if not getattr(sys, "frozen", False):
        return
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        log = open(os.path.join(BASE_DIR, "app_log.txt"), "a", buffering=1,
                   encoding="utf-8", errors="replace")
    except Exception:
        log = open(os.devnull, "w")
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


ensure_std_streams()
os.makedirs(SNAPSHOT_DIR, exist_ok=True)
