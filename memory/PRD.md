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

## Implemented (update 2026-06 #8) — Update 404 fix, no-link updates, bigger fonts, disconnect fix
- Updates: DEFAULT_REPO baked into updater.py at CI (sed $GITHUB_REPOSITORY); Updates tab repo
  input REMOVED — one-click Check for Updates; check_latest returns empty on GitHub 404
  (no release yet) -> friendly Hindi message instead of error
- Fonts: base 13px->15px, header 26px, login 28px, stat 36px, paddings up
- Disconnect: signals disconnected instantly + non-blocking request_stop(); _open now
  instance method honoring _running (returns None on abort); run() handles None cap
- Verified by testing agent /app/test_reports/iteration_4.json: 14/14 pass incl. LIVE GitHub
  404/normal checks + CI-bake sed simulation. Visual font/disconnect click = user on Windows.

## Implemented (update 2026-06 #9) — Slow-camera hardening + camera_log.txt
- User's blank-video persists on new build; root cause pending user's Test dialog output.
- FFMPEG_OPTS: timeout+analyzeduration(10s)+probesize(5MB)+max_delay on all rtsp opens;
  first-frame wait probe 10s / live connect 12s per transport (was 5-6s; slow cameras like
  Tapo take >5s for first keyframe - likely why VLC worked but app gave up)
- engine.clog() -> camera_log.txt (app folder) records every probe/connect attempt;
  Test result now in copyable QPlainTextEdit dialog + one-line summary in status bar
- Verified testing agent /app/test_reports/iteration_5.json: 20/20 pass (new
  test_iter5_diagnostics.py by testing agent + 14 regression). AWAITING USER: Test dialog
  text + camera_log.txt from Windows build to pinpoint camera-side issue.

## Implemented (update 2026-06 #10) — VLC-grade FFmpeg fallback engine (user: "VLC me chal raha hai")
- Since VLC plays the stream, camera/creds/path are fine; OpenCV's linked ffmpeg fails it.
- engine.FFmpegPipeSource: spawns bundled ffmpeg (imageio-ffmpeg==0.6.0) with rtsp tcp +
  timeout/analyzeduration/probesize, scales to 960x540 bgr24 rawvideo pipe; cv2-compatible
  read()/isOpened()/release(); stderr -> ffmpeg_err.txt; CREATE_NO_WINDOW on Windows
- VideoThread._open ladder: cv2 TCP -> cv2 UDP -> FFmpegPipeSource -> default cv2
- probe_rtsp new step "Video stream (FFmpeg engine)" after TCP/UDP
- Packaging: --collect-binaries imageio_ffmpeg in workflow + README
- Verified testing agent /app/test_reports/iteration_6.json: 30/30 pass incl. real lavfi
  video decode through the pipe (shape/std checks), fast-fail on unreachable, cleanup.
  Live camera confirmation pending user on Windows build.

## Implemented (update 2026-06 #11) — Private-repo update fix (GitHub token support)
- RCA: Save-to-GitHub repos are PRIVATE by default; /releases/latest returns 404 without
  auth, so app showed "no release/no update" even though releases existed.
- updater.py rewritten requests-based: check_latest(repo, token) -> Bearer auth; 404 =>
  ('',None,''); 401/403 => Hindi RuntimeError (token/rate-limit hint); token flow picks API
  asset 'url' (needed for private asset download; requests drops Authorization on S3 redirect)
- Settings > Updates: optional GitHub Token field (config gh_token); "No release" dialog now
  explains private repo -> fine-grained token (Contents: Read-only) OR make repo public
- sed anchors (APP_VERSION/DEFAULT_REPO) preserved for CI bake
- Verified testing agent /app/test_reports/iteration_7.json: 50/50 pass (mocks + live public
  GitHub API + sed-bake simulation). USER MUST: install newest Setup manually ONCE (old build
  lacks token support), then either make repo public or paste token.

## Implemented (update 2026-06 #12) — Blank video fix: windowed-mode print crash guard
- User screenshot: Test all GREEN incl. "Video aa raha hai (848x480)" (camera now .105/stream2)
  yet live monitor blank. RCA: PyInstaller --windowed => sys.stdout/stderr None; ultralytics/
  torch prints crash VideoThread at model load (probe path never imports ultralytics).
- config.ensure_std_streams(): frozen mode redirects None stdout/stderr -> app_log.txt at
  config import (before torch imports)
