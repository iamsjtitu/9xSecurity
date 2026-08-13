"""9x Security - Vehicle detection using YOLOv8 (offline, free)."""
import os

import config

# COCO class ids that represent vehicles.
COCO_VEHICLES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}


class VehicleDetector:
    def __init__(self, model_path=None, conf=0.40, allowed=None):
        self.model_path = model_path or config.MODEL_PATH
        self.conf = conf
        self.allowed = set(allowed or ["car", "truck", "bus"])
        self.model = None
        self._load()

    def _load(self):
        from ultralytics import YOLO

        # If a local weights file exists use it, else ultralytics downloads yolov8n.
        path = self.model_path if os.path.exists(self.model_path) else "yolov8n.pt"
        self.model = YOLO(path)

    def set_allowed(self, allowed):
        self.allowed = set(allowed)

    def detect(self, frame):
        """Return list of {bbox:(x1,y1,x2,y2), label, conf}."""
        class_ids = [cid for cid, name in COCO_VEHICLES.items() if name in self.allowed]
        results = self.model(frame, verbose=False, conf=self.conf, classes=class_ids)
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
