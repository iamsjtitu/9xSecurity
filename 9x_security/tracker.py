"""9x Security - Lightweight centroid tracker + line-crossing detection."""
import math
import time
from collections import Counter

HEAVY = ("bus", "truck")


def _side(p, a, b):
    """Signed cross product => which side of line (a->b) point p is on."""
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def _iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / float(ua or 1)


def _along(p, a, b):
    """Projection of p onto line a->b as a fraction of its length (0 = at a, 1 = at b)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = dx * dx + dy * dy
    return ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / n if n else 0.0


def _size(bbox):
    return max(1.0, float((bbox[2] - bbox[0]) * (bbox[3] - bbox[1])))


def _hits_segment(p, q, a, b, margin=0.15):
    """True when the movement p->q crosses the DRAWN line segment a-b (extended by
    `margin` of its length at both ends) — not just the infinite line through it.
    The user's rule: count only when the vehicle comes ON the yellow line."""
    d1 = (b[0] - a[0], b[1] - a[1])
    d2 = (q[0] - p[0], q[1] - p[1])
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-9:
        return False
    t = ((p[0] - a[0]) * d2[1] - (p[1] - a[1]) * d2[0]) / den   # position along a->b
    u = ((p[0] - a[0]) * d1[1] - (p[1] - a[1]) * d1[0]) / den   # position along p->q
    return -margin <= t <= 1 + margin and 0 <= u <= 1


