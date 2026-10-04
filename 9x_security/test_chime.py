"""Gate chimes: synthesised per-category WAVs, shared night window, per-category rate limit, worker hook."""
import os
import sys
import wave
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import chime  # noqa: E402
import config  # noqa: E402


def _cfg(**over):
    return {**config.DEFAULTS, **over}


def _peak(path):
    with wave.open(path) as w:
        return int(np.abs(np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")).max()), w.getnframes() / w.getframerate()


def test_build_wav_is_valid_and_volume_scales(tmp_path):
    loud = chime.build_wav(100, str(tmp_path / "a.wav"))
    quiet = chime.build_wav(20, str(tmp_path / "b.wav"))
    with wave.open(loud) as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == chime.SAMPLE_RATE
    pa, dur = _peak(loud)
    pb, _ = _peak(quiet)
    assert 1.2 < dur < 1.6 and pa > 15000 and 0.15 < pb / pa < 0.25  # 20% volume ~ 1/5 amplitude
    assert _peak(chime.build_wav(0, str(tmp_path / "c.wav")))[0] == 0


def test_each_category_has_its_own_distinct_tone(tmp_path):
    files = {c: chime.build_wav(80, str(tmp_path / f"{c}.wav"), category=c) for c in chime.TONES}
    durs = {c: _peak(p)[1] for c, p in files.items()}
    assert durs["two_wheeler"] < durs["person"] < durs["vehicle"]  # quick / medium / long
    raw = {}
    for c, p in files.items():
        with wave.open(p) as w:
            raw[c] = w.readframes(w.getnframes())
    assert len(set(raw.values())) == 3
    assert chime.wav_path("person").endswith("person_chime.wav") and chime.wav_path("vehicle").endswith("chime_vehicle.wav")


def test_allowed_now_gating_per_category():
    night = datetime(2026, 6, 1, 23, 30)
    day = datetime(2026, 6, 1, 13, 0)
    for cat in chime.TONES:
        assert chime.allowed_now(_cfg(), now=night, category=cat)
        assert not chime.allowed_now(_cfg(), now=day, category=cat)                       # shared window 6 PM - 6 AM
        assert chime.allowed_now(_cfg(person_chime_schedule_enabled=False), now=day, category=cat)  # 24h
    assert not chime.allowed_now(_cfg(vehicle_chime_enabled=False), now=night, category="vehicle")
    assert chime.allowed_now(_cfg(vehicle_chime_enabled=False), now=night, category="person")
    assert not chime.allowed_now(_cfg(person_chime_enabled=False), now=night, category="person")
    assert not chime.allowed_now(_cfg(), now=night, category="unknown")
    assert chime.allowed_now(_cfg(person_chime_start="00:00", person_chime_end="06:00"), now=datetime(2026, 6, 1, 2, 0))
    assert not chime.allowed_now(_cfg(person_chime_start="00:00", person_chime_end="06:00"), now=datetime(2026, 6, 1, 7, 0))


def test_maybe_play_routes_category_and_rate_limits_per_category(monkeypatch):
    calls = []
    monkeypatch.setattr(chime, "play", lambda vol, cat: calls.append((vol, cat)) or (True, "ok"))
    monkeypatch.setattr(chime, "_last_play", {})
    cfg = _cfg(person_chime_schedule_enabled=False, person_chime_volume=40)
    assert chime.maybe_play(cfg, {"category": "vehicle", "vehicle_type": "car"})
    assert chime.maybe_play(cfg, {"category": "two_wheeler"})
    assert chime.maybe_play(cfg, {"category": "person", "count": 2})
    assert not chime.maybe_play(cfg, {"category": "person"})   # within MIN_GAP_S for person
    assert not chime.maybe_play(cfg, {"category": "vehicle"})  # within MIN_GAP_S for vehicle
    monkeypatch.setattr(chime, "_last_play", {})
    assert not chime.maybe_play(_cfg(vehicle_chime_enabled=False, person_chime_schedule_enabled=False), {"category": "vehicle"})
    assert not chime.maybe_play(cfg, {"category": ""})
    import time
    time.sleep(0.05)
    assert sorted(calls) == [(40, "person"), (40, "two_wheeler"), (40, "vehicle")]


def test_play_on_non_windows_reports_unsupported_but_builds_wav(monkeypatch, tmp_path):
    monkeypatch.setattr(chime, "WAV_PATH", str(tmp_path / "chime.wav"))
    monkeypatch.setattr(chime.config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(chime, "_built", {})
    monkeypatch.setattr(chime, "winsound", None)
    ok, detail = chime.play(55)
    assert ok is False and "Windows" in detail and os.path.exists(chime.WAV_PATH)
    ok, _ = chime.play(55, "vehicle")
    assert ok is False and os.path.exists(tmp_path / "chime_vehicle.wav")

    class _WS:
        SND_FILENAME, SND_ASYNC, SND_NODEFAULT = 1, 2, 4
        played = []

        @classmethod
        def PlaySound(cls, path, flags):
            cls.played.append(path)
    monkeypatch.setattr(chime, "winsound", _WS)
    assert chime.play(55, "two_wheeler")[0] and _WS.played[-1].endswith("chime_two_wheeler.wav")
    assert chime.play(55, "bogus")[0] and _WS.played[-1] == chime.WAV_PATH  # unknown -> person tone


def test_worker_on_event_triggers_chime_once_per_event(monkeypatch):
    import service
    calls = []
    monkeypatch.setattr(service.chime, "maybe_play", lambda cfg, ev: calls.append((ev.get("id"), ev.get("category"))) or True)
    w = service.Worker()
    w._on_event({"id": 7, "category": "person", "direction": "Entry", "count": 1, "plate_status": ""})
    w._on_event({"id": 8, "category": "vehicle", "direction": "Entry", "plate_status": "pending"})
    w._on_event({"id": 8, "category": "vehicle", "direction": "Entry", "plate_status": "done"})  # OCR backfill: no 2nd chime
    w._on_event({"id": 9, "category": "two_wheeler", "direction": "Exit", "plate_status": ""})
    assert calls == [(7, "person"), (8, "vehicle"), (9, "two_wheeler")]
    assert w.last_event["id"] == 9 and w.last_event["category"] == "two_wheeler"
