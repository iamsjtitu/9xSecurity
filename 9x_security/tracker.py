"""9x Security - Lightweight centroid tracker + line-crossing detection."""
import math
import time
from collections import Counter

HEAVY = ("bus", "truck")
HEAVY_VOTE = 0.35    # confidence-weighted share of heavy (truck/bus) votes that makes a track heavy
BUS_MIN_SHARE = 0.2  # inside 'heavy': 'bus' only when truck votes are below this share (decorated Indian trucks
                     # are called 'bus' by YOLO in many frames; a real bus almost never gets 'truck' votes)
ANIMAL_LABELS = ("dog", "cat", "cow", "horse", "sheep", "bird", "elephant", "bear", "zebra", "giraffe")
ANIMAL_VOTE = 0.25   # >= this share of frames called it an animal -> it is an animal, never a person
TWO_WHEELER_LABELS = ("motorcycle", "bicycle")
TWO_WHEELER_VOTE = 0.25  # a 'person' that was a motorcycle/bicycle in >= 25% of frames is the two-wheeler
SPEED_MAX = 5.0      # plausible speed: box-sizes per second (bike at ~30 km/h through a gate)
JUMP_CAP = 3.0       # never accept a single-step jump above this many box-sizes
CLEAR_FRAC = 0.20    # 'clearly past the line' = this fraction of the box's larger side (min hysteresis)
RUN_HOLD_S = 1.0     # momentum (monotone progress towards one side) dies after this long without motion
RESUME_S = 300.0     # a counted vehicle lost standing at the gate is remembered this long (user: 5 min)
RESUME_IOU = 0.6     # a new box this similar to a counted vehicle's box IS that vehicle
GLITCH_RATIO_LO, GLITCH_RATIO_HI = 0.6, 1.67  # box width/height change in ONE step beyond this = detection glitch, not a move
TWIN_IOU = 0.55      # two LIVE tracks glued together this much (merge_twins trackers) = one object, two boxes
TWIN_FRAMES = 4      # ... for this many consecutive updates before the uncounted twin inherits the counted state


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
    def __init__(self, tid, centroid, bbox, label, side, dist=0.0, ts=0.0, conf=1.0):
        self.id = tid
        self.centroid = centroid
        self.bbox = bbox
        self.labels = Counter([label])
        self.label_w = Counter({label: conf})  # votes weighted by YOLO confidence (a hesitant 'truck' on a SUV weighs little)
        self.side = side          # COMMITTED side sign (-1 / 1), 0 = born on the line, side not yet known
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
        self.ref = ((bbox[0] + bbox[2]) // 2, bbox[3])  # ref point used last time (edge-compensated)
        self.full_w = None        # width when last seen fully inside the picture (edge compensation)
        self.dist = dist          # signed px distance to the line at the last detection
        self.run = 0.0            # monotone perpendicular progress (px, signed) — momentum towards a side
        self.run_ts = ts
        self.seg_hit = False      # since the side was last committed, a step crossed the DRAWN segment
        self.resumed_from = None  # id of the counted vehicle this track continues (lost / second box)
        self.born_ts = ts         # when this track was created (twin-box tests in the engine)
        self.twin_frames = 0      # consecutive updates glued to a counted track (merge_twins)
        self.verdict = None       # label decided by the engine's zoomed looks ('dog'): frame votes never outvote it
        self.zoom_animal = 0.0    # accumulated zoomed-look evidence (engine): sum of animal / person confidences
        self.zoom_person = 0.0
        self.zoom_label = ""      # strongest animal class seen on the zoomed looks
        self.zoom_best = 0.0
        self.zoom_looks = 0       # zoomed looks that returned a result

    @property
    def person_conf(self):
        """Average YOLO confidence of the frames that called this track 'person'."""
        n = self.labels.get("person", 0)
        return self.label_w.get("person", 0.0) / n if n else 0.0

    @property
    def counted(self):
        return self.crossings > 0

    @property
    def label(self):
        """Voted label over the track's life, votes weighted by confidence. YOLO often calls a
        truck 'car' for a few frames (confidently 'truck' otherwise) and a SUV 'truck' in a few
        hesitant frames — the heavy class wins only when its WEIGHTED share is >= HEAVY_VOTE.
        Within heavy, 'truck' is preferred unless 'bus' is nearly unanimous. A dog/cow called
        'person' in a few frames is an ANIMAL if >=25% of frames said so."""
        if self.verdict:
            return self.verdict
        total = sum(self.labels.values()) or 1
        animal = {k: v for k, v in self.labels.items() if k in ANIMAL_LABELS}
        if animal and sum(animal.values()) / total >= ANIMAL_VOTE:
            return max(animal, key=animal.get)
        w = self.label_w
        total_w = sum(w.values()) or 1.0
        heavy_w = sum(w.get(h, 0.0) for h in HEAVY)
        if heavy_w / total_w >= HEAVY_VOTE:
            return "truck" if w.get("truck", 0.0) >= BUS_MIN_SHARE * heavy_w else "bus"
        top = self.labels.most_common(1)[0][0]
        if top == "person":
            # a bike + rider is often boxed as 'person' most of the time: if YOLO saw a two-wheeler
            # in >= 25% of frames, this is the two-wheeler (never a lone walker)
            tw = {k: v for k, v in self.labels.items() if k in TWO_WHEELER_LABELS}
            if tw and sum(tw.values()) / total >= TWO_WHEELER_VOTE:
                return max(tw, key=tw.get)
        return top

    def state(self):
        return (self.crossings, self.last_cross_ts, self.last_cross_to, self.side, self.armed)

    def inherit(self, src_id, state):
        self.crossings, self.last_cross_ts, self.last_cross_to, self.side, self.armed = state
        self.resumed_from = src_id


class CentroidTracker:
    """Matches detections by centroid distance; line-crossing is judged on the
    BOTTOM-CENTER point (where the wheels touch the ground). For a gate camera
    looking down, a vehicle's centroid is already past a ground line when it
    first becomes visible — bottom-center crosses the line reliably.

    A crossing counts only when the vehicle is CLEARLY on the new side: either its
    ref point is >= `clear` px past the line (CLEAR_FRAC of the box) or it travelled
    >= `clear` px towards that side in one monotone run. A vehicle standing ON the
    line whose YOLO box wobbles (cab only / cab + load) therefore never flips.

    A track can cross MORE THAN ONCE (car exits, turns, comes back in): after a
    crossing it is disarmed until the ref point moves >= `clear` px past the
    line, and a new crossing needs >= min_gap_s since the last one."""

    def __init__(self, max_disappeared=20, max_distance=90, near_band=0, hysteresis=None, min_gap_s=3.0,
                 lost_after_s=2.5, merge_twins=False):
        self.next_id = 1
        self.tracks = {}
        self.max_disappeared = max_disappeared
        # person/two-wheeler tracker: a bicycle often carries TWO live boxes (bicycle + motorcycle label,
        # or two boxes after an ID swap); once one of them is counted the other must inherit that state.
        # Never for cars: a queued car behind another overlaps it in a front-view camera and is the NEXT car.
        self.merge_twins = merge_twins
        # a track is dropped only after BOTH max_disappeared frames AND lost_after_s seconds without
        # a detection: on a fast PC (15 fps) 20 frames is just 1.3 s — a truck hidden behind the gate
        # pillar for 2 s would come back as a NEW track past the line and never be counted
        self.lost_after_s = lost_after_s
        self.max_distance = max_distance
        # Occluded gates: a vehicle may FIRST appear already just past the line
        # (wall hides the outside). If it appeared within near_band px of the line
        # and then moved >= 1.5*band away on that same side, count it as a crossing.
        self.near_band = near_band
        self.hysteresis = hysteresis
        self.min_gap_s = min_gap_s
        # tid -> (last bbox, ts, state-or-None) of aged-out tracks. state (crossings...) is kept
        # RESUME_S for counted vehicles: a truck standing at the gate that YOLO loses for a while
        # comes back as the SAME counted vehicle, never as a fresh one that could count again.
        self.lost = {}
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

    def clear_px(self, bbox):
        """'Clearly past the line' margin for this box: a standing truck's YOLO box wobbles
        ~10% of its size (cab only <-> cab + load), so a flip inside this band is jitter."""
        x1, y1, x2, y2 = bbox
        return max(self._hyst(), CLEAR_FRAC * max(x2 - x1, y2 - y1))

    @staticmethod
    def _push_run(tr, delta, now):
        """Momentum: monotone perpendicular progress. A turn (sign change) or a stop
        (> RUN_HOLD_S without motion) restarts it — a vehicle that stopped at the gate
        gets no credit for the way it came."""
        if abs(delta) < 1.0:
            if now - tr.run_ts > RUN_HOLD_S:
                tr.run = 0.0
            return
        if now - tr.run_ts > RUN_HOLD_S or (tr.run != 0 and (delta > 0) != (tr.run > 0)):
            tr.run = 0.0
        tr.run += delta
        tr.run_ts = now

    def _inherit_source(self, bbox, cur_dist, cur_sign, clear, now):
        """A new box that is almost the same box as a COUNTED vehicle — one YOLO lost at the
        gate a moment (or minutes) ago, or a second box on a live counted vehicle — continues
        that vehicle's crossing state instead of starting a fresh, countable life.
        Only on the vehicle's own side of the line (or inside the jitter band): a vehicle
        tracked in from the OTHER side is the next vehicle, never the old one."""
        best, best_iou, best_state, best_lost = None, RESUME_IOU, None, False
        for tid, (box, ts, state) in self.lost.items():
            if state is None or now - ts > RESUME_S:
                continue
            iou = _iou(box, bbox)
            if iou >= best_iou and (cur_sign == state[3] or abs(cur_dist) < clear):
                best, best_iou, best_state, best_lost = tid, iou, state, True
        for tid, tr in self.tracks.items():
            if tr.crossings == 0:
                continue
            iou = _iou(tr.bbox, bbox)
            if iou >= best_iou and (cur_sign == tr.side or abs(cur_dist) < clear):
                best, best_iou, best_state, best_lost = tid, iou, tr.state(), False
        if best is None:
            return None
        if best_lost:
            self.lost.pop(best, None)
        return best, best_state

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
            best_id = assigned.get(di)
            if best_id is not None:
                tr = self.tracks[best_id]
                x1, y1, x2, y2 = d["bbox"]
                edge = _touches_edge(d["bbox"], frame_size)
                if edge and tr.full_w:
                    # box clipped by the left/right picture edge: its centre is NOT the vehicle's
                    # centre. We know how wide the vehicle really is (seen fully a moment ago), so
                    # rebuild the true bottom-centre instead of refusing to count the crossing.
                    if x2 >= frame_size[0] - 3:
                        cur_ref = (int(x1 + tr.full_w / 2), y2)
                    else:
                        cur_ref = (int(x2 - tr.full_w / 2), y2)
                    edge = False
                elif not edge:
                    tr.full_w = x2 - x1
            cur_dist = _side(cur_ref, a, b) / line_len
            cur_sign = self._sign(cur_dist)
            clear = self.clear_px(d["bbox"])
            if best_id is not None:
                prev_sign = tr.side
                prev_ref = tr.ref
                prev_c = tr.centroid
                prev_dist = tr.dist
                dt = max(0.0, now - tr.last_ts)
                jump = math.hypot(cur_ref[0] - prev_ref[0], cur_ref[1] - prev_ref[1])
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
                if edge:
                    steady = False  # partly outside the picture and we never saw it whole: position unknown
                # the box suddenly doubled / halved: it swallowed the guard standing in front (or lost half
                # the vehicle behind a pole) — its bottom-centre is not where the wheels are. Not a move.
                pw, ph = max(tr.bbox[2] - tr.bbox[0], 8), max(tr.bbox[3] - tr.bbox[1], 8)
                glitch = not (GLITCH_RATIO_LO <= (x2 - x1) / pw <= GLITCH_RATIO_HI
                              and GLITCH_RATIO_LO <= (y2 - y1) / ph <= GLITCH_RATIO_HI)
                if glitch:
                    steady = False
                if steady and dt > 1e-3:
                    v = ((c[0] - prev_c[0]) / dt, (c[1] - prev_c[1]) / dt)
                    tr.vel = v if tr.vel is None else (0.5 * tr.vel[0] + 0.5 * v[0], 0.5 * tr.vel[1] + 0.5 * v[1])
                tr.centroid = c
                tr.bbox = d["bbox"]
                if not glitch:
                    tr.ref = cur_ref  # a glitch keeps the last good reference: the next real step is judged from there
                    tr.dist = cur_dist
                tr.labels[d["label"]] += 1
                tr.label_w[d["label"]] += float(d.get("conf", 1.0))
                tr.disappeared = 0
                tr.last_ts = now
                if steady:
                    self._push_run(tr, cur_dist - prev_dist, now)
                    if _hits_segment(prev_ref, cur_ref, a, b):
                        tr.seg_hit = True
                # clearly on this side: far enough past the line, or arrived here in one real run
                sure = abs(cur_dist) >= clear or (abs(tr.run) >= clear and self._sign(tr.run) == cur_sign)

                # re-arm only once the vehicle is clearly past the line it just crossed
                if not tr.armed and abs(cur_dist) >= clear:
                    tr.armed = True

                raw_flip = prev_sign != 0 and cur_sign != 0 and cur_sign != prev_sign
                # the step that made it 'sure' may be a few detections after the one that touched
                # the drawn segment -> remember that the path did cross the yellow segment
                on_segment = _hits_segment(prev_ref, cur_ref, a, b) or (tr.seg_hit and -0.3 <= _along(cur_ref, a, b) <= 1.3)
                crossed = raw_flip and steady and sure and on_segment
                appeared_at_line = (
                    tr.crossings == 0
                    and self.near_band > 0
                    and cur_sign != 0
                    and abs(tr.start_dist) <= self.near_band
                    and abs(cur_dist) >= 1.5 * self.near_band
                    and tr.start_side in (0, cur_sign)  # born ON the line (0): whichever way it drives off
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
                    tr.side = cur_sign
                    tr.seg_hit = False
                elif raw_flip:
                    tr.last_reject = self._reject_reason(edge, steady, sure, cur_dist, clear, jump, max_jump,
                                                         on_segment, tr, gap_ok, repeat, glitch)
                    self.rejects.append({"track_id": best_id, "label": tr.label, "bbox": d["bbox"],
                                         "to_side": cur_sign, "reason": tr.last_reject,
                                         "kind": "jitter" if steady and not sure else "reject"})
                if sure and steady:
                    tr.side = cur_sign  # commit only a CLEAR position; a wobble inside the band changes nothing
                    tr.seg_hit = False
            else:
                tid = self.next_id
                self.next_id += 1
                # born inside the jitter band: side unknown until it clearly moves off the line
                # (appeared-at-line then counts whichever way it drives). Beyond the near band a
                # birth is far enough from the line to trust its side.
                unsure_r = min(clear, self.near_band) if self.near_band > 0 else 0.0
                side0 = cur_sign if abs(cur_dist) >= unsure_r else 0
                tr = Track(tid, c, d["bbox"], d["label"], side0, cur_dist, ts=now, conf=float(d.get("conf", 1.0)))
                tr.start_edge = _touches_edge(d["bbox"], frame_size)
                if not tr.start_edge:
                    tr.full_w = d["bbox"][2] - d["bbox"][0]
                src = self._inherit_source(d["bbox"], cur_dist, cur_sign, clear, now)
                if src is not None:
                    tr.inherit(src[0], src[1])
                    self.rejects.append({"track_id": tid, "label": d["label"], "bbox": d["bbox"], "to_side": tr.side,
                                         "reason": "", "kind": "resumed", "from": src[0]})
                self.tracks[tid] = tr
                used_track_ids.add(tid)

        if self.merge_twins:
            self._merge_twins()

        # Age out unmatched tracks (both the frame count AND the wall-clock gap must be exceeded)
        for tid in list(self.tracks.keys()):
            if tid not in used_track_ids:
                tr = self.tracks[tid]
                tr.disappeared += 1
                if tr.disappeared > self.max_disappeared and (now - tr.last_ts) >= self.lost_after_s:
                    if tr.crossings == 0 and tr.start_side != 0 and tr.side != 0 and tr.side != tr.start_side:
                        # it ended up on the other side of the line without ever being counted
                        self.rejects.append({"track_id": tid, "label": tr.label, "bbox": tr.bbox, "to_side": tr.side,
                                             "reason": tr.last_reject or "side badla par koi crossing count nahi hui",
                                             "kind": "possible-miss"})
                    self.lost[tid] = (tr.bbox, now, tr.state() if tr.crossings else None)
                    del self.tracks[tid]
        self.lost = {k: v for k, v in self.lost.items() if now - v[1] < (RESUME_S if v[2] else 120)}
        return crossings

    def _merge_twins(self):
        """An UNCOUNTED live track glued (IoU >= TWIN_IOU) to a COUNTED one for TWIN_FRAMES updates
        is a second box on the same object: it inherits the counted state (side, disarmed,
        last direction) so it can never count that bicycle/person again."""
        for tid, tr in self.tracks.items():
            if tr.crossings:
                continue
            src = next((o for oid, o in self.tracks.items()
                        if oid != tid and o.crossings and _iou(o.bbox, tr.bbox) >= TWIN_IOU), None)
            if src is None:
                tr.twin_frames = 0
                continue
            tr.twin_frames += 1
            if tr.twin_frames >= TWIN_FRAMES:
                tr.inherit(src.id, src.state())
                self.rejects.append({"track_id": tid, "label": tr.label, "bbox": tr.bbox, "to_side": tr.side,
                                     "reason": "", "kind": "resumed", "from": src.id})

    @staticmethod
    def _reject_reason(edge, steady, sure, cur_dist, clear, jump, max_jump, on_segment, tr, gap_ok, repeat,
                       glitch=False):
        if edge:
            return "box picture ke kinare ko chhoo raha tha (aadhi gaadi bahar)"
        if glitch:
            return "box ka size ek step me dugna/aadha ho gaya (saath khade aadmi/gate ko box me le liya?) — position bharosemand nahi"
        if not steady:
            return f"ek step me {jump:.0f}px jump (limit {max_jump:.0f}px) — teleport/galat match"
        if not sure:
            return (f"line par khadi gaadi ka box hil raha hai (jitter): sirf {abs(cur_dist):.0f}px paar, "
                    f"saaf paar ke liye {clear:.0f}px chahiye")
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
