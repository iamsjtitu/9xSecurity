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

## Implemented (update 2026-06 #3)
- wa.9x.design docs are now PUBLIC (https://wa.9x.design/docs). whatsapp.py rewritten to
  the exact documented v2 API (old 5-format guessing removed):
  • Text: POST {base}/api/v2/sendMessage — multipart form: phonenumber, text
  • Photo: POST {base}/api/v2/sendMessageFile — multipart: phonenumber, file, caption, filename
  • Auth: Authorization: Bearer <API_KEY>; recipients normalized to digits-only
  Verified: 9/9 test_engine.py pass + LIVE endpoint sanity (both URLs return 401
  "Invalid API token" with fake key → correct URL/format, not 404). Real delivery
  pending user's API key + connected session (Settings → Send Test Message).
- UI labels updated: "X-API-Key" → "API Key (Bearer)"


- Core self-tested headless (test_engine.py): line-crossing event logging, snapshot save,
  tracker id persistence, side-of-line sign, auth hash/verify, WhatsApp text payload format,
  disabled-notifier no-op, real YOLO load + detect on sample image (bus @0.87). ALL PASS.
- NOT tested in this environment (headless Linux, no camera): PyQt5 GUI render, live RTSP,
  and the REAL wa.9x.design media endpoint (docs are login-gated; multipart used + text fallback).
  These run on the user's Windows machine.

## Implemented (update 2026-06 #5) — RTSP @ fix + WHITE theme
- BUG FIX (user URL rtsp://admin:Admin@123@192.168.31.65:554/stream1 no video):
  '@' in password broke FFmpeg URL parsing. engine.normalize_rtsp_url() percent-encodes
  userinfo (Admin@123 -> Admin%40123), idempotent. VideoThread opens via CAP_FFMPEG with
  OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp (forced) + default-backend fallback;
  reconnect uses same path; Hindi error message on failure.
- WHITE THEME: BASE_STYLE + all inline styles rewritten light (#f6f8fb window, #ffffff
  panels, #1e293b text, #1f6feb accent, video area #eef2f7, muted #64748b, entry #15803d,
  exit #b45309, error #dc2626). Zero dark colors remain (grep-verified).
- Verified by testing agent /app/test_reports/iteration_2.json: 12/12 tests pass, URL fix
  confirmed with live cv2 attempt (correct host targeted). Visual theme check = user on Windows.


## Implemented (update 2026-06 #4) — Folder-zip build (user chose option c)
- Workflow now builds ONEDIR (folder) instead of onefile: build 2-3x faster (~8-15 min),
  app starts in seconds (no 1.5GB self-extract per launch). Zips dist/9xSecurity ->
  9xSecurity-v{ver}.zip via Compress-Archive; release asset is the zip. timeout 60 min.
- config.py frozen-aware paths: user data (config.json/events.db/snapshots/wa_log) next to
  exe (survives updates); yolov8n.pt resolved from _internal (sys._MEIPASS).
- updater.py: _pick_asset prefers .zip (legacy .exe fallback), apply_update() extracts zip,
  finds app root, batch xcopy /E /Y (no delete -> user data safe) + restart; main.py uses
  apply_update + ext-aware temp filename.
- Verified: 11/11 test_engine.py pass (new: _pick_asset zip-first, _zip_app_root) +
  workflow YAML valid. Windows-side install/restart flow needs real-device validation.


## Implemented (update 2026-06 #6) — Setup installer + auto-version (user chose installer)
- User: "zip mat karo" + version auto-change per release. New flow:
  • CI auto-version: 1.0.{github.run_number}; sed-patches updater.py APP_VERSION pre-build
  • onedir build kept (fast), then Inno Setup (preinstalled on runner, choco fallback)
    compiles installer.iss -> dist/9xSecuritySetup-v1.0.N.exe; release asset = installer only
  • installer.iss: per-user install to {localappdata}\9xSecurity (writable, no admin),
    desktop + start menu shortcuts, CloseApplications=force, [Run] relaunch postinstall
  • updater: _pick_asset prefers Setup .exe > .zip > any .exe; apply_update runs installer
    /VERYSILENT /FORCECLOSEAPPLICATIONS (upgrade-in-place, auto relaunch); legacy
    single-exe replace removed; zip path kept
  • Workflow triggers: push main/master + dispatch only (tag trigger removed - tags now
    created by release action)
- Verified: 12/12 test_engine.py pass (installer-first asset pick, 1.0.12>1.0.2 compare),
  YAML valid, sed version patch tested. Inno compile + install/update flow = user on Windows.


## Implemented (update 2026-06 #7) — Camera Test diagnostics (RTSP still-not-working report)
- User's camera (LAN 192.168.31.65) not reproducible from container; built diagnostic tooling:
  • engine.probe_rtsp(): URL fix -> socket reachability (3s) -> capture TCP -> UDP with
    frame verification; Hindi step results + hints (VLC check, stream1/stream2, creds)
  • Main window: new "Test" button beside Connect (ProbeThread, result message box)
  • VideoThread._open: TCP -> UDP -> default backend, each with 5s frame-arrival check;
    ffmpeg option now 'timeout;5000000' (modern) instead of deprecated 'stimeout'
  • opencv-python==4.10.0.84 pin confirmed (avoids opencv 4.11 RTSP no-frames bug #27091)
- Verified by testing agent /app/test_reports/iteration_3.json: 13/13 pass, all logic checks
  green. Live camera confirmation must come from user via Test button on Windows build.

## Backlog / Next

- P1: Vehicle re-identification to avoid double counting if it lingers on line
- P1: CSV/Excel export of event log
- P2: Multi-camera support (separate entry/exit cams)
- P2: Plate-based search + email/Telegram alert on entry
- P2: Auto-delete snapshots older than N days
