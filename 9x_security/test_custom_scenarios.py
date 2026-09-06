"""Custom tracker scenarios for iteration_22 review."""
import time
from tracker import CentroidTracker


LINE = ((0, 380), (960, 380))
FS = (960, 540)


def _truck_bbox(cx, bottom_y, w=200, h=300):
    x1 = cx - w // 2
    x2 = cx + w // 2
    y2 = bottom_y
    y1 = y2 - h
    return (x1, y1, x2, y2)


def test_a_stopping_at_gate_with_jitter_counts_once():
    tr = CentroidTracker(min_gap_s=3.0)
    t = 1000.0
    # Approach: bottom goes from y=205 (above line) down through 380
    for by in range(205, 415, 30):
        tr.update([{"bbox": _truck_bbox(480, by), "label": "truck"}], LINE, now=t, frame_size=FS)
        t += 0.1
    # Now stand at gate with jitter +/-25 around bottom=410 for 60 seconds
    total = 0
    import random
    random.seed(0)
    for i in range(600):  # 60s @ 10fps
        jitter = random.randint(-25, 25)
        by = 410 + jitter
        ev = tr.update([{"bbox": _truck_bbox(480, by), "label": "truck"}], LINE, now=t, frame_size=FS)
        total += len(ev)
        t += 0.1
    # Total crossings across the whole scenario: exactly 1 (the entry)
    all_tracks = list(tr.tracks.values())
    assert len(all_tracks) == 1
    assert all_tracks[0].crossings == 1, f"expected 1, got {all_tracks[0].crossings}"


def test_b_real_reverse_counts_twice():
    tr = CentroidTracker(min_gap_s=3.0)
    t = 1000.0
    # Approach down through line
    for by in range(205, 415, 30):
        tr.update([{"bbox": _truck_bbox(480, by), "label": "truck"}], LINE, now=t, frame_size=FS)
        t += 0.1
    # Stand briefly with wider jitter that pushes past re-arm threshold (>=45px past line)
    for by in [400, 430, 445, 435, 420]:
        tr.update([{"bbox": _truck_bbox(480, by), "label": "truck"}], LINE, now=t, frame_size=FS)
        t += 0.1
    # Wait past min_gap
    t += 5.0
    # Now real reverse back out: bottom moves back up through 380 to 250 (>15% of 300=45px past line)
    events_total = 0
    for by in range(400, 240, -15):
        ev = tr.update([{"bbox": _truck_bbox(480, by), "label": "truck"}], LINE, now=t, frame_size=FS)
        events_total += len(ev)
        t += 0.1
    all_tracks = list(tr.tracks.values())
    assert len(all_tracks) == 1
    assert all_tracks[0].crossings == 2, f"expected 2, got {all_tracks[0].crossings}"


def test_c_right_edge_no_crossing_then_moves_in_counts():
    tr = CentroidTracker(min_gap_s=3.0)
    t = 1000.0
    # bbox touches right edge (x2=960 == w). Bottom crosses line vertically.
    for by in range(305, 465, 25):
        # x1 large so x2 = 960
        bbox = (760, by - 300, 960, by)
        ev = tr.update([{"bbox": bbox, "label": "truck"}], LINE, now=t, frame_size=FS)
        assert ev == [], f"edge-glued should not cross, got {ev}"
        t += 0.1
    # Now fully inside (x2 = 910). Move above line first.
    for by in range(300, 195, -25):
        bbox = (700, by - 300, 910, by)
        tr.update([{"bbox": bbox, "label": "truck"}], LINE, now=t, frame_size=FS)
        t += 0.1
    # Now cross downward through the line
    events_total = 0
    for by in range(210, 470, 30):
        bbox = (700, by - 300, 910, by)
        ev = tr.update([{"bbox": bbox, "label": "truck"}], LINE, now=t, frame_size=FS)
        events_total += len(ev)
        t += 0.1
    assert events_total == 1, f"expected 1 crossing after moving off edge, got {events_total}"


def test_d_first_seen_at_left_edge_moving_away_not_counted():
    tr = CentroidTracker(near_band=40, min_gap_s=3.0)
    t = 1000.0
    # First frame: bbox touches left edge (x1=0), bottom near line (y=390 -> just past)
    # near_band=40 so start_dist within band would normally allow appeared-at-line
    total = 0
    # First seen at edge, near the line
    ev = tr.update([{"bbox": (0, 90, 200, 390), "label": "truck"}], LINE, now=t, frame_size=FS)
    total += len(ev)
    t += 0.1
    # Then move down/away from line (bottom keeps increasing), but bbox no longer touches edge
    for by in range(400, 500, 15):
        ev = tr.update([{"bbox": (30, by - 300, 230, by), "label": "truck"}], LINE, now=t, frame_size=FS)
        total += len(ev)
        t += 0.1
    assert total == 0, f"first-at-edge should not use appeared-at-line, got {total} events"