- VideoThread hardened: run() wrapper logs CRASH tracebacks -> camera_log.txt; model load
  failure => engine=None but RAW VIDEO STILL STREAMS; per-frame try/except (video continues
  on AI error, one-time status); clog stage markers; main() excepthook -> UNCAUGHT to clog
- Verified testing agent /app/test_reports/iteration_8.json: 51/51 pass incl. frozen-stdout
  simulation + engine-raises-still-shows-frame simulation. Visual confirm = user Windows.

## Implemented (update 2026-06 #13) — ELECTRON REBUILD (major architecture change)
- User: "Electron me bana ke do, sundar and smart". Choices: Electron(React) UI + Python
  engine in one installer; modern clean look; same features; faster builds; video confirmed
  working on old build before rebuild.
- New architecture:
  • service.py — FastAPI engine on 127.0.0.1:8971 (reuses engine/detector/tracker/plate/
    whatsapp/updater/database). Endpoints: /api/login (token), state, camera connect/
    disconnect/test, stream (MJPEG), line, swap, options, events, counts, snapshot (path-
    contained), settings GET/POST (+password change), whatsapp/test, update/check+apply.
    Serves electron/dist statically when present (container testing).
  • electron/ — React + Vite + Tailwind per /app/design_guidelines.json (dark slate-900
    sidebar + slate-50 body, #1f6feb accent, bento dashboard: camera col-8, stats/controls
    col-4, events table col-12, Hinglish strings, full data-testids). electron-main.js
    spawns engine exe (resources/engine/9xEngine.exe), health-waits, kills on quit;
    preload: openPath/quit.
  • engine.open_stream() shared ladder (tcp->udp->ffmpeg-pipe->default); legacy PyQt
    main.py no longer built (reference only); PyQt5 removed from requirements
    (+fastapi, uvicorn).
  • updater._run_installer now NSIS flags (/S --force-run) — installer is electron-builder
    NSIS 9xSecuritySetup-v{V}.exe (name has 'setup' so _pick_asset works).
  • Workflow: python engine (PyInstaller onedir 9xEngine from service.py) + yarn build +
    electron-builder NSIS; auto-version 1.0.{run_number} baked into updater.py AND
    electron/package.json; pip/yarn/electron caches. yarn.lock committed.
- Verified testing agent /app/test_reports/iteration_9.json: 71/71 backend pytest + FULL
  frontend E2E via playwright at :8971 (login, dashboard, settings tabs, line draw, swap,
  camera test modal, events filters, logout). Electron shell + Windows installer = user
  verification after Save to GitHub.

## Implemented (update 2026-06 #14) — Timing/Schedule system (user request)
- Settings > new "Timing" tab with 2 independent schedules (overnight windows supported,
  e.g. 18:00 -> 06:00):
  • WhatsApp alert window: outside it events still capture, but no WhatsApp (gated in
    WhatsAppNotifier.allowed_now via config.in_time_window; skip logged to wa_log.txt)
  • Capture window (default OFF = 24hr): outside it live video keeps showing but
    detection/snapshots/events pause; status shows "capture PAUSED (schedule...)"
- Config keys: wa_schedule_enabled/wa_start/wa_end, capture_schedule_enabled/
  capture_start/capture_end; exposed via /api/settings
- Tested (self): unit tests (in_time_window overnight/day/equal/invalid + notifier gating)
  pass, /api/settings roundtrip via curl, UI e2e (Timing tab save -> toast -> persisted)
  via playwright screenshot. Real overnight behavior = user on Windows.

## Implemented (update 2026-06 #15) — 7-day retention + Auto Delete + Entry/Exit filters
- Auto Delete (Settings > Timing > Records/Storage): toggle (default ON) + retention_days
  (default 7, clamp 1-365). service._purge_old runs at startup + every 6h: deletes DB rows
  older than cutoff + their snapshot files + whole old day-dirs. db.purge_older_than added.
- Events table upgraded: All/Entry/Exit segmented filter buttons, "Pichhle 7 din" quick
  date chips (Aaj/Kal/dates), live summary badges (N Entry / N Exit for current filter),
  date picker applies instantly, Show All; details via existing snapshot modal.
