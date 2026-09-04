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

## Implemented (update 2026-06 #22) — 'Detection line nahi kaam kar raha' fix (self-tested, verified via API pixel-check + screenshot)
- ROOT CAUSE (user video at 07:21 AM): when capture schedule window (default 18:00-06:00)
  is OFF-hours, or AI engine is None, worker served RAW frames — detection line was never
  drawn and detection silently off with no visual cue.
- FIX: Worker._draw_line_only() always burns the yellow line onto frames even when paused /
  engine failed; /api/state exposes capture_paused; amber chip on video
  (data-testid detection-paused-chip): 'Detection PAUSED — schedule time ke bahar
  (Settings > Timing me badlein)'. Watchdog reconnect no longer overwrites PAUSED status.
- Verified: paused frame has 3300+ yellow line pixels via /api/frame; chip + canvas line
  confirmed in Playwright screenshot; line set via /api/line reflects immediately.

## Implemented (update 2026-06 #23) — Inbuilt (permanent) GitHub update token (self-tested: 3/3 pytest + UI screenshot)
- User: token har install me daalna padta tha → ab CI build me bake hota hai.
  • updater.py: DEFAULT_TOKEN = "" (sed anchor) + effective_token(cfg_token) → Settings token
    (override) > inbuilt token > None. service update/check + update/apply use it.
  • Workflow new step "Bake inbuilt GitHub update token": reads repo secret UPDATE_TOKEN;
    missing → ::warning (build continues, old behavior); present → verifies against
    api.github.com/repos/$GITHUB_REPOSITORY (non-200 → ::error, build fails with Hindi hint);
    OK → sed-bakes into updater.py before PyInstaller. Secrets are auto-masked in logs.
  • /api/settings returns gh_token_builtin; Settings > Updates shows green chip
    (data-testid gh-token-builtin-chip) "token inbuilt hai" or the how-to hint
    (gh-token-missing-hint) naming the UPDATE_TOKEN secret; token field becomes optional override.
  • "No release" message now explains UPDATE_TOKEN secret path.
- USER ONE-TIME SETUP: GitHub repo → Settings → Secrets and variables → Actions → New repository
  secret → Name UPDATE_TOKEN, Value = Fine-grained PAT (Repository access: only this repo;
  Permissions → Contents: Read-only; long expiry). Then Save to GitHub → new build → install once.
- Security note (told user): baked token is extractable from the installer; keep it read-only,
  single-repo scoped. Alternative chosen too (user "Dono"): making repo public needs no token.
- Tests: test_builtin_token.py 3/3 (precedence, sed-bake simulation, service wiring + flag).
- Quick detection ON/OFF dashboard toggle: user said "ye rehne do" — NOT built (dropped).

## Implemented (update 2026-06 #24) — Sidebar "New version" badge (self-tested: 2/2 pytest + 24 regression + UI E2E screenshot)
- service.py: _update_check_loop (daemon) runs updater.check_latest 20s after start
  (UPDATE_CHECK_DELAY env) and every 6h (UPDATE_CHECK_SECONDS env), dev mode (no repo)
  skipped; errors logged only. Cached in _update_info; manual /api/update/check also refreshes
  cache. /api/state now has update_available + update_latest (UI polls every 2.5s).
- updater.is_newer: current defaults to APP_VERSION at CALL time (was bound at def time).
- UI: Sidebar emerald pill (data-testid update-available-badge) "New version v1.0.N" + green dot
  on Settings nav (nav-settings-update-dot); click → Settings > Updates tab (App holds
  settingsTab; SettingsPage tab is now a controlled prop) and Updates tab auto-runs the check
  on open so "Download & Install Now" shows immediately.
- Tests: test_update_badge.py 2/2; Playwright: badge+dot appear, click lands on Updates with
  update-info + apply button. Real GitHub check = user's installed build.

