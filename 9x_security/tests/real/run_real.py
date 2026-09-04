"""Run the plate pipeline on the user's real gate snapshots (960x540 WhatsApp copies)."""
import glob
import os
import sys
import time

import cv2

sys.path.insert(0, "/app/9x_security")
from detector import VehicleDetector  # noqa: E402
from plate_reader import PlateReader  # noqa: E402

det = VehicleDetector(model_path="/app/9x_security/yolov8s.pt", conf=0.35)
pr = PlateReader()
pr.warmup()
scale = float(os.environ.get("UP", "2.0"))  # snapshots are 960 wide; camera is 1920 -> upscale to mimic
for f in sorted(glob.glob("/app/9x_security/tests/real/wa*.jpg")):
    img = cv2.imread(f)
    dets = [d for d in det.detect(img) if d["label"] in ("truck", "car", "bus")]
    if not dets:
        print(f, "no vehicle"); continue
    d = max(dets, key=lambda d: (d["bbox"][2] - d["bbox"][0]) * (d["bbox"][3] - d["bbox"][1]))
    x1, y1, x2, y2 = [int(v) for v in d["bbox"]]
    crop = img[max(0, y1):y2, max(0, x1):x2]
    if scale != 1.0:
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    t0 = time.time()
    plate, detail = pr.read_many([crop], budget_s=float(os.environ.get("BUDGET", "8")))
    print(f"{os.path.basename(f)} {d['label']} crop={crop.shape[1]}x{crop.shape[0]} -> {plate or 'Not detected'} ({detail}) {time.time()-t0:.1f}s")
    for t in pr.last_trace:
        print("    ", t[:200])
    cv2.imwrite(f.replace(".jpg", "_crop.png"), crop)
