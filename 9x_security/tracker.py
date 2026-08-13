"""9x Security - Lightweight centroid tracker + line-crossing detection."""
import math


def _side(p, a, b):
    """Signed cross product => which side of line (a->b) point p is on."""
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


class Track:
    def __init__(self, tid, centroid, bbox, label, side):
        self.id = tid
        self.centroid = centroid
        self.bbox = bbox
        self.label = label
        self.side = side          # last known side sign (-1 / 0 / 1)
        self.disappeared = 0
        self.counted = False      # already logged a crossing


class CentroidTracker:
    def __init__(self, max_disappeared=20, max_distance=90):
        self.next_id = 1
        self.tracks = {}
        self.max_disappeared = max_disappeared
        self.max_distance = max_distance

    @staticmethod
    def _centroid(bbox):
        x1, y1, x2, y2 = bbox
        return ((x1 + x2) // 2, (y1 + y2) // 2)

    def update(self, detections, line):
        """
        detections: list of {bbox,label}
        line: (a, b) two points in same coord space as detections
        Returns list of crossing events: {track_id, label, from_side, to_side}
        """
        a, b = line
        crossings = []
        inputs = []
        for d in detections:
            c = self._centroid(d["bbox"])
            inputs.append((c, d))

        used_track_ids = set()
        # Match each detection to nearest existing track.
        for c, d in inputs:
            best_id, best_dist = None, self.max_distance + 1
            for tid, tr in self.tracks.items():
                if tid in used_track_ids:
                    continue
                dist = math.hypot(c[0] - tr.centroid[0], c[1] - tr.centroid[1])
                if dist < best_dist:
                    best_dist, best_id = dist, tid

            cur_sign = self._sign(_side(c, a, b))
            if best_id is not None and best_dist <= self.max_distance:
                tr = self.tracks[best_id]
                prev_sign = tr.side
                tr.centroid = c
                tr.bbox = d["bbox"]
                tr.label = d["label"]
                tr.disappeared = 0
                used_track_ids.add(best_id)
                if not tr.counted and prev_sign != 0 and cur_sign != 0 and cur_sign != prev_sign:
                    crossings.append(
                        {
                            "track_id": best_id,
                            "label": tr.label,
                            "bbox": d["bbox"],
                            "from_side": prev_sign,
                            "to_side": cur_sign,
                        }
                    )
                    tr.counted = True
                tr.side = cur_sign
            else:
                tid = self.next_id
                self.next_id += 1
                self.tracks[tid] = Track(tid, c, d["bbox"], d["label"], cur_sign)
                used_track_ids.add(tid)

        # Age out unmatched tracks.
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
