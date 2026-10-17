"""9x Security - Vehicle detection using YOLOv8 (offline, free)."""
import os

import config

# COCO class ids we detect, grouped into user-facing categories. Animals are detected only as
# CONTEXT (never alerted): a dog/cow that YOLO would otherwise call 'person' in a few frames
# gets its real label and is voted out of the person category by the tracker.
ANIMAL_LABELS = ("dog", "cat", "cow", "horse", "sheep", "bird", "elephant", "bear", "zebra", "giraffe")
COCO_CLASSES = {0: "person", 1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck",
                14: "bird", 15: "cat", 16: "dog", 17: "horse", 18: "sheep", 19: "cow"}
COCO_VEHICLES = COCO_CLASSES  # backwards-compat alias
CATEGORY = {
    "person": "person",
    "bicycle": "two_wheeler", "motorcycle": "two_wheeler",
    "car": "vehicle", "truck": "vehicle", "bus": "vehicle",
    **{a: "animal" for a in ANIMAL_LABELS},
}


def category_of(label):
    """person / two_wheeler / vehicle / animal for a detected label."""
    return CATEGORY.get(label, "vehicle")

MODEL_FILES = {"fast": "yolov8n.pt", "accurate": "yolov8s.pt"}
# every COCO animal: a far dog at night is 'person' at full-frame scale, but zoomed in YOLO sees the animal
ZOOM_ANIMALS = {14: "bird", 15: "cat", 16: "dog", 17: "horse", 18: "sheep", 19: "cow", 20: "elephant",
                21: "bear", 22: "zebra", 23: "giraffe"}


def _iou(a, b):
    ix1, iy1, ix2, iy2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def resolve_model_path(pref="auto"):
    """'fast' -> yolov8n, 'accurate' -> yolov8s, 'auto' -> yolov8s if bundled else n.
    Returns (path, tier)."""
    res_dir = os.path.dirname(config.MODEL_PATH)
    s_path = os.path.join(res_dir, MODEL_FILES["accurate"])
    n_path = os.path.join(res_dir, MODEL_FILES["fast"])
    if pref == "fast":
        return n_path, "fast"
    if pref == "accurate":
        return (s_path if os.path.exists(s_path) else n_path), ("accurate" if os.path.exists(s_path) else "fast")
    if os.path.exists(s_path):
        return s_path, "accurate"
    return n_path, "fast"


class VehicleDetector:
    def __init__(self, model_path=None, conf=0.40, allowed=None, imgsz=640):
        self.model_path = model_path or config.MODEL_PATH
        self.conf = conf
        self.imgsz = imgsz
        self.allowed = set(allowed or ["car", "truck", "bus"])
        self.model = None
        self.model_name = os.path.basename(self.model_path)
        self._load()

    def _load(self):
        from ultralytics import YOLO

        try:  # leave one core for the video decoder / UI / HTTP server
            import torch

            torch.set_num_threads(int(os.environ.get("NX_TORCH_THREADS") or max(1, (os.cpu_count() or 2) - 1)))
        except Exception:
            pass
        # If a local weights file exists use it, else ultralytics downloads yolov8n.
        path = self.model_path if os.path.exists(self.model_path) else "yolov8n.pt"
        self.model_name = os.path.basename(path)
        self.model = YOLO(path)

    def set_allowed(self, allowed):
        self.allowed = set(allowed)

    def detect(self, frame):
        """Return list of {bbox:(x1,y1,x2,y2), label, conf}."""
        class_ids = [cid for cid, name in COCO_CLASSES.items() if name in self.allowed]
        # agnostic_nms: one vehicle must not become two boxes (car + truck) -> double count
        # (several cameras = several engines detecting concurrently: measured faster in total than
        # a turn-by-turn lock, so no serialisation here)
        results = self.model(
            frame, verbose=False, conf=self.conf, classes=class_ids, agnostic_nms=True, imgsz=self.imgsz
        )
        dets = []
        for r in results:
            if r.boxes is None:
                continue
            for box in r.boxes:
                cid = int(box.cls[0])
                name = COCO_CLASSES.get(cid)
                if name and name in self.allowed:
                    x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                    dets.append(
                        {"bbox": (x1, y1, x2, y2), "label": name, "conf": float(box.conf[0])}
                    )
        return dets

    def second_opinion(self, frame, bbox, grow=1.5, imgsz=320):
        """Zoomed second look at ONE 'person' box: a square crop around it (grow x its size) is run
        through the model with person + every animal class. At full-frame scale a far dog is a
        30x80 px blob YOLO calls 'person'; at crop scale it is a dog. Returns
        {person: conf, animal: conf, animal_label} for boxes on the object, or None."""
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = bbox
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        half = max(x2 - x1, y2 - y1, 8) * grow
        cx1, cy1 = int(max(0, cx - half)), int(max(0, cy - half))
        cx2, cy2 = int(min(w, cx + half)), int(min(h, cy + half))
        crop = frame[cy1:cy2, cx1:cx2]
        if crop.size == 0:
            return None
        obj = (x1 - cx1, y1 - cy1, x2 - cx1, y2 - cy1)
        ocx, ocy = (obj[0] + obj[2]) / 2, (obj[1] + obj[3]) / 2
        results = self.model(crop, verbose=False, conf=0.15, classes=[0, *ZOOM_ANIMALS], agnostic_nms=False, imgsz=imgsz)
        out = {"person": 0.0, "animal": 0.0, "animal_label": ""}
        for r in results:
            if r.boxes is None:
                continue
            for box in r.boxes:
                bx = tuple(map(int, box.xyxy[0].tolist()))
                on_object = _iou(bx, obj) >= 0.3 or (bx[0] <= ocx <= bx[2] and bx[1] <= ocy <= bx[3])
                if not on_object:
                    continue
                cid, c = int(box.cls[0]), float(box.conf[0])
                if cid == 0:
                    out["person"] = max(out["person"], c)
                elif cid in ZOOM_ANIMALS and c > out["animal"]:
                    out["animal"], out["animal_label"] = c, ZOOM_ANIMALS[cid]
        return out
