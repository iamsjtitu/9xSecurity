"""9x Security - Configuration handling (persisted to config.json)."""
import json
import os
import sys

APP_NAME = "9x Security"
if getattr(sys, "frozen", False):
    # PyInstaller build: bundled resources (model) live in _internal (sys._MEIPASS).
    # User data lives OUTSIDE the install folder — the NSIS updater wipes the whole
    # install dir on every update (config/events/snapshots were lost each update).
    _EXE_DIR = os.path.dirname(os.path.abspath(sys.executable))
    _RES_DIR = getattr(sys, "_MEIPASS", _EXE_DIR)
    _LOCAL = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    BASE_DIR = os.environ.get("NX_DATA_DIR") or os.path.join(_LOCAL, "9xSecurity")
else:
    _EXE_DIR = os.path.dirname(os.path.abspath(__file__))
    _RES_DIR = _EXE_DIR
    BASE_DIR = os.environ.get("NX_DATA_DIR") or _EXE_DIR
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SNAPSHOT_DIR = os.path.join(BASE_DIR, "snapshots")
DB_PATH = os.path.join(BASE_DIR, "events.db")
MODEL_PATH = os.path.join(_RES_DIR, "yolov8n.pt")
_USER_FILES = ("config.json", "events.db", "snapshots", "wa_log.txt", "camera_log.txt", "app_log.txt")


def migrate_legacy_data(src_dir=None, dst_dir=None):
    """One-time move of user data from the old location (next to the exe) to the
    stable data dir. Copies only files that don't exist at the destination yet."""
    import shutil

    src_dir = src_dir or _EXE_DIR
    dst_dir = dst_dir or BASE_DIR
    if os.path.normcase(os.path.abspath(src_dir)) == os.path.normcase(os.path.abspath(dst_dir)):
        return []
    moved = []
    for name in _USER_FILES:
        s, d = os.path.join(src_dir, name), os.path.join(dst_dir, name)
        if not os.path.exists(s) or os.path.exists(d):
            continue
        try:
            if os.path.isdir(s):
                shutil.copytree(s, d)
            else:
                shutil.copy2(s, d)
            moved.append(name)
        except Exception:
            pass
    return moved

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
    "confidence": 0.35,
    "detector_model": "auto",        # auto | fast (yolov8n) | accurate (yolov8s)
    "detect_frame_skip": 2,          # run detector every N frames (CPU friendly)
    "enable_plate": True,
    "vehicle_classes": ["car", "truck", "bus"],
    "enable_person": False,        # tick => person Entry/Exit alerts
    "enable_two_wheeler": False,   # tick => motorcycle/bicycle Entry/Exit alerts
    # Per-category time window. enabled + outside window => that category is NOT counted at all.
    "cat_schedules": {
        "vehicle": {"enabled": False, "start": "00:00", "end": "23:59"},
        "person": {"enabled": False, "start": "00:00", "end": "23:59"},
        "two_wheeler": {"enabled": False, "start": "00:00", "end": "23:59"},
    },
    # ---- WhatsApp (wa.9x.design) alerts ----
    "wa_enabled": False,
    "wa_base_url": "https://wa.9x.design",
    "wa_api_key": "",
    "wa_recipients": [],          # ["919876543210", ...]
    "wa_groups": [],              # [{"id": "120363...@g.us", "name": "Gate Staff"}, ...]
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
    "ignore_zones": [],  # [{x1,y1,x2,y2} normalized] parked-vehicle areas never counted
    # ---- Saved cameras (one ACTIVE at a time — light on the PC). The top-level rtsp_url/line/
    # entry_direction/ignore_zones always belong to the active camera; sync_cameras() mirrors them.
    "cameras": [],            # [{id, name, gate, rtsp_url, rtsp_url_main, line, entry_direction, ignore_zones}]
    "active_camera_id": "",
    "auto_connect": True,  # engine connects the saved camera by itself at start (PC reboot) and retries
    "setup_done": False,  # first-run Setup Wizard finished/skipped (existing installs with a camera URL count as done)
    "auto_lock_minutes": 10,  # UI locks (login screen) after this idle time; 0 = never. Engine keeps running.
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
    return sync_cameras(cfg)


