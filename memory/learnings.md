# 9x Security — agent learnings / gotchas

- Service on 127.0.0.1:8971 is started manually (`nohup python service.py`), NOT supervisor.
  `pkill -f service.py` often fails to kill; use `pkill -9 -f service` and check `ss -ltnp | grep 8971`.
- NEVER `git stash` here: running service rewrites camera_log.txt / wa_log.txt / config.json /
  ffmpeg_err.txt → `stash pop` refuses; you must `git checkout --` those files then pop.
- Default password 9xsecurity forces a change on first UI login (Login.jsx force-change form).
  For screenshots: change to Temp123456, then restore via POST /api/settings {"new_password":"9xsecurity"}.
- events.db and config.json are git-tracked; clean test events with sqlite DELETE, `git checkout -- config.json`.
- E2E vehicle test: build an mp4 from ultralytics/assets/bus.jpg sliding across the line and
  POST /api/camera/connect {"url": "/path/file.mp4"} — real YOLO detects it (label bus).
- Container date is Sept 2026 (matches user's PC), not June.
- User data on Windows now lives in %LOCALAPPDATA%\9xSecurity (config.BASE_DIR); install dir is
  wiped by NSIS on every update.
- test_service_api.py::test_settings_roundtrip is stale (secrets masked since security audit) — pre-existing failure.
- test_outbox.py / test_async_ocr.py / test_plate_fmt.py / test_ptz.py are SCRIPT-style (no test_ functions):
  run with `python test_x.py`, not pytest (pytest collects 0).
- Editing 9x_security/requirements.txt triggers the platform to pip-install it into the container
  (torch is now pinned 2.5.1 here too). Disk is tight (~3 GB free) — avoid adding big packages.
- CI now runs the frozen exe with NX_SELFTEST=1 (service.selftest) — keep that entrypoint working.
- EasyOCR: ALWAYS Reader(quantize=False). Default int8 quantized LSTM gives garbage ('LELELD', conf 0.00)
  on CPU depending on torch thread count / input width. Never test OCR with cv2 Hershey fonts — use
  tests/synth_plate.py (TTF). OCR must have a hard deadline before every readtext (one call = 1-3 s at 640px).
- Only ONE OCR job at a time (engine._ocr_worker queue); parallel torch jobs starve UI/HTTP for minutes.
- UI pollers must be sequential (api.js poll()) and every api() call has a timeout; setInterval + slow
  engine = request pile-up (6 sockets/host) = Settings 'Loading…' forever.
- Full pytest in one session: run test_service_api.py separately (test_engine.py leaks a requests.post patch).
- NEVER `git checkout -- events.db` while service.py is running: the service keeps the old inode open and
  reads/writes a phantom DB (API shows [] while the file has rows). Clean rows with sqlite DELETE instead;
  if you did checkout, restart the service.
