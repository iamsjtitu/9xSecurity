"""9x Security - Lightweight centroid tracker + line-crossing detection."""
import math
import time
from collections import Counter

HEAVY = ("bus", "truck")
ANIMAL_LABELS = ("dog", "cat", "cow", "horse", "sheep", "bird")
ANIMAL_VOTE = 0.25   # >= this share of frames called it an animal -> it is an animal, never a person
SPEED_MAX = 5.0      # plausible speed: box-sizes per second (bike at ~30 km/h through a gate)
JUMP_CAP = 3.0       # never accept a single-step jump above this many box-sizes


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


def _touches_edge(bbox, frame_size, margin=3):
    if not frame_size:
        return False
    w = frame_size[0]
    return bbox[0] <= margin or bbox[2] >= w - margin


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
    def __init__(self, tid, centroid, bbox, label, side, dist=0.0, ts=0.0):
        self.id = tid
        self.centroid = centroid
        self.bbox = bbox
        self.labels = Counter([label])
        self.side = side          # last known side sign (-1 / 0 / 1)
        self.start_side = side
        self.start_dist = dist    # signed px distance to line when first seen
        self.start_ref = ((bbox[0] + bbox[2]) // 2, bbox[3])  # ref point when first seen
        self.start_edge = False   # first seen touching the left/right picture edge
        self.disappeared = 0
        self.crossings = 0
        self.last_cross_ts = None
        self.last_cross_to = 0
        self.armed = True         # may count a crossing right now (hysteresis)
        self.last_ts = ts         # time of the last matched detection (speed-aware matching)
        self.vel = None           # centroid velocity px/s (EMA), None until the 2nd detection
        self.last_reject = ""     # why the latest side flip was NOT counted (diagnostics)

    @property
    def counted(self):
        return self.crossings > 0

    @property
    def label(self):
        """Voted label over the track's life. YOLO often calls a truck 'car' for a
        few frames — if a heavy class shows up in >=30% of frames, trust it. Likewise a
        dog/cow called 'person' in a few frames is an ANIMAL if >=25% of frames said so."""
        total = sum(self.labels.values()) or 1
        animal = {k: v for k, v in self.labels.items() if k in ANIMAL_LABELS}
        if animal and sum(animal.values()) / total >= ANIMAL_VOTE:
            return max(animal, key=animal.get)
        for heavy in HEAVY:
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
        self.lost = {}  # tid -> (last bbox, ts) of aged-out tracks (engine de-dup: "lost right here?")
        self.rejects = []  # per update(): side flips that were NOT counted + possible misses (diagnostics)

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

    @staticmethod
    def _cadence_allowance(bbox, dt, floor):
        """How far a REAL vehicle may have travelled in dt seconds (slow PC = 2-4 detections/s):
        a fast bike must not become a new track at every detection."""
        x1, y1, x2, y2 = bbox
        size = max(x2 - x1, y2 - y1, 20)
        return max(floor, min(JUMP_CAP * size, SPEED_MAX * size * max(0.0, dt)))

    def _match_window(self, tr, bbox, dt):
        """(anchor point, radius) a detection must fall in to continue track `tr`.
        Known velocity -> look where the vehicle SHOULD be now (prediction), with slack for it
        having stopped. Unknown velocity -> around its last spot; a fresh track (seen at the
        previous update) gets the speed-based cadence allowance, a stale one does not (a track
        that vanished must never grab a different vehicle appearing elsewhere)."""
        base = self._allowed_dist(bbox)
        if tr.vel is None:
            if tr.disappeared == 0:
                return tr.centroid, self._cadence_allowance(bbox, dt, base)
            return tr.centroid, base
        speed = math.hypot(tr.vel[0], tr.vel[1])
        anchor = (tr.centroid[0] + tr.vel[0] * dt, tr.centroid[1] + tr.vel[1] * dt)
        return anchor, base + 0.5 * speed * dt

    def _hyst(self):
        if self.hysteresis is not None:
            return self.hysteresis
        return max(20.0, 0.5 * self.near_band)

    def update(self, detections, line, now=None, frame_size=None):
        """
        detections: list of {bbox,label}
        line: (a, b) two points in same coord space as detections
        Returns list of crossing events: {track_id, label, from_side, to_side, via}
        """
        now = time.time() if now is None else now
        a, b = line
        line_len = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
        crossings = []
        self.rejects = []
        inputs = [(self._centroid(d["bbox"]), d) for d in detections]

        # Global greedy assignment: best (track, detection) pairs first. Cost prefers
        # box overlap and forbids big size jumps, so a small far-away vehicle can never
        # steal a parked truck's track (identity swaps looked like Entry/Exit crossings).
        pairs = []
        for di, (c, d) in enumerate(inputs):
            for tid, tr in self.tracks.items():
                iou = _iou(tr.bbox, d["bbox"])
                if iou >= 0.25:
                    pairs.append((1.0 - iou, tid, di))
                    continue
                anchor, limit = self._match_window(tr, d["bbox"], now - tr.last_ts)
                dist = math.hypot(c[0] - anchor[0], c[1] - anchor[1])
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
                prev_c = tr.centroid
                dt = max(0.0, now - tr.last_ts)
                jump = math.hypot(cur_ref[0] - prev_ref[0], cur_ref[1] - prev_ref[1])
                x1, y1, x2, y2 = d["bbox"]
                size = max(x2 - x1, y2 - y1, 20)
                # one-step teleport = not a real move. Allowed step: where the vehicle SHOULD be
                # (its velocity x time, or stopped), or — for a fresh track without velocity yet —
                # a plausible speed for the time passed (slow PC: 2-4 detections per second)
                if tr.vel is not None:
                    pred = (prev_ref[0] + tr.vel[0] * dt, prev_ref[1] + tr.vel[1] * dt)
                    jump_eff = min(jump, math.hypot(cur_ref[0] - pred[0], cur_ref[1] - pred[1]))
                    max_jump = max(0.8 * size, 0.5 * math.hypot(tr.vel[0], tr.vel[1]) * dt)
                elif tr.disappeared == 0:
                    jump_eff, max_jump = jump, self._cadence_allowance(d["bbox"], dt, 0.8 * size)
                else:
                    jump_eff, max_jump = jump, 0.8 * size
                steady = jump_eff <= max_jump
                edge = _touches_edge(d["bbox"], frame_size)
                if edge:
                    steady = False  # partly outside the picture: its bottom-centre is not where the vehicle is
                if steady and dt > 1e-3:
                    v = ((c[0] - prev_c[0]) / dt, (c[1] - prev_c[1]) / dt)
                    tr.vel = v if tr.vel is None else (0.5 * tr.vel[0] + 0.5 * v[0], 0.5 * tr.vel[1] + 0.5 * v[1])
                tr.centroid = c
                tr.bbox = d["bbox"]
                tr.labels[d["label"]] += 1
                tr.disappeared = 0
                tr.last_ts = now

                # re-arm only once the vehicle is clearly past the line it just crossed:
                # at least 15% of its own height (a Bolero stopping at the gate jitters +-20px)
                if not tr.armed and abs(cur_dist) >= max(self._hyst(), 0.15 * (y2 - y1)):
                    tr.armed = True

                flipped = prev_sign != 0 and cur_sign != 0 and cur_sign != prev_sign
                on_segment = _hits_segment(prev_ref, cur_ref, a, b)
                crossed = flipped and steady and on_segment
                appeared_at_line = (
                    tr.crossings == 0
                    and self.near_band > 0
                    and cur_sign != 0
                    and abs(tr.start_dist) <= self.near_band
                    and abs(cur_dist) >= 1.5 * self.near_band
                    and self._sign(tr.start_dist) in (0, cur_sign)
                    and steady
                    and not tr.start_edge  # came into view from the picture edge, not 'at the line'
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
                elif flipped:
                    tr.last_reject = self._reject_reason(edge, steady, jump, max_jump, on_segment, tr, gap_ok, repeat)
                    self.rejects.append({"track_id": best_id, "label": tr.label, "bbox": d["bbox"],
                                         "to_side": cur_sign, "reason": tr.last_reject, "kind": "reject"})
                if steady or cur_sign == prev_sign:
                    tr.side = cur_sign  # a teleport does not change which side we believe the vehicle is on
            else:
                tid = self.next_id
                self.next_id += 1
                self.tracks[tid] = Track(tid, c, d["bbox"], d["label"], cur_sign, cur_dist, ts=now)
                self.tracks[tid].start_edge = _touches_edge(d["bbox"], frame_size)
                used_track_ids.add(tid)

        # Age out unmatched tracks
        for tid in list(self.tracks.keys()):
            if tid not in used_track_ids:
                tr = self.tracks[tid]
                tr.disappeared += 1
                if tr.disappeared > self.max_disappeared:
                    if tr.crossings == 0 and tr.start_side != 0 and tr.side != 0 and tr.side != tr.start_side:
                        # it ended up on the other side of the line without ever being counted
                        self.rejects.append({"track_id": tid, "label": tr.label, "bbox": tr.bbox, "to_side": tr.side,
                                             "reason": tr.last_reject or "side badla par koi crossing count nahi hui",
                                             "kind": "possible-miss"})
                    self.lost[tid] = (tr.bbox, now)
                    del self.tracks[tid]
        self.lost = {k: v for k, v in self.lost.items() if now - v[1] < 120}
        return crossings

    @staticmethod
    def _reject_reason(edge, steady, jump, max_jump, on_segment, tr, gap_ok, repeat):
        if edge:
            return "box picture ke kinare ko chhoo raha tha (aadhi gaadi bahar)"
        if not steady:
            return f"ek step me {jump:.0f}px jump (limit {max_jump:.0f}px) — teleport/galat match"
        if not on_segment:
            return "line ko drawn segment ke bahar cross kiya (line chhoti hai?)"
        if not tr.armed:
            return "pichhli crossing ke baad abhi re-arm nahi hua (line ke bahut paas)"
        if not gap_ok:
            return "pichhli crossing ke 3 sec ke andar"
        if repeat:
            return "usi direction me dobara (beech me wapas cross nahi hua)"
        return "unknown"

    @staticmethod
    def _sign(v):
        if v > 1e-6:
            return 1
        if v < -1e-6:
            return -1
        return 0
