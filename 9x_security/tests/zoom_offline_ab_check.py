"""Is the E2E dog actually stopped by the zoom, or by the older sanitize rule? Run the real engine
offline on the E2E dog video with and without second_opinion."""
import os, sys, tempfile, pathlib
sys.path.insert(0, "/app/9x_security"); sys.path.insert(0, "/app/9x_security/tests")
import cv2
import config, detector, engine as eng, database
from zoom_real_yolo_check import make_video

LINE = {"x1": 0.05, "y1": 0.5, "x2": 0.95, "y2": 0.5}
scale = float(sys.argv[1]) if len(sys.argv) > 1 else 0.45
name = sys.argv[2] if len(sys.argv) > 2 else "night_dog.jpg"
CONF = float(sys.argv[3]) if len(sys.argv) > 3 else 0.3
path = f"/tmp/nx_zoom_dbg_{scale}.mp4"
make_video(name, scale, path, seconds=20)
path_m, _ = detector.resolve_model_path("fast")


def run(zoom):
    cfg = {**config.DEFAULTS, "enable_plate": False, "enable_person": True, "confidence": CONF, "line": LINE}
    db = database.EventDB(db_path=str(pathlib.Path(tempfile.mkdtemp()) / "x.db"))
    det = detector.VehicleDetector(path_m, conf=CONF, allowed=config.detect_classes(cfg))
    if not zoom:
        det.second_opinion = None
    e = eng.SecurityEngine(cfg=cfg, db=db, detector=det, plate_reader=None)
    e.notifier.enabled = False
    logs = []
    eng.clog = lambda m: logs.append(m)
    cap = cv2.VideoCapture(path)
    t = [1000.0]; rt = eng.time.time; eng.time.time = lambda: t[0]
    ev = []
    pers_frames = 0
    try:
        while True:
            ok, f = cap.read()
            if not ok:
                break
            t[0] += 1 / 12
            ev += e.process_frame(f)[1]
            pers_frames += any(d["label"] == "person" for d in e.last_dets)
        ev += e.flush_pending(force=True)
    finally:
        eng.time.time = rt
    for x in ev:
        if x.get("image_path") and os.path.exists(x["image_path"]):
            os.remove(x["image_path"])
    print(f"zoom={zoom}: events={[(x['direction'], x['category']) for x in ev]} frames_with_person_after_sanitize={pers_frames}")
    for m in logs:
        if "zoom" in m or "person" in m:
            print("   ", m)


run(False)
run(True)