## Implemented (update 2026-06 #25) — "Photo nahi gaya / capture nahi hua / dashboard khaali" fix (self-tested: 7 new + 43 regression pytest, real-YOLO E2E video, screenshots)
- USER REPORT: real WhatsApp alert text arrived (Entry - CAR) but no photo; dashboard 0 rows.
- ROOT CAUSE #1 (data loss): user data (config.json, events.db, snapshots, logs) lived in
  resources\engine\ INSIDE the install dir; electron-builder NSIS runs `RMDir /r $INSTDIR`
  on EVERY update → all events/snapshots/settings wiped each update (also why the user had to
  re-enter token/API key each install). FIX: config.py frozen BASE_DIR = %LOCALAPPDATA%\9xSecurity
  (env NX_DATA_DIR override); config.migrate_legacy_data() copies old files once (never
  overwrites). NOTE: the update TO this build still wipes (old uninstaller) → user re-enters
  settings ONE last time.
- ROOT CAUSE #2 (silent snapshot failure): cv2.imwrite fails silently on Unicode paths /
  unwritable dirs → image_path pointed to nothing → WhatsApp text-only. FIX: imencode + Python
  write, try/except → clog "event: ... -> path" or "SNAPSHOT SAVE FAILED", event still logged
  with image_path='' (UI shows 'photo save nahi hui' placeholder, testid snapshot-missing).
  whatsapp._deliver logs 'image-missing->text' / 'image-rejected->text' in wa_log.txt.
- DETECTION RELIABILITY (tracker.py): crossing judged on BOTTOM-CENTER (wheels) instead of
  centroid; size-adaptive match distance max(90, 0.6*bbox); NEW occluded-gate fallback
  near_band=10% frame height: vehicle first seen within band of the line that then moves
  ≥2.5×band away on the same side counts as crossing (via='appeared-at-line'). Known
  trade-off: a car parked right at the line that later drives inward may count as Entry.
- LIVE HUD (engine._annotate): 'AI: N vehicles tracked' bottom-left, red dot at ref point,
  counted tracks grey '(counted)'. User can now SEE whether AI detects the vehicle.
- DIAGNOSTICS: GET /api/diagnostics (version, data_dir, disk free, snapshot write test,
  events total/today, last_event + image_exists, outbox, engine flags/tracks/detections,
  WhatsApp config flags, capture schedule, tail of camera_log.txt & wa_log.txt). Settings >
  Diagnostics tab (DiagnosticsTab.jsx; testids diagnostics-tab, diag-flags, diag-copy-btn,
  diag-camera-log, diag-wa-log) with 'Copy sab kuch' → user pastes into chat.