- Tested (self): unit (purge + direction filters), API (Entry/Exit filter results, retention
  roundtrip, purge removed only old event), UI E2E screenshots (filters switch rows +
  summary updates, chips render, auto-delete toggle saves). Demo data cleaned after test.

## Implemented (update 2026-06 #16) — Plate Search
- db.get_events(plate_filter): SQL LIKE %q% (case-insensitive); /api/events?plate=q
- Events table: plate search box (debounced 400ms, mono/uppercase, clear X). Non-empty
  query searches ALL dates (ignores date chips, shows hint line); clear restores date view.
  Works combined with Entry/Exit direction buttons.
- Tested (self): unit (partial/case-insensitive/none/direction-combo), API curl (hr26 -> 2
  correct rows), UI E2E (type HR26 -> 2 rows + hint, clear -> all rows). Demo data cleaned.

## Implemented (update 2026-06 #17) — CI fix: yarn.lock cache failure
- User's GH Actions build failed at setup-node: cache-dependency-path electron/yarn.lock
  missing. RCA: platform auto-commit NEVER commits yarn.lock files (untracked), so it never
  reaches GitHub. Fix: setup-node + electron cache keys now hash electron/package.json
  (committed), plain 'yarn install' (no --frozen-lockfile), node 20 -> 22 (deprecation).
- Verified testing agent /app/test_reports/iteration_10.json: YAML valid, zero yarn.lock
  refs, package.json git-tracked, fresh no-lockfile yarn install simulation SUCCESS,
  version-bake node -e works, 19/19 regression pass. Real run = user's next push.
- LEARNING (do not repeat): never depend on yarn.lock in CI for this repo.

## Implemented (update 2026-06 #18) — Security audit + hardening (verified iteration_11, 100%)
- Audit findings fixed: (1) default-password FORCE CHANGE on first login (login returns
  must_change_password; Login.jsx force-change form, min 6 chars, rejects default);
  (2) CORS locked to ['null','file://','http://localhost:5173'] (evil origins blocked);
  (3) /api/settings masks secrets (wa_api_key/gh_token/wa_account_password -> '' + *_set
  bools; empty POST keeps stored secret; whatsapp/test falls back to stored key);
  (4) token TTL 12h + /api/logout (UI logout calls it); (5) RTSP creds redacted in
  camera_log (engine.redact_url); (6) Electron open-path IPC allowlisted to dirs under
  home; (7) CSP meta + Google Fonts removed (system Segoe UI stack).
- Deferred (documented): config.json at-rest encryption (DPAPI), code-signing of installer,
  CI action SHA-pinning.
- Verified testing agent /app/test_reports/iteration_11.json: backend 100% + frontend 100%
  (force-change E2E, old default 401, masked placeholders). Password restored to default in
  container; user's real install will force-change on next login after update.
