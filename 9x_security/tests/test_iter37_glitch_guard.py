"""Iter 37 — tracker glitch-guard regression scenarios requested by main agent.

Horizontal line in a 960x540 frame; outside = above line (y < 300), inside = below.
Tests use CentroidTracker.update directly with scripted boxes.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from tracker import CentroidTracker  # noqa: E402

FRAME = (960, 540)
LINE = ((100, 300), (860, 300))  # horizontal line, y=300


def _run(frames, dt=0.15):
    tr = CentroidTracker()
    tr.near_band = 54
    events, rejects_runs = [], []
    for i, dets in enumerate(frames):
        evs = tr.update(dets, LINE, now=i * dt, frame_size=FRAME)
        events += [(i, e["to_side"], e["via"], e["label"]) for e in evs]
        rejects_runs.append(list(tr.rejects))
    return events, rejects_runs, tr


# (a) The user's case: a car approaches from outside; one frame the box doubles height
# (swallows the guard) and bottom-centre jumps across the line, then snaps back; then
# the car drives in for real. Expected: NO crossing on the glitch; exactly ONE crossing
# via 'cross' once the real drive-in happens.
def test_a_car_glitch_followed_by_real_crossing():
    frames = []
    # 10 frames approaching, bottom-centre well above line (y around 240..270)
    for i in range(10):
        y_bot = 240 + i * 3
        frames.append([{"bbox": (430, y_bot - 80, 530, y_bot), "label": "car", "conf": 0.8}])
    # one glitch frame: box doubles in height (guard swallowed), bottom jumps 135 px to inside
    frames.append([{"bbox": (430, 240, 530, 405), "label": "car", "conf": 0.8}])
    # snap back to normal size, still outside
    frames.append([{"bbox": (430, 190, 530, 270), "label": "car", "conf": 0.8}])
    # a couple more above-line frames
    for i in range(3):
        y_bot = 275 + i * 5
        frames.append([{"bbox": (430, y_bot - 80, 530, y_bot), "label": "car", "conf": 0.8}])
    # now the car actually drives in: bottom-centre moves from 290 -> 330 across y=300
    frames.append([{"bbox": (430, 210, 530, 290), "label": "car", "conf": 0.8}])
    frames.append([{"bbox": (430, 250, 530, 330), "label": "car", "conf": 0.8}])
    for i in range(5):
        y_bot = 350 + i * 10
        frames.append([{"bbox": (430, y_bot - 80, 530, y_bot), "label": "car", "conf": 0.8}])

    events, rejects_runs, _ = _run(frames)
    # Exactly one crossing to the inside side via 'cross'
    assert len(events) == 1, events
    _idx, to_side, via, _lbl = events[0]
    assert to_side == 1  # inside
    assert via == "cross"
    # The glitch frame must have produced a reject with glitch reason OR not have raw-flipped
    # (last-good ref kept). Confirm no 'possible-miss' is recorded anywhere.
    for rs in rejects_runs:
        for r in rs:
            assert r.get("kind") != "possible-miss", rejects_runs


# (b) Occlusion scenario: a truck passing behind a pole loses half its box for 2 frames
# EXACTLY while bottom-centre crosses the line; full box returns past the line. The
# crossing MUST still be counted exactly once, and NO 'possible-miss' reject.
def test_b_occlusion_half_box_during_crossing_counts_once():
    frames = []
    # Approach: full-sized truck bottom-centre moving down from y=240 -> y=290 outside
    for i in range(8):
        y_bot = 240 + i * 6
        frames.append([{"bbox": (400, y_bot - 120, 560, y_bot), "label": "truck", "conf": 0.7}])
    # Two frames behind a pole: half-height box. Bottom-centre crosses line here.
    # Previous full h=120 -> half ~ 60; ratio 60/120 = 0.5 which is below GLITCH_RATIO_LO=0.6
    # -> glitch flagged; crossing should NOT be lost: a moment later full box returns past the line.
    frames.append([{"bbox": (400, 245, 560, 305), "label": "truck", "conf": 0.7}])  # bottom 305 (just inside)
    frames.append([{"bbox": (400, 260, 560, 320), "label": "truck", "conf": 0.7}])
    # Full box returns past the line
    for i in range(8):
        y_bot = 330 + i * 8
        frames.append([{"bbox": (400, y_bot - 120, 560, y_bot), "label": "truck", "conf": 0.7}])

    events, rejects_runs, _ = _run(frames)
    assert len(events) == 1, events
    assert events[0][1] == 1 and events[0][2] == "cross"
    for rs in rejects_runs:
        for r in rs:
            assert r.get("kind") != "possible-miss", r


# (c) Vehicle approaching the camera growing 25-30% per step at 2-3 fps — should count once.
def test_c_vehicle_growing_fast_counts_once():
    frames = []
    w, h = 60, 40
    y_bot = 200
    # 2-3 fps (dt ~ 0.35 s) — the simulated clock dt in _run is 0.15 s per step, use dt=0.4
    for _ in range(10):
        frames.append([{"bbox": (480 - w // 2, y_bot - h, 480 + w // 2, y_bot),
                        "label": "car", "conf": 0.8}])
        y_bot += int(h * 0.6)  # move down fast
        w = int(w * 1.27)
        h = int(h * 1.27)
    events, rejects_runs, _ = _run(frames, dt=0.4)
    assert len(events) == 1, events
    assert events[0][1] == 1
    for rs in rejects_runs:
        for r in rs:
            assert r.get("kind") != "possible-miss", r


# (d) Slow PC — dt 0.5-1.0 s between detections, a car at ~150 px/s should still count once.
def test_d_slow_pc_car_counts_once():
    frames = []
    y_bot = 180
    for _ in range(10):
        frames.append([{"bbox": (430, y_bot - 80, 530, y_bot), "label": "car", "conf": 0.8}])
        y_bot += 120  # 150 px/s * 0.8 s = 120 px per step
    events, rejects_runs, _ = _run(frames, dt=0.8)
    assert len(events) == 1, events
    assert events[0][1] == 1
    for rs in rejects_runs:
        for r in rs:
            assert r.get("kind") != "possible-miss", r


# (e) Jittery small bicycle with +-15% h/w jitter crossing — counted once.
def test_e_jittery_bicycle_counts_once():
    import random
    rnd = random.Random(42)
    frames = []
    y_bot = 220
    for _ in range(16):
        jh = int(50 * (1 + (rnd.random() - 0.5) * 0.30))
        jw = int(28 * (1 + (rnd.random() - 0.5) * 0.30))
        cx = 480
        frames.append([{"bbox": (cx - jw // 2, y_bot - jh, cx + jw // 2, y_bot),
                        "label": "bicycle", "conf": 0.5}])
        y_bot += 12
    events, rejects_runs, _ = _run(frames, dt=0.15)
    # bicycle -> two_wheeler at engine level; at tracker level label stays "bicycle"
    assert len(events) == 1, events
    assert events[0][1] == 1
    for rs in rejects_runs:
        for r in rs:
            assert r.get("kind") != "possible-miss", r


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
