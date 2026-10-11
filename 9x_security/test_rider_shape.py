"""USER (11-10, 12:09 PM): a bike + rider gave '👤 Person Entry — 2 persons' — YOLO boxed the bike and
the rider as TWO 'person' boxes and no motorcycle, so the rider suppression (needs a two-wheeler
track) never fired. Rules now: (1) a 'person' track that was a motorcycle/bicycle in >= 25% of its
frames IS the two-wheeler; (2) a SEATED shape (box shorter than 1.8 widths) moving at bike speed
(> 1.5 body-heights/s) is a rider, never a lone walker — a tall walker at walking speed still counts."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from tracker import CentroidTracker, Track  # noqa: E402
from test_categories import _drive, _engine  # noqa: E402

LINE = ((0, 380), (960, 380))


def _label_votes(votes):
    tr = Track(1, (0, 0), (0, 0, 10, 10), votes[0], 1)
    for v in votes[1:]:
        tr.labels[v] += 1
    return tr.label


def test_person_with_some_two_wheeler_votes_is_the_two_wheeler():
    assert _label_votes(["person"] * 7 + ["motorcycle"] * 3) == "motorcycle"
    assert _label_votes(["person"] * 9 + ["bicycle"] * 1) == "person"       # 10%: just noise
    assert _label_votes(["car"] * 7 + ["motorcycle"] * 3) == "car"           # only rescues 'person'
    assert _label_votes(["person"] * 6 + ["dog"] * 4) == "dog"               # animal rule unchanged


def _frames(box_w, box_h, step, x=780, n=None):
    """A box of the given size moving DOWN `step` px per frame (8 fps) across the test line (~y 132)."""
    out = []
    y = 60
    while y < 330 and (n is None or len(out) < n):
        out.append([{"bbox": (x - box_w // 2, y - box_h, x + box_w // 2, y), "label": "person"}])
        y += step
    return out


def _cleanup(ev):
    for x in ev:
        if os.path.exists(x.get("image_path", "")):
            os.remove(x["image_path"])


def test_seated_fast_person_box_is_a_rider_not_a_walker(tmp_path):
    e = _engine(tmp_path, enable_person=True, enable_two_wheeler=True)
    # 60x95 box (1.6 widths tall = seated) moving 20 px/frame = 160 px/s = 1.7 body-heights/s (bike)
    assert _drive(e, _frames(60, 95, 20)) == []


def test_two_seated_fast_boxes_bike_and_rider_give_no_person_alert(tmp_path):
    """The user's photo: one box on the bike, one on the rider, both 'person', both moving together."""
    e = _engine(tmp_path, enable_person=True, enable_two_wheeler=True)
    frames = []
    for f in _frames(60, 95, 20):
        b = f[0]["bbox"]
        frames.append([f[0], {"bbox": (b[0] - 50, b[1] + 20, b[2] - 40, b[3] + 5), "label": "person"}])  # bike box: wide, lower
    assert _drive(e, frames) == []


def test_tall_walker_at_walking_speed_still_counts(tmp_path):
    e = _engine(tmp_path, enable_person=True)
    # 40x100 box (2.5 widths tall) at 6 px/frame = 48 px/s = 0.5 body-heights/s
    ev = _drive(e, _frames(40, 100, 6))
    assert [x["category"] for x in ev] == ["person"], ev
    _cleanup(ev)


def test_tall_person_running_still_counts(tmp_path):
    """Only the SEATED shape + speed is a rider: a tall box moving fast is a running person."""
    e = _engine(tmp_path, enable_person=True)
    ev = _drive(e, _frames(40, 110, 24))                        # 192 px/s = 1.75 body-heights/s but tall
    assert [x["category"] for x in ev] == ["person"], ev
    _cleanup(ev)


def test_seated_shape_at_walking_speed_is_still_a_person(tmp_path):
    """A short/stocky walker (steep camera angle) at walking speed must not be dropped."""
    e = _engine(tmp_path, enable_person=True)
    ev = _drive(e, _frames(60, 95, 6))
    assert [x["category"] for x in ev] == ["person"], ev
    _cleanup(ev)


def test_tracker_label_vote_makes_bike_track_a_two_wheeler_event(tmp_path):
    """YOLO alternates person/motorcycle on the bike box (3 of 10 frames motorcycle): one
    two-wheeler alert, and the rider box on it is suppressed as 'ke upar/saath'."""
    e = _engine(tmp_path, enable_person=True, enable_two_wheeler=True)
    frames = []
    for i, f in enumerate(_frames(90, 70, 20)):                 # bike: wide low box
        bike = {**f[0], "label": "motorcycle" if i % 3 == 0 else "person"}
        b = bike["bbox"]
        rider = {"bbox": (b[0] + 20, b[1] - 60, b[0] + 70, b[3] - 10), "label": "person"}
        frames.append([bike, rider])
    ev = _drive(e, frames)
    assert [x["category"] for x in ev] == ["two_wheeler"], [(x["category"], x["vehicle_type"]) for x in ev]
    _cleanup(ev)


def test_tracker_two_wheeler_vote_in_update():
    tr = CentroidTracker(min_gap_s=3.0)
    t = 1000.0
    for i, bottom in enumerate(range(200, 560, 30)):
        label = "motorcycle" if i % 4 == 0 else "person"
        out = tr.update([{"bbox": (450, bottom - 70, 540, bottom), "label": label}], LINE, now=t, frame_size=(960, 540))
        t += 0.125
        if out:
            assert out[0]["label"] == "motorcycle", out
