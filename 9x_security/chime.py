"""9x Security - gentle 'lone person crossed' chime.

Plays from the ENGINE (Windows winsound) so it works while the UI is minimised, in the
tray or locked. The WAV is synthesised on the fly (two soft sine notes with a decay),
no audio asset needed. On non-Windows hosts the file is generated but nothing plays."""
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

WAV_PATH = os.path.join(config.BASE_DIR, "person_chime.wav")
MIN_GAP_S = 3.0          # never more than one chime per 3 s (a group = one event anyway)
SAMPLE_RATE = 22050
NOTES = ((659.25, 0.0), (783.99, 0.35))  # E5 then G5 — a soft 'ding-dong'

_lock = threading.Lock()
_last_play = 0.0
_built_volume = None


def build_wav(volume=70, path=None):
    """Write the chime WAV (mono 16-bit). Amplitude follows `volume` 0-100."""
    path = path or WAV_PATH
    vol = max(0, min(100, int(volume))) / 100.0
    n = int(SAMPLE_RATE * 1.4)
    t_all = np.arange(n) / SAMPLE_RATE
    mix = np.zeros(n, dtype=np.float64)
    for freq, start in NOTES:
        t = t_all - start
        active = t >= 0
        tt = np.where(active, t, 0.0)
        env = np.exp(-3.2 * tt) * (1.0 - np.exp(-180.0 * tt))  # soft attack, gentle decay
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


def _ensure_wav(volume):
    global _built_volume
    with _lock:
        if _built_volume != int(volume) or not os.path.exists(WAV_PATH):
            build_wav(volume)
            _built_volume = int(volume)
    return WAV_PATH


def play(volume=70):
    """Play once (async). Returns (supported, detail)."""
    try:
        path = _ensure_wav(volume)
    except Exception as e:
        return False, f"chime WAV nahi ban payi: {e}"
    if winsound is None:
        return False, "Sound sirf Windows PC par bajti hai (yahan sirf WAV file bani)"
    try:
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        return True, "Chime baj gayi"
    except Exception as e:  # pragma: no cover - audio device issues
        return False, f"Sound play fail: {e}"


def allowed_now(cfg, now=None):
    if not cfg.get("person_chime_enabled", True):
        return False
    if cfg.get("person_chime_schedule_enabled", True):
        return config.in_time_window(cfg.get("person_chime_start", "18:00"),
                                     cfg.get("person_chime_end", "06:00"), now=now)
    return True


def maybe_play(cfg, ev, now=None):
    """Chime for a LONE-PERSON event when enabled + inside the window; rate-limited."""
    global _last_play
    if (ev or {}).get("category") != "person" or not allowed_now(cfg, now=now):
        return False
    t = time.time()
    if t - _last_play < MIN_GAP_S:
        return False
    _last_play = t
    threading.Thread(target=play, args=(cfg.get("person_chime_volume", 70),), daemon=True).start()
    return True