- NOTE (user's build error repeat): failed GH run used OLD workflow (yarn.lock/Node20) —
  user must Save to GitHub for new workflow; "Re-run failed jobs" reruns the old commit.

## Implemented (update 2026-06 #19) — System tray + durable WhatsApp Outbox (verified iteration_12, 100%)
- TRAY: closing window (X) hides to system tray instead of quitting; tray menu 'Open 9x
  Security' / 'Exit'; only Exit (or UI quit-app IPC) sets quitting flag, kills Python engine
  and quits; window-all-closed guarded; second-instance/tray click restores window; one-time
  Windows balloon tip on first hide. New app icon assets/icon.png (transparent shield+camera),
  wired as BrowserWindow icon, tray icon, and electron-builder win.icon (assets/** in files).
- OUTBOX: SQLite `outbox` table (database.py: add/pending/delete/mark_failed/count/purge).
  whatsapp.py: _deliver() per recipient (photo -> text fallback on provider reject; network
  error => queue full alert with image path); failed sends enqueued; flush_outbox() delivers
  pending, deletes only on provider 200 (duplicate-safe, lock-guarded, short-circuits batch
  on network error). service.py: _outbox_loop retries every 30s (OUTBOX_RETRY_SECONDS env),
  started in main(); outbox purged with retention_days; GET /api/outbox; outbox_pending in
  /api/state and /api/counts. engine.py passes db into notifier.
- UI: sidebar badge data-testid='outbox-pending-count' "WA Pending: X" (amber when >0),
  polled via /api/state every 2.5s.
- Tests: /app/9x_security/test_outbox.py (6/6), testing agent iteration_12: 18/18 backend,
  frontend badge 0->1->0 E2E, electron-main.js static review + node --check pass. No bugs.
- NOTE: tray behavior itself needs Windows to run — user must build/install new setup to see it.

## Implemented (update 2026-06 #20) — App-hang fix + Camera Zoom (verified iteration_13, 100%)
- HANG ROOT CAUSE: CameraPanel <img src={streamUrl()}> embedded Date.now() so every 2.5s
  state poll opened a NEW long-lived MJPEG multipart connection -> Chromium buffer pile-up ->
  full renderer freeze (user needed restart). FIX: canvas rendering polling GET /api/frame
  (single JPEG, 150ms, createImageBitmap + close previous — memory-stable). 30s soak: 0
  /api/stream calls, page responsive. /api/stream endpoint kept but unused by UI.
- Backend watchdog: worker reconnects after 50 bad reads OR >15s without a good frame;
  frame_age exposed in /api/state; amber 'Stream ruk gaya' overlay + amber LIVE dot when
  frames stop >6s.
- DIGITAL ZOOM: 1x–6x canvas crop; +/− buttons, mouse wheel, drag-pan when zoomed, reset;
  draw-line mode force-resets to 1x so line coords stay correct. testids: video-canvas,
  zoom-level, digital-zoom-in/out-btn, zoom-reset-btn, stale-overlay.
- OPTICAL PTZ ZOOM: new /app/9x_security/ptz.py — hand-rolled ONVIF SOAP (WS-UsernameToken
  digest, GetProfiles discovery on ports 2020/80/8000/8899/5000, cached; ContinuousMove/Stop
  zoom ±0.5). POST /api/ptz/zoom {dir,action}; creds auto-parsed from RTSP URL (@ in password
  ok); press-and-hold UI buttons; supported:false hides PTZ buttons + hint toast; cache reset
  on camera connect. NOT validated on a real ONVIF camera (container has none) — mock-tested.
- Tests: test_ptz.py 6/6; testing agent iteration_13: 11/11 pytest + full Playwright UI
  (live canvas pixels, zoom steps, PTZ hide, draw-line regression, outbox badge). No bugs.

## Implemented (update 2026-06 #21) — 12-hour time + Plate capture overhaul (verified iteration_14, 100%)
- 12-HOUR FORMAT everywhere user-visible: snapshot burned overlay (engine _save_snapshot
  '%d-%m-%Y %I:%M:%S %p'), WhatsApp caption + test message (whatsapp.py), events table
  Date&Time cell + snapshot preview modal (EventsTable.jsx fmt12 helper). DB storage stays
  24h ISO (queries/retention unchanged).
- PLATE CAPTURE overhaul (user: 'number plate capture nahi ho raha'):
  - plate_reader.py rewritten: allowlist A-Z0-9, variants (full crop + lower half, upscale
    to >=320px h, CLAHE), Indian plate regex scoring (MH12AB1234 / 22BH1234AB +1.0 bonus),
    warmup() preload, last_error surfaced, bundled-models support (_bundled_model_dir:
    easyocr_models next to frozen exe, download_enabled=False).
  - engine.py: background OCR warmup thread at engine start (logs to camera_log.txt);
    per-track BEST (largest) full-res crop kept while tracking (capped 1000px wide for
    memory); at crossing, event saved INSTANTLY with plate='' and OCR runs in async thread
    (crossing crop -> best crop fallback), backfills plate via db.update_event_plate and
    THEN sends WhatsApp (alert includes plate). Frame loop never blocks (measured 7ms).
  - workflow build-windows.yml: EasyOCR models downloaded+cached in CI and bundled via
    PyInstaller --add-data easyocr_models -> OCR works fully OFFLINE on user machines
    (previously models downloaded at first use = silent failure offline; likely root cause).
- Tests: test_plate_fmt.py 5/5, test_async_ocr.py 2/2 (real+fake OCR), testing agent
  iteration_14: 10/10 pytest + Playwright 12h/plate UI. Real night-camera accuracy is
  user-device territory: plate needs to be visibly readable in the frame.

## Backlog / Next

- P1: Vehicle re-identification to avoid double counting if it lingers on line
- P1: CSV/Excel export of event log
- P2: Multi-camera support (separate entry/exit cams)
- P2: Plate-based search + email/Telegram alert on entry
- P2: Auto-delete snapshots older than N days
