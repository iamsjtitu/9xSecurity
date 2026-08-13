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
