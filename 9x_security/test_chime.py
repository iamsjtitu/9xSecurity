"""Lone-person chime: synthesised WAV, night window gating, rate limit, worker hook, API."""
import os
import sys
import wave
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import chime  # noqa: E402
import config  # noqa: E402


def _cfg(**over):
    return {**config.DEFAULTS, **over}


def test_build_wav_is_valid_and_volume_scales(tmp_path):
    import numpy as np
    loud = chime.build_wav(100, str(tmp_path / "a.wav"))
    quiet = chime.build_wav(20, str(tmp_path / "b.wav"))
    with wave.open(loud) as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == chime.SAMPLE_RATE
        n = w.getnframes()
        assert 1.2 < n / w.getframerate() < 1.6
        pa = np.abs(np.frombuffer(w.readframes(n), dtype="<i2")).max()
    with wave.open(quiet) as w:
        pb = np.abs(np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")).max()
    assert pa > 15000 and 0.15 < pb / pa < 0.25  # 20% volume ~ 1/5 amplitude
    silent = chime.build_wav(0, str(tmp_path / "c.wav"))
    with wave.open(silent) as w:
        assert np.abs(np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")).max() == 0


def test_allowed_now_gating():
    night = datetime(2026, 6, 1, 23, 30)
    day = datetime(2026, 6, 1, 13, 0)
    assert chime.allowed_now(_cfg(), now=night)
    assert not chime.allowed_now(_cfg(), now=day)                      # default window 6 PM - 6 AM
    assert chime.allowed_now(_cfg(person_chime_schedule_enabled=False), now=day)  # 24h
    assert not chime.allowed_now(_cfg(person_chime_enabled=False), now=night)
    assert chime.allowed_now(_cfg(person_chime_start="00:00", person_chime_end="06:00"), now=datetime(2026, 6, 1, 2, 0))
    assert not chime.allowed_now(_cfg(person_chime_start="00:00", person_chime_end="06:00"), now=datetime(2026, 6, 1, 7, 0))


def test_maybe_play_only_lone_person_and_rate_limited(monkeypatch):
    calls = []
    monkeypatch.setattr(chime, "play", lambda vol: calls.append(vol) or (True, "ok"))
    monkeypatch.setattr(chime, "_last_play", 0.0)
    cfg = _cfg(person_chime_schedule_enabled=False, person_chime_volume=40)
    assert not chime.maybe_play(cfg, {"category": "vehicle", "vehicle_type": "car"})
    assert not chime.maybe_play(cfg, {"category": "two_wheeler"})
    assert chime.maybe_play(cfg, {"category": "person", "count": 2})
    assert not chime.maybe_play(cfg, {"category": "person"})  # within MIN_GAP_S
    monkeypatch.setattr(chime, "_last_play", 0.0)
    assert not chime.maybe_play(_cfg(person_chime_enabled=False, person_chime_schedule_enabled=False), {"category": "person"})
    import time
    time.sleep(0.05)
    assert calls == [40]


def test_play_on_non_windows_reports_unsupported_but_builds_wav(monkeypatch, tmp_path):
    monkeypatch.setattr(chime, "WAV_PATH", str(tmp_path / "chime.wav"))
    monkeypatch.setattr(chime, "_built_volume", None)
    monkeypatch.setattr(chime, "winsound", None)
    ok, detail = chime.play(55)
    assert ok is False and "Windows" in detail and os.path.exists(chime.WAV_PATH)
    # fake winsound -> played
    class _WS:
        SND_FILENAME, SND_ASYNC, SND_NODEFAULT = 1, 2, 4
        played = []

        @classmethod
        def PlaySound(cls, path, flags):
            cls.played.append((path, flags))
    monkeypatch.setattr(chime, "winsound", _WS)
    ok, detail = chime.play(55)
    assert ok and _WS.played and _WS.played[0][0] == chime.WAV_PATH


def test_worker_on_event_triggers_chime_once_per_event(monkeypatch):
    import service
    calls = []
    monkeypatch.setattr(service.chime, "maybe_play", lambda cfg, ev: calls.append(ev.get("id")) or True)
    w = service.Worker()
    w._on_event({"id": 7, "category": "person", "direction": "Entry", "count": 1, "plate_status": ""})
    w._on_event({"id": 8, "category": "vehicle", "direction": "Entry", "plate_status": "pending"})
    w._on_event({"id": 8, "category": "vehicle", "direction": "Entry", "plate_status": "done"})
    assert calls == [7]
    assert w.last_event["id"] == 8 and w.last_event["category"] == "vehicle"
