"""9x Security - gentle gate chimes (lone person / vehicle / two-wheeler).

Played from the ENGINE (Windows winsound) so they work while the UI is minimised, in the
tray or locked. Each tone is synthesised on the fly (soft sine notes with a decay), no audio
asset needed. On non-Windows hosts the file is generated but nothing plays."""
import os
import threading
import time
import wave

import numpy as np

import config

try:  # stdlib on Windows only
    import winsound
except ImportError:  # pragma: no cover - Linux/macOS dev hosts
    winsound = None

MIN_GAP_S = 3.0          # per category: never more than one chime per 3 s
SAMPLE_RATE = 22050
# category -> (notes [(hz, start_s)], decay, duration_s): distinct, instantly recognisable tones
TONES = {
    "person": (((659.25, 0.0), (783.99, 0.35)), 3.2, 1.4),      # E5-G5 soft 'ding-dong'
    "vehicle": (((392.00, 0.0), (329.63, 0.45)), 2.4, 1.7),     # G4-E4 deeper 'bong-bong'
    "two_wheeler": (((880.00, 0.0), (880.00, 0.2)), 7.0, 0.8),  # A5 quick bright 'ti-ti'
}
CATEGORY_KEYS = {"person": "person_chime_enabled", "vehicle": "vehicle_chime_enabled",
                 "two_wheeler": "two_wheeler_chime_enabled"}
WAV_PATH = os.path.join(config.BASE_DIR, "person_chime.wav")  # legacy name (person tone)

_lock = threading.Lock()
_last_play = {}   # category -> ts
_built = {}       # category -> volume the file was built with


def wav_path(category="person"):
    return WAV_PATH if category == "person" else os.path.join(config.BASE_DIR, f"chime_{category}.wav")


def build_wav(volume=70, path=None, category="person"):
    """Write the chime WAV (mono 16-bit). Amplitude follows `volume` 0-100."""
    path = path or wav_path(category)
    notes, decay, dur = TONES.get(category, TONES["person"])
    vol = max(0, min(100, int(volume))) / 100.0
    n = int(SAMPLE_RATE * dur)
    t_all = np.arange(n) / SAMPLE_RATE
    mix = np.zeros(n, dtype=np.float64)
    for freq, start in notes:
        t = t_all - start
        active = t >= 0
        tt = np.where(active, t, 0.0)
        env = np.exp(-decay * tt) * (1.0 - np.exp(-180.0 * tt))  # soft attack, gentle decay
        mix += np.where(active, np.sin(2 * np.pi * freq * tt) * env, 0.0)
    peak = float(np.max(np.abs(mix))) or 1.0
    pcm = (mix / peak * vol * 0.6 * 32767).astype("<i2")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
    return path


def _ensure_wav(volume, category):
    path = wav_path(category)
    with _lock:
        if _built.get(category) != int(volume) or not os.path.exists(path):
            build_wav(volume, path, category)
            _built[category] = int(volume)
    return path


def play(volume=70, category="person"):
    """Play once (async). Returns (supported, detail)."""
    if category not in TONES:
        category = "person"
    try:
        path = _ensure_wav(volume, category)
    except Exception as e:
        return False, f"chime WAV nahi ban payi: {e}"
    if winsound is None:
        return False, "Sound sirf Windows PC par bajti hai (yahan sirf WAV file bani)"
    try:
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        return True, "Chime baj gayi"
    except Exception as e:  # pragma: no cover - audio device issues
        return False, f"Sound play fail: {e}"


def category_enabled(cfg, category):
    key = CATEGORY_KEYS.get(category)
    return bool(key) and bool(cfg.get(key, config.DEFAULTS.get(key, False)))


def allowed_now(cfg, now=None, category="person"):
    if not category_enabled(cfg, category):
        return False
    if cfg.get("person_chime_schedule_enabled", True):
        return config.in_time_window(cfg.get("person_chime_start", "18:00"),
                                     cfg.get("person_chime_end", "06:00"), now=now)
    return True


def maybe_play(cfg, ev, now=None):
    """Chime for an event's category when enabled + inside the shared window; rate-limited per category."""
    category = (ev or {}).get("category") or ""
    if category not in TONES or not allowed_now(cfg, now=now, category=category):
        return False
    t = time.time()
    if t - _last_play.get(category, 0.0) < MIN_GAP_S:
        return False
    _last_play[category] = t
    threading.Thread(target=play, args=(cfg.get("person_chime_volume", 70), category), daemon=True).start()
    return True
