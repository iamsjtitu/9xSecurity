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
