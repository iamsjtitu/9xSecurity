"""9x Security - Vehicle detection using YOLOv8 (offline, free)."""
import os

import config

# COCO class ids that represent vehicles.
COCO_VEHICLES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}

MODEL_FILES = {"fast": "yolov8n.pt", "accurate": "yolov8s.pt"}


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
        class_ids = [cid for cid, name in COCO_VEHICLES.items() if name in self.allowed]
        # agnostic_nms: one vehicle must not become two boxes (car + truck) -> double count
        results = self.model(
            frame, verbose=False, conf=self.conf, classes=class_ids, agnostic_nms=True, imgsz=self.imgsz
        )
        dets = []
        for r in results:
            if r.boxes is None:
                continue
            for box in r.boxes:
                cid = int(box.cls[0])
                name = COCO_VEHICLES.get(cid)
                if name and name in self.allowed:
                    x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                    dets.append(
                        {"bbox": (x1, y1, x2, y2), "label": name, "conf": float(box.conf[0])}
                    )
        return dets