- db.stats() added. Tests: test_capture_fixes.py 7/7; full suite green except pre-existing
  stale test_service_api::test_settings_roundtrip (expects unmasked secrets; masked since #18).
- E2E: synthetic gate video (real bus asset sliding across line) via /api/camera/connect →
  real YOLO → 'event: Exit bus (cross)' in camera_log, snapshot file exists, row in table,
  HUD visible in screenshot. Test data cleaned.
- LEARNING: never `git stash` in this repo (running service rewrites log files → pop fails).

## Implemented (update 2026-06 #26) — Update download progress (self-tested: 4/4 pytest + Playwright with fake 800MB download)
- USER: 'kitna download ho gaya dikha nahi raha' — apply was a blocking request with no feedback.
- Backend: POST /api/update/apply now starts a background thread and returns immediately;
  job state in _update_job {state: idle|checking|downloading|installing|done|cancelled|error,
  percent, read, total, message, latest, ok}. GET /api/update/progress; POST /api/update/cancel
  (threading.Event → updater.download(should_stop=) aborts + temp file removed). Re-clicking
  apply during a run returns the running job (no duplicate). /api/state exposes update_job
  {state, percent}. updater.download now reports progress even without Content-Length.
- UI: UpdateProgress.jsx (testids update-progress, update-progress-bar, update-progress-stats,
  update-progress-message, update-cancel-btn): bar + % + MB/total + MB/s + ETA + Cancel;
  error/cancel → 'Dobara Download & Install' button; done+ok → toast then app quit (installer
  relaunches). Progress resumes when returning to the Updates tab. Sidebar badge shows
  'Downloading… N%' with fill (update-badge-progress) / 'Install ho raha hai…'.
- Tests: test_update_progress.py 4/4 (progress→done, no-duplicate + cancel, error, updater
  stop/no-total). Playwright: 15%→ stats '126 / 800 MB · 24 MB/s · ~28 sec', badge 12%,
  navigate away/back resumes, cancel → retry button.

## Implemented (update 2026-06 #27) — "v1.0.8 me detection chalta tha, latest me nahi" (self-tested: 6 new + 41 regression pytest + 4 script suites, Playwright)
- USER: v1.0.8 build captures + WhatsApp OK; v1.0.14/15: no boxes, no snapshots, no WhatsApp;
  every update wipes settings + asks password again (data-wipe already fixed in #25).
- ANALYSIS: engine code path unchanged between builds → most probable cause = CI dependency
  drift: torch/torchvision/pyinstaller were UNPINNED, so later builds pulled newer versions that
  can break inference inside the frozen exe (classic: torchvision::nms op missing) → model LOADS
  fine but every process_frame raises → pre-#22 raw frame ("line nahi dikh raha" report), post-#22
  line-only with no boxes (exactly this report). Status "AI OFF" hint was also overwritten by the
  watchdog reconnect, so nothing visible told the user.
- FIXES:
  • requirements.txt pins torch==2.5.1 + torchvision==0.20.1 (validated in container: YOLO bus
    detect 171 ms, all suites green); workflow pins pyinstaller==6.11.1, adds
    --collect-binaries torchvision, prints lib versions, runs a YOLO smoke test pre-build AND a
    **frozen-exe self-test** (NX_SELFTEST=1 → service.selftest(): real YOLO inference + EasyOCR
    model load inside dist/9xEngine/9xEngine.exe) — build FAILS if packaged detection is broken,
    warning if EasyOCR models missing. A broken build can no longer be released.
  • service.Worker: _ai_selftest() right after model load (one real inference); failure →
    engine disabled + ai_error; per-frame errors → ai_error + rate-limited traceback logging
    (first + 1/min; previously one traceback PER FRAME could bloat camera_log.txt); _live_status()
    keeps "(AI ERROR — Settings > Diagnostics dekhein)" sticky across watchdog reconnects;
    ai_ms (EMA), ai_frames, ai_errors tracked; red banner burned into the video when AI is down.
  • /api/state: ai_loaded, ai_error, ai_ms. CameraPanel red chip data-testid ai-error-chip with the
    error text. Diagnostics: AI flag shows error/ms-per-frame, frames/errors, library versions,
    engine_out.log + app_log.txt tails (diag-engine-log); Copy includes all.
  • engine: OCR warmup thread at startup REMOVED (lazy on first crossing, async) — no torch
    contention at connect; HUD shows detect ms ("AI: 1 vehicle tracked | 57 ms").
  • electron-main.js: engine stdout/stderr → %LOCALAPPDATA%\9xSecurity\engine_out.log via fd
    (unread pipes could block engine threads once a library printed >64KB); 5MB truncate.
- Tests: test_ai_health.py 6/6 (self-test fail → AI disabled + red banner; ok path; rate-limit
  1 traceback for 50 errors; state/diag fields; selftest() rc 0 real model / rc 1 broken).
  Playwright: broken-detector run shows red chip + banner + Diagnostics flag with exact error.
- USER NEXT: Save to GitHub → install new build ONCE (last re-entry of settings) → connect →
  HUD "AI: … ms" must appear; if red banner: Settings > Diagnostics > Copy sab kuch → paste.

## Implemented (update 2026-06 #28) — Capture Toast (self-tested: 2 pytest + real-YOLO E2E screenshot)
- Worker._on_event hooked to engine.on_event → worker.last_event {id, direction, vehicle_type,
  plate, image_path, timestamp}; exposed in /api/state.last_event (no extra DB query).
- App.jsx tracks last seen event id (skips the stale event present at login) and shows
  CaptureToast.jsx top-right for 8s: snapshot thumbnail, "ENTRY/EXIT — CAR captured", 12h time,
  plate if known, close button; emerald border for Entry, orange for Exit; slideIn keyframe.
  testids: capture-toast, capture-toast-title, capture-toast-meta, capture-toast-img,
  capture-toast-close. Works on every page (Dashboard + Settings).
- Verified: bus video crossing → toast "EXIT — BUS CAPTURED · 11:40:45 AM" with thumbnail within
  the 2.5s state poll; close works; no toast on fresh login.

## Implemented (update 2026-06 #29) — WhatsApp API key always visible + actionable send errors (testing agent iteration_15: ALL PASS)
- USER: masked key field → couldn't verify saved key → WhatsApp "failed".
- GET /api/settings returns wa_api_key in plain text (+ wa_api_key_set); gh_token &
  wa_account_password remain write-only (_VISIBLE_SECRETS). POST strips whitespace/newlines from
  secrets; empty wa_api_key now really clears (visible), empty write-only secrets keep old value.
- UI: text input visible by default + eye toggle (wa-api-key-toggle), status line
  (wa-api-key-status) '✔ Key saved hai (N characters)' / 'Koi key saved nahi…'.
- whatsapp._explain: 401 → 'API key galat/expire — Dashboard > API Key se copy', 403 → QR/session,
  404/429/5xx hints + provider snippet; transport → 'Internet/connection error: …';
  _is_network_error ignores 'HTTP …' strings (provider answered ≠ offline).
- Verified live against wa.9x.design: bad key → 'HTTP 401: API key galat/expire hai … [{"detail":"Invalid API token"}]'.
- Tests: test_wa_key.py 2/2; iteration_15 backend 7/7 + full UI flow green.

## Implemented (update 2026-06 #30) — Brand footer (screenshot-verified)
- BrandFooter.jsx (testids brand-footer / brand-copyright / brand-designer / brand-phone):
  "© <year> 9x Security. All rights reserved." · "Designed by : 9x.design" (link) ·
  "Mobile No: 7587922222" (tel link). Shown at sidebar bottom (all pages) and login left panel
  (small screens: centered under the form). BRAND constant holds designer/url/phone.

## Implemented (update 2026-06 #31) — WhatsApp GROUP alerts (self-tested: 6 pytest w/ fake provider + outbox script + Playwright)
- Provider endpoints (wa.9x.design docs): POST /api/v2/sendGroup (groupId, text), POST
  /api/v2/sendGroupFile (groupId, file, caption, filename), GET /api/v2/groupChat/getGroupList.
- whatsapp.py: _group_id() normalizes dict/string → '<digits>@g.us' (≥10 digits); recipients =
  numbers + groups; _send_text/_send_image route by _is_group(to) to group endpoints; outbox
  stores the '@g.us' recipient string so retries work unchanged; list_groups(); test message
  labels groups 'Group <id>: SENT'.
- config DEFAULTS wa_groups: [{id,name}]; _SETTINGS_KEYS + /api/whatsapp/test accept wa_groups;
  NEW POST /api/whatsapp/groups {wa_api_key?, wa_base_url?} → {ok, groups[{id,name,size}]} or
  {ok:false, detail}. Diagnostics whatsapp.groups count.
- UI WaGroupsPicker.jsx in Settings > WhatsApp: 'Groups load karein' (wa-groups-load-btn) →
  checkbox list (wa-group-option-<id>) → selected chips (wa-groups-selected, remove buttons) →
  manual Group ID add (wa-group-manual-input / wa-group-manual-add-btn); summary line
  wa-targets-summary 'Alerts jayenge: N number + M group'. Alerts go to numbers AND groups;
  group-only = leave numbers empty (explained in UI).
- Verified: load 2 groups → select → Save → reload persists → Send Test → 'Group …: SENT ✅'.

## Implemented (update 2026-06 #32) — Re-crossing fix + stronger detection (testing agent iteration_16: ALL PASS)
- USER: exit captured, re-entry 10-15s later not captured; later "minimized → exit not captured";
  trucks labeled car; "AI strong banao".
- ROOT CAUSE: Track.counted = True forever → a vehicle that stays in view (parked in yard /
  waiting outside) keeps its track and every later crossing was ignored. Minimize was a
  coincidence (engine runs independent of the window; tray mode was already background-safe).
- tracker.py rewrite: crossings per track unlimited; after a crossing the track is DISARMED until
  its bottom-center is >= hysteresis (max(20, 0.5*near_band)) px past the line, and next crossing
  needs >= min_gap_s (3s); update(..., now=) for testability; crossing dict has nth + via.
  Label VOTING per track (Counter): truck/bus win if >= 30% of frames (YOLO mislabels trucks as
  car in some frames). appeared-at-line threshold lowered 2.5→1.5×band.
- detector.py: agnostic_nms=True (car+truck double box → 1), imgsz param, model tiers
  resolve_model_path('auto'|'fast'|'accurate') → yolov8s.pt (accurate) bundled in CI
  (downloaded from ultralytics assets v8.3.0, size-checked) + local copy (gitignored);
  auto = yolov8s, falls back to yolov8n if self-test > AUTO_MODEL_MAX_MS (350 ms env).
  DEFAULT confidence 0.35 (was 0.40), detector_model 'auto'.
- service: /api/options accepts detector_model + confidence (clamped 0.15..0.9; applied live to
  detector.conf); /api/state + diagnostics expose detector_model, confidence, ai_model, ai_tier;
  self-test does warm-up + timed inference; selftest() (CI frozen check) tests BOTH models.
  HUD: 'AI: N vehicles tracked | 140 ms | yolov8s.pt'.
- UI Detection Controls: AI Model select (detector-model-select: auto/accurate/fast, info toast
  "reconnect par lagu"), active model line (ai-model-active), Sensitivity slider
  (confidence-slider, confidence-value %).
- Electron: powerSaveBlocker 'prevent-app-suspension' so the PC does not sleep while hidden in tray.
- Tests: test_detection_strength.py 7/7 (recross twice, jitter, quick-bounce gap, truck voting,
  model paths, yolov8s detects bus w/o duplicate boxes, options API); full suite 58 pytest +
  scripts green. iteration_16: real-YOLO E2E bus exit→wait→re-entry = 2 events same track id,
  yolov8s 134 ms, options API, UI select verified.
- NOTE test_engine.py globally patches whatsapp.requests.post; test_wa_groups fixture restores it.

## Implemented (update 2026-06 #33) — Blurry-every-2s video / HEVC decode errors (testing agent iteration_17: ALL PASS)
- USER: pasted '[hevc] Could not find ref with POC …' + 'video har 1-2 sec me dhundla'. Also shared
  wa_log showing group-text/group-image status 200 → WhatsApp GROUP alerts confirmed working live.
- ROOT CAUSE: synchronous cap.read() in the worker; while YOLO ran (150-300 ms) the decoder stalled →
  camera/ffmpeg dropped packets → missing HEVC refs → smeared frames + growing lag.
- FIX: engine.LatestFrameReader (bg thread decodes continuously, AI gets NEWEST frame only,
  .dropped/.decoded counters, read(timeout), get(), release joins thread). Worker._open(source,
  live) wraps only RTSP sources (files keep every frame → E2E tests deterministic). FFMPEG_OPTS +
  'fflags;discardcorrupt'; FFmpegPipeSource '-fflags +discardcorrupt'. codec_name(cap) via
  CAP_PROP_FOURCC → worker.codec; frames_dropped tracked; both in /api/state + diagnostics;
  Diagnostics rows 'Camera codec' / 'Frames skipped (AI busy)' + amber HEVC hint (diag-hevc-hint:
  use H.264 or sub-stream /Streaming/Channels/102, subtype=1). camera_log 'source opened codec=…'.
- Tests: test_live_reader.py 5/5; test_live_e2e.py (full Worker loop, paced 12-fps fake 'hevc'
  cam through the reader, real YOLO: exit → wait → re-entry = 2 events, ai_error ''). iteration_17
  green (pytest 13/13, file-source regression, Diagnostics UI). Real RTSP validation = user.

## Implemented (update 2026-06 #34) — Sub-stream one-click switch + Worker restart race fix (testing agent iteration_18: ALL PASS)
- engine.substream_url(url): Hikvision 101→102 / legacy main→sub av_stream, Dahua/CP Plus
  realmonitor subtype=1 (add if missing), Reolink _main→_sub, Uniview video1→video2, /stream1→
  /stream2, generic main→sub; '' when unknown or already sub. HEVC_CODECS constant.
- /api/state: substream_url (only when connected AND codec is HEVC) + rtsp_url_main.
  POST /api/camera/substream (saves rtsp_url_main, switches, worker.start(), ptz cache reset) /
  POST /api/camera/mainstream (revert). camera_log 'switched to sub-stream' / 'switched back'.
- UI CameraPanel: amber chip substream-chip + substream-switch-btn (video ke upar, bottom-left),
  mainstream-revert-btn (bottom-right) while on sub-stream; URL input follows; toasts mask creds.
- RACE FIX (pre-existing, found here): Worker.start() re-set _running=True before the old loop
  noticed stop() → two capture loops (double events, camera session leak). Now _gen counter:
  loops run while _alive(gen); start() joins the previous thread ≤3s; stale loops don't write
  frames/status. test_worker_restart.py covers it.
- test_live_e2e now uses detector_model 'fast' (CPU-contention flakiness seen when two YOLO
  services ran at once).
- Tests: test_substream.py 17/17, test_worker_restart 1/1; iteration_18 green (backend fake-HEVC
  flow: connect→suggest→switch→400 on repeat→revert; UI chip/toast/URL/revert).

## Implemented (update 2026-06 #35) — WhatsApp "Number:" line + strict/strong plate OCR + UI hang fix (testing agent iteration_19: ALL PASS)
- USER: caption me 'Number: <plate>' ya 'Number: Not detected' chahiye, galat number kabhi nahi, OCR strong
  (truck plates). Plus: 'minimize se open kiya to 2 min hang, Settings sirf Loading…, restart se theek'.
- ROOT CAUSE #1 (OCR garbage/wrong plates): EasyOCR default `quantize=True` (int8 dynamic-quantized
  recognizer LSTM) returns garbage ('LELELD', conf 0.00) on CPU depending on torch thread count /
  input width (torch 2.5 bug). Same image: 8 threads OK, 7 or 1 threads garbage. FIX: Reader(quantize=False)
  → exact + stable + same speed. This very likely explains "number plate detect nahi ho raha" on Windows.
- ROOT CAUSE #2 (hang): one candidates() call took 19-28 s (4 variants × mag_ratio 1.5 on 1000px crops),
  budget only checked between crops, and EVERY crossing spawned its own OCR thread → several torch jobs
  saturating all cores for minutes → HTTP/UI starved. UI pollers (setInterval ×4, no timeouts) piled
  up requests (Chromium 6 sockets/host) → Settings 'Loading…' forever until restart.
- plate_reader.py rewrite: _prep (gray+CLAHE, ≤640px, upscale ≤4x), candidates(): plate zone (lower 60%)
  → zoom re-read of ≤2 biggest text boxes (padded; recognizer needs whitespace) → full-crop fallback only
  when no clean read; hard deadline checked before EVERY OCR call; last_trace (raw reads) logged to
  camera_log on Not detected. repair_plate: district '0' rejected, layout cost (1-digit district +0.5,
  3-letter series +1.0) so MHIZAB1254 → MH12AB1254 not MH1ZAB1254. read_many acceptance: votes≥2 in
  different crops, OR 0 fixes & conf≥0.45, OR ≤2 slot-constrained fixes & conf≥0.60; two equally scored
  different candidates → ambiguous → ''. Typical read now 1-3 s/crop (was 19-28 s).
- engine.py: ONE OCR worker thread + queue (_queue_ocr/_ocr_worker); job waiting >20 s
  (OCR_MAX_QUEUE_WAIT_S) is skipped → WhatsApp still sent as 'Not detected'. detector.py: torch threads =
  cpu_count-1 (NX_TORCH_THREADS) so decoder/UI/HTTP keep a core.
- whatsapp._caption: 4th line 'Number: <plate>' / 'Number: Not detected'.
- UI: api() has AbortController timeout (default 15 s; 60-180 s for camera test / WA test / groups /
  update check); poll() helper = sequential polling (App state, StatCards, EventsTable); CameraPanel frame
  fetch 4 s abort + 1 fps when window hidden; SettingsPage shows settings-load-error + settings-retry-btn
  instead of infinite Loading.
- Tests: test_plate_ocr.py 12 (TTF-rendered plates via tests/synth_plate.py: one/two-row, small/big/blur,
  truck text → '', cut-off plate → '', acceptance rules, single OCR worker); tests/test_iter14 fixed
  (old fixture painted text wider than the image). iteration_19: backend 100% (22 + 116 pytest + scripts),
  frontend 100% (sequential /api/state, hang-fix retry flow, all tabs, regression).
- LEARNING: never test OCR with OpenCV Hershey fonts (3→5, 2→Z, 1→I misreads are the font's fault).
- USER NEXT: Save to GitHub → install build → Number Plate (OCR) ON → real truck/car plates check;
  agar 'Not detected' zyada aaye to Settings > Diagnostics camera_log me 'plate OCR raw reads' line paste karein.

## Implemented (update 2026-06 #36) — Live plate on toast/table + manual plate correction (testing agent iteration_20: ALL PASS)
- DB: events.plate_status ('' | 'pending' | 'done') + plate_source ('' | 'ocr' | 'manual'), idempotent
  ALTER TABLE migration in EventDB._init; add_event(plate_status=); update_event_plate(eid, plate, source,
  status) returns the row (None if missing).
- Engine: crossing event saved with plate_status 'pending' when OCR on; after OCR the DB row is updated
  (source 'ocr'/'', status 'done') and on_event fires AGAIN with the same id → Worker.last_event updated →
  /api/state → App.jsx updates the visible capture toast in place (timer restarts so the number stays 8 s).
- API: POST /api/events/{id}/plate {plate} — normalizes to A-Z0-9 (4-12 chars, else 400), 404 unknown id,
  '' clears; sets plate_source 'manual'; updates worker.last_event if it is that event; camera_log
  'plate manual: event N -> …'. Manual plates are searchable via the existing plate LIKE search.
- UI: PlateBadge.jsx (number + amber '✎ manual' tag / italic 'reading…' / grey 'Not detected'); used in
  table Plate column (event-plate-<id>, event-plate-<id>-manual), CaptureToast 'Number:' line
  (capture-toast-plate) and snapshot modal header (snapshot-modal-plate). PlateEditor.jsx inside the modal
  (plate-editor, plate-editor-current, plate-editor-input, plate-editor-save-btn; Enter saves; disabled
  until changed/valid) → toast 'Number X save ho gaya ✔ (search me milega)', row + header update instantly.
  Modal shows snapshot-modal-missing placeholder when the photo is missing.
- Tests: test_plate_manual.py 3/3 (migration, pending→done events, API); iteration_20 backend+frontend 100%
  incl. real-engine E2E toast 'reading…' → 'Not detected' on the same toast.
- LEARNING: never `git checkout -- events.db` while service.py runs (stale inode → API reads a phantom DB).

## Backlog / Next

- P1: Vehicle re-identification to avoid double counting if it lingers on line
- P1: CSV/Excel export of event log
- P2: Multi-camera support (separate entry/exit cams)
- P2: Plate-based search + email/Telegram alert on entry
- P2: Auto-delete snapshots older than N days
