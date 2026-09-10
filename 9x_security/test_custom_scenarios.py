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


# ---------------------------------------------------------------------------
# Iteration 23: engine-level dedupe scenarios (fix for user's "car Entry aaya,
# uske baad JCB/car ka nahi aaya - continuity nahi hai"). These reuse the
# _engine/_ScriptDet/_Clock/_drive helpers from test_tracker_false_crossings.
# Line goes ~(700,119)->(850,143) in a 960x540 frame; the bottom-centre lives
# at x=780 where the line is at y~=131.8, so bottom_y in {100,106,...,130,136}
# from _cross_frames(...,step=6) never lands on the line.
# ---------------------------------------------------------------------------
from test_tracker_false_crossings import (
    _Clock, _ScriptDet, _cleanup, _cross_frames, _drive, _engine,
)


def test_e_following_vehicle_while_first_still_visible_counts(tmp_path, monkeypatch):
    """Two DIFFERENT vehicles cross the same spot ~5 s apart while the first
    is still tracked deeper in the yard (its track never ages out). The second
    must not be swallowed by de-dup."""
    import engine as eng
    clock = _Clock()
    monkeypatch.setattr(eng.time, "time", clock)
    e = _engine(tmp_path, _ScriptDet())
    # v1 crosses down (bottom 100 -> 290) and drives deeper (bottom 290 -> 410)
    v1_cross = [[d] for d in _cross_frames(60, 250)]                              # ~24 frames
    v1_deep = [[{"bbox": (720, 210, 840, 290), "label": "truck"}]] * 32           # stays alive ~4 s
    v1_deeper = [[{"bbox": (720, 320 + i * 2, 840, 400 + i * 2), "label": "truck"}] for i in range(8)]
    # v2 appears at the same gate spot while v1 is still visible below
    def combo(v1_box, v2_dets):
        return [v1_box] + v2_dets
    v2_frames = _cross_frames(60, 250)
    combo_frames = []
    for i, v2 in enumerate(v2_frames):
        v1_box = {"bbox": (720, 380 + i * 2, 840, 460 + i * 2), "label": "truck"}
        combo_frames.append(combo(v1_box, [v2]))
    events = _drive(e, clock, v1_cross + v1_deep + v1_deeper + combo_frames)
    dirs = [ev["direction"] for ev in events]
    assert len(events) == 2 and len(set(dirs)) == 1, [
        (ev["direction"], ev["vehicle_type"], ev["id"]) for ev in events
    ]
    _cleanup(events)


def test_f_same_vehicle_track_recreated_at_gate_is_one_event(tmp_path, monkeypatch):
    """Same physical vehicle: crosses down, stops just past the line, detector
    misses it for >20 frames (track aged out), YOLO re-detects it exactly
    there, it moves on into the yard. Must remain a single Entry event."""
    import engine as eng
    clock = _Clock()
    monkeypatch.setattr(eng.time, "time", clock)
    e = _engine(tmp_path, _ScriptDet())
    # cross down and stop with bottom at y=224 (clearly past line ~132, outside near_band)
    script = [[d] for d in _cross_frames(60, 190)]
    stopped = {"bbox": (720, 144, 840, 224), "label": "truck"}
    script += [[stopped]] * 4                                     # sits past the gate
    script += [[]] * 25                                            # detector drops it >max_disappeared(20)
    script += [[stopped]] * 3                                      # re-detected at the SAME spot
    # now drives further in (bottom 224 -> 400)
    script += [[{"bbox": (720, y - 80, 840, y), "label": "truck"}] for y in range(236, 410, 12)]
    events = _drive(e, clock, script)
    assert len(events) == 1, [(ev["direction"], ev["id"]) for ev in events]
    _cleanup(events)


def test_g_opposite_direction_after_entry_is_not_deduped(tmp_path, monkeypatch):
    """Entry (down through gate), then a DIFFERENT vehicle exits (up through
    the same spot) ~6 s later. De-dup is same-direction only, so both count
    and the two events have different directions."""
    import engine as eng
    clock = _Clock()
    monkeypatch.setattr(eng.time, "time", clock)
    e = _engine(tmp_path, _ScriptDet())
    # entering vehicle: bottom 100 -> 290 (crosses line ~131 downward = Entry)
    entry = [[d] for d in _cross_frames(60, 250)]
    # entering vehicle drives out of view; empty gap ~6 s -> track aged out far away
    gap = [[]] * 48
    # different vehicle exits: bottom starts far past the line (y=290) and moves
    # UP through the line to y=100 -> to_side flips the other way
    exit_ys = list(range(250, 50, -6))
    exit_frames = [[{"bbox": (720, y - 40, 840, y + 40), "label": "car"}] for y in exit_ys]
    events = _drive(e, clock, entry + gap + exit_frames)
    dirs = {ev["direction"] for ev in events}
    assert len(events) == 2 and dirs == {"Entry", "Exit"}, [
        (ev["direction"], ev["vehicle_type"], ev["id"]) for ev in events
    ]
    _cleanup(events)