def save_config(cfg):
    try:
        sync_cameras(cfg)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        return True
    except Exception:
        return False


# ---- saved cameras ---------------------------------------------------------
CAMERA_KEYS = ("rtsp_url", "rtsp_url_main", "line", "entry_direction", "ignore_zones")
MAX_CAMERAS = 8


def _copy(v):
    return json.loads(json.dumps(v))


def active_camera(cfg):
    aid = cfg.get("active_camera_id")
    for c in cfg.get("cameras") or []:
        if isinstance(c, dict) and c.get("id") == aid:
            return c
    return None


def new_camera_id(cfg):
    ids = {c.get("id") for c in cfg.get("cameras") or []}
    n = 1
    while f"cam{n}" in ids:
        n += 1
    return f"cam{n}"


def _store_from_top(cfg, cam):
    for k in CAMERA_KEYS:
        cam[k] = _copy(cfg.get(k, DEFAULTS.get(k, "")))


def _load_into_top(cfg, cam):
    for k in CAMERA_KEYS:
        cfg[k] = _copy(cam.get(k, DEFAULTS.get(k, "")))


def sync_cameras(cfg):
    """Camera list <-> top-level (active camera) settings. Active camera present: the
    top-level values are the truth and get stored into its entry. No valid active camera:
    adopt the first saved camera. No cameras but a URL (old installs): create 'Camera 1'."""
    cams = [c for c in (cfg.get("cameras") or []) if isinstance(c, dict) and c.get("id")]
    cfg["cameras"] = cams
    if not cams:
        if cfg.get("rtsp_url"):
            cam = {"id": "cam1", "name": "Camera 1", "gate": ""}
            _store_from_top(cfg, cam)
            cams.append(cam)
            cfg["active_camera_id"] = "cam1"
        else:
            cfg["active_camera_id"] = ""
        return cfg
    cam = active_camera(cfg)
    if cam is None:
        cam = cams[0]
        cfg["active_camera_id"] = cam["id"]
        _load_into_top(cfg, cam)
    else:
        _store_from_top(cfg, cam)
    return cfg


def activate_camera(cfg, cam_id):
    """Make cam_id the active camera: its URL/line/zones become the live top-level settings."""
    sync_cameras(cfg)
    cam = next((c for c in cfg["cameras"] if c.get("id") == cam_id), None)
    if cam is None:
        raise ValueError("camera not found")
    cfg["active_camera_id"] = cam_id
    _load_into_top(cfg, cam)
    return cam


def gate_name(cfg):
    cam = active_camera(cfg)
    return str((cam or {}).get("gate") or "").strip()


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


CATEGORIES = ("vehicle", "person", "two_wheeler")


def allowed_classes(cfg):
    """COCO labels that may produce an ALERT, from the category toggles."""
    allowed = [c for c in (cfg.get("vehicle_classes") or []) if c in ("car", "truck", "bus")]
    if cfg.get("enable_two_wheeler"):
        allowed += ["motorcycle", "bicycle"]
    if cfg.get("enable_person"):
        allowed += ["person"]
    return allowed


def detect_classes(cfg):
    """Labels the detector looks for: alert classes + context. With Person ON, vehicles and
    two-wheelers are always detected (even when their alerts are off) so a rider/driver is
    recognised as 'person WITH a vehicle' and never becomes a lone-person alert."""
    classes = allowed_classes(cfg)
    if cfg.get("enable_person"):
        classes += [c for c in ("car", "truck", "bus", "motorcycle", "bicycle") if c not in classes]
    return classes


def category_counts_now(cfg, category, now=None):
    """Per-category schedule gate: True => this category may be counted right now."""
    sch = (cfg.get("cat_schedules") or {}).get(category) or {}
    if not sch.get("enabled"):
        return True
    return in_time_window(sch.get("start", "00:00"), sch.get("end", "23:59"), now=now)


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


os.makedirs(BASE_DIR, exist_ok=True)
if getattr(sys, "frozen", False):
    migrate_legacy_data()
ensure_std_streams()
os.makedirs(SNAPSHOT_DIR, exist_ok=True)
