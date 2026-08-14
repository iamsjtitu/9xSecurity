# 9x Security — PRD

## Problem Statement
Desktop software "9x Security": gate par laga IP camera (RTSP) se live feed lekar
jab bhi 4-wheeler/truck andar aaye ya bahar jaaye, uska photo date+time ke saath
save ho. Single camera + line-crossing se Entry/Exit, vehicle type label, aur
number plate bhi capture karna hai. Platform: Windows desktop. AI: offline & free.

## User Choices
- RTSP IP camera
- Offline free AI model (YOLOv8)
- Single camera, screen par line kheench kar Entry/Exit (line-crossing direction)
- Windows desktop app
- Snapshot ke saath: vehicle type + Entry/Exit label + number plate (OCR)

## Architecture (standalone Python desktop app — /app/9x_security)
- `main.py`     — PyQt5 GUI (live feed, draw line, event gallery, counters, filters)
- `engine.py`   — per-frame pipeline: detect → track → line-cross → snapshot + DB log
- `detector.py` — YOLOv8n vehicle detection (car/truck/bus/motorcycle), offline
- `tracker.py`  — centroid tracker + signed-line-crossing (Entry/Exit direction)
- `plate_reader.py` — EasyOCR number plate (optional, lazy loaded)
- `database.py` — SQLite events store (timestamp, date, time, type, direction, plate, image_path)
- `config.py`   — persisted settings (config.json)
- `yolov8n.pt`  — bundled model (offline)
- Snapshots saved to `snapshots/YYYY-MM-DD/`

## Implemented (2026-06)
- Full detection → tracking → line-crossing → snapshot + SQLite logging pipeline
- Entry/Exit direction via user-drawn line + swap control
- Vehicle type label, number plate OCR toggle, per-class filters
- GUI: live feed, draw-line by 2 clicks, event table with thumbnails, date/direction filters, today counters, snapshot preview, open-folder
- README with Windows install/run + PyInstaller .exe build steps
- WhatsApp alerts (wa.9x.design): on every Entry/Exit sends snapshot photo (multipart) with caption
  {direction, vehicle type, time, plate}; text-alert fallback if media send fails; logs to wa_log.txt
- App LOGIN screen (username/password, PBKDF2 hashed, default admin/9xsecurity)
- Settings dialog: WhatsApp (enable, base URL, API key, recipients, send-image), Account
  (wa.9x email/password), Login/Security (change username & password)
- .exe stack: Python (PyQt5 GUI + OpenCV + YOLOv8), packaged via PyInstaller

## Implemented (update 2026-06 #2)
- WhatsApp "Send Test Message" button in Settings → verifies API key + recipients live
- Reliable photo delivery: image send auto-tries 5 known gateway formats (multipart file/media/image,
  JSON base64, JSON media-object) and uses the first that returns 2xx; text fallback otherwise
- GitHub Actions CI (.github/workflows/build-windows.yml): auto-runs on every push to
  main/master (Save to GitHub) and on v* tags; builds Windows .exe (PyInstaller onefile)
  and auto-publishes a Release tagged v{APP_VERSION from updater.py} with the exe attached
  (fixed June 2026: earlier it only ran on tag push, so user had to run workflow manually)
- Build "stuck" fix (June 2026): PyInstaller onefile with torch/easyocr has a 10-25 min quiet
  phase (looked stuck at matplotlib font cache). Workflow now: timeout-minutes 90, pip cache,
  pre-builds matplotlib font cache, --collect-data ultralytics/easyocr,
  --exclude-module tkinter/IPython/pytest/notebook. Verified by testing agent
  (/app/test_reports/iteration_1.json, 9/9 regression pass). Real Windows run still needs
  user push via Save to GitHub.
- In-app auto-update (Settings → Updates): checks GitHub latest release, downloads .exe, self-replaces
  and restarts (frozen mode); updater.py APP_VERSION drives version compare

## Testing status
- Core self-tested headless (test_engine.py): line-crossing event logging, snapshot save,
  tracker id persistence, side-of-line sign, auth hash/verify, WhatsApp text payload format,
  disabled-notifier no-op, real YOLO load + detect on sample image (bus @0.87). ALL PASS.
- NOT tested in this environment (headless Linux, no camera): PyQt5 GUI render, live RTSP,
  and the REAL wa.9x.design media endpoint (docs are login-gated; multipart used + text fallback).
  These run on the user's Windows machine.

## Backlog / Next
- P1: Vehicle re-identification to avoid double counting if it lingers on line
- P1: CSV/Excel export of event log
- P2: Multi-camera support (separate entry/exit cams)
- P2: Plate-based search + email/Telegram alert on entry
- P2: Auto-delete snapshots older than N days
