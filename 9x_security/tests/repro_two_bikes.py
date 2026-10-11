"""Reproduce: 2 people pushing 2 bicycles side by side across the line -> how many two-wheeler alerts?"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_categories import _drive, _engine  # noqa: E402

LINE = {"x1": 0.05, "y1": 0.45, "x2": 0.95, "y2": 0.45}


def frames(seed, p_det=0.7, dup=True, swap_jitter=12):
    rnd = random.Random(seed)
    out = []
    y = 150.0
    for _ in range(140):
        y += 1.5  # walking with the bike: ~12 px/s at 8 fps
        dets = []
        for bx in (560, 620):  # two bicycles 60 px apart
            jx = rnd.randint(-swap_jitter, swap_jitter)
            box = (bx - 16 + jx, int(y) - 70, bx + 16 + jx, int(y))
            if rnd.random() < p_det:
                dets.append({"bbox": box, "label": "bicycle"})
                if dup and rnd.random() < 0.15:
                    dets.append({"bbox": (box[0] - 2, box[1] + 3, box[2] + 2, box[3] - 1), "label": "motorcycle"})
            # the person walking beside the bike (tall box)
            dets.append({"bbox": (bx - 40, int(y) - 110, bx - 10, int(y) - 5), "label": "person"})
        out.append(dets)
    return out


def main(tmp="/tmp/nx_bikes"):
    os.makedirs(tmp, exist_ok=True)
    from pathlib import Path
    tot = {}
    for seed in range(12):
        e = _engine(Path(tmp), line=LINE, line2=None, enable_person=True, enable_two_wheeler=True)
        ev = _drive(e, frames(seed))
        cats = [(x["category"], x["direction"], x.get("count")) for x in ev]
        tot[seed] = cats
        print(seed, cats)
        for x in ev:
            if os.path.exists(x.get("image_path", "")):
                os.remove(x["image_path"])
    n = [len([c for c in v if c[0] == "two_wheeler"]) for v in tot.values()]
    print("two-wheeler alerts per run:", n)


if __name__ == "__main__":
    main()