class Track:
    def __init__(self, tid, centroid, bbox, label, side, dist=0.0):
        self.id = tid
        self.centroid = centroid
        self.bbox = bbox
        self.labels = Counter([label])
        self.side = side          # last known side sign (-1 / 0 / 1)
        self.start_dist = dist    # signed px distance to line when first seen
        self.start_ref = ((bbox[0] + bbox[2]) // 2, bbox[3])  # ref point when first seen
        self.disappeared = 0
        self.crossings = 0
        self.last_cross_ts = None
        self.last_cross_to = 0
        self.armed = True         # may count a crossing right now (hysteresis)

    @property
    def counted(self):
        return self.crossings > 0

    @property
    def label(self):
        """Voted label over the track's life. YOLO often calls a truck 'car' for a
        few frames — if a heavy class shows up in >=30% of frames, trust it."""
        total = sum(self.labels.values()) or 1
        for heavy in ("bus", "truck"):
            if self.labels.get(heavy, 0) / total >= 0.30:
                return heavy
        return self.labels.most_common(1)[0][0]


class CentroidTracker:
    """Matches detections by centroid distance; line-crossing is judged on the
    BOTTOM-CENTER point (where the wheels touch the ground). For a gate camera
    looking down, a vehicle's centroid is already past a ground line when it
    first becomes visible — bottom-center crosses the line reliably.

    A track can cross MORE THAN ONCE (car exits, turns, comes back in): after a
    crossing it is disarmed until the ref point moves >= hysteresis px past the
    line, and a new crossing needs >= min_gap_s since the last one."""

    def __init__(self, max_disappeared=20, max_distance=90, near_band=0, hysteresis=None, min_gap_s=3.0):
        self.next_id = 1
        self.tracks = {}
        self.max_disappeared = max_disappeared
        self.max_distance = max_distance
        # Occluded gates: a vehicle may FIRST appear already just past the line
        # (wall hides the outside). If it appeared within near_band px of the line
        # and then moved >= 1.5*band away on that same side, count it as a crossing.
        self.near_band = near_band
        self.hysteresis = hysteresis
        self.min_gap_s = min_gap_s

    @staticmethod
    def _centroid(bbox):
        x1, y1, x2, y2 = bbox
        return ((x1 + x2) // 2, (y1 + y2) // 2)

    @staticmethod
    def ref_point(bbox):
        x1, y1, x2, y2 = bbox
        return ((x1 + x2) // 2, y2)

    def _allowed_dist(self, bbox):
        x1, y1, x2, y2 = bbox
        # big (close) vehicles move many pixels per frame: scale tolerance with size
        return max(self.max_distance, 0.6 * max(x2 - x1, y2 - y1))

    def _hyst(self):
        if self.hysteresis is not None:
            return self.hysteresis
        return max(20.0, 0.5 * self.near_band)

    def update(self, detections, line, now=None):
        """
        detections: list of {bbox,label}
        line: (a, b) two points in same coord space as detections
        Returns list of crossing events: {track_id, label, from_side, to_side, via}
        """
        now = time.time() if now is None else now
        a, b = line
        line_len = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
        crossings = []
        inputs = [(self._centroid(d["bbox"]), d) for d in detections]

        # Global greedy assignment: best (track, detection) pairs first. Cost prefers
        # box overlap and forbids big size jumps, so a small far-away vehicle can never
        # steal a parked truck's track (identity swaps looked like Entry/Exit crossings).
        pairs = []
        for di, (c, d) in enumerate(inputs):
            limit = self._allowed_dist(d["bbox"])
            for tid, tr in self.tracks.items():
                iou = _iou(tr.bbox, d["bbox"])
                dist = math.hypot(c[0] - tr.centroid[0], c[1] - tr.centroid[1])
                if iou >= 0.25:
                    pairs.append((1.0 - iou, tid, di))
                    continue
                ratio = _size(d["bbox"]) / max(1.0, _size(tr.bbox))
                if dist <= limit and 0.4 <= ratio <= 2.5:
                    pairs.append((1.0 + dist / limit, tid, di))
        pairs.sort()
        assigned = {}
        used_track_ids = set()
        for _cost, tid, di in pairs:
            if tid in used_track_ids or di in assigned:
                continue
            assigned[di] = tid
            used_track_ids.add(tid)

        for di, (c, d) in enumerate(inputs):
            cur_ref = self.ref_point(d["bbox"])
            cur_dist = _side(cur_ref, a, b) / line_len
            cur_sign = self._sign(cur_dist)
            best_id = assigned.get(di)
            if best_id is not None:
                tr = self.tracks[best_id]
                prev_sign = tr.side
                prev_ref = self.ref_point(tr.bbox)
                jump = math.hypot(cur_ref[0] - prev_ref[0], cur_ref[1] - prev_ref[1])
                x1, y1, x2, y2 = d["bbox"]
                steady = jump <= 0.8 * max(x2 - x1, y2 - y1, 20)  # one-frame teleport = not a real move
                tr.centroid = c
                tr.bbox = d["bbox"]
                tr.labels[d["label"]] += 1
                tr.disappeared = 0

                # re-arm once the vehicle is clearly past the line it just crossed
                if not tr.armed and abs(cur_dist) >= self._hyst():
                    tr.armed = True

                crossed = (prev_sign != 0 and cur_sign != 0 and cur_sign != prev_sign
                           and steady and _hits_segment(prev_ref, cur_ref, a, b))
                appeared_at_line = (
                    tr.crossings == 0
                    and self.near_band > 0
                    and cur_sign != 0
                    and abs(tr.start_dist) <= self.near_band
                    and abs(cur_dist) >= 1.5 * self.near_band
                    and self._sign(tr.start_dist) in (0, cur_sign)
                    and steady
                    and -0.15 <= _along(tr.start_ref, a, b) <= 1.15  # appeared ON the drawn segment
                )
                gap_ok = tr.last_cross_ts is None or (now - tr.last_cross_ts) >= self.min_gap_s
                # same vehicle, same direction again (it went back without crossing the drawn
                # segment) -> never a second Entry for one track without an Exit in between
                repeat = tr.last_cross_to != 0 and cur_sign == tr.last_cross_to
                if tr.armed and gap_ok and not repeat and (crossed or appeared_at_line):
                    crossings.append(
                        {
                            "track_id": best_id,
                            "label": tr.label,
                            "bbox": d["bbox"],
                            "from_side": prev_sign if crossed else -cur_sign,
                            "to_side": cur_sign,
                            "via": "cross" if crossed else "appeared-at-line",
                            "nth": tr.crossings + 1,
                        }
                    )
                    tr.crossings += 1
                    tr.last_cross_ts = now
                    tr.last_cross_to = cur_sign
                    tr.armed = False
                if steady or cur_sign == prev_sign:
                    tr.side = cur_sign  # a teleport does not change which side we believe the vehicle is on
            else:
                tid = self.next_id
                self.next_id += 1
                self.tracks[tid] = Track(tid, c, d["bbox"], d["label"], cur_sign, cur_dist)
                used_track_ids.add(tid)

        # Age out unmatched tracks
        for tid in list(self.tracks.keys()):
            if tid not in used_track_ids:
                self.tracks[tid].disappeared += 1
                if self.tracks[tid].disappeared > self.max_disappeared:
                    del self.tracks[tid]
        return crossings

    @staticmethod
    def _sign(v):
        if v > 1e-6:
            return 1
        if v < -1e-6:
            return -1
        return 0
