"""9x Security - local engine service (FastAPI) powering the Electron UI.

Runs on 127.0.0.1 only. The Electron renderer talks to it over HTTP and
receives live video as an MJPEG stream.
"""
import os
import re
import secrets
import shutil
import threading
import time
from datetime import datetime, timedelta

import cv2
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import auth
import config
import updater
from database import EventDB
from engine import SecurityEngine, clog, normalize_rtsp_url, open_stream, probe_rtsp
from whatsapp import WhatsAppNotifier

PORT = int(os.environ.get("ENGINE_PORT", "8971"))
app = FastAPI(title="9x Security Engine")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["null", "file://", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

TOKEN_TTL = 12 * 3600
_tokens = {}  # token -> created_ts
_db = EventDB()


def _cfg():
    return config.load_config()


def _check(request: Request):
    t = request.headers.get("x-auth-token") or request.query_params.get("t")
    ts = _tokens.get(t) if t else None
    if ts is None or time.time() - ts > TOKEN_TTL:
        if t:
            _tokens.pop(t, None)
        raise HTTPException(401, "unauthorized")


class Worker:
    def __init__(self):
        self._running = False
        self.thread = None
        self.status = "Idle — camera URL daal kar Connect dabayein"
        self.engine = None
        self._jpeg = None
        self._lock = threading.Lock()
        self.last_frame_ts = 0.0
        self.capture_paused = False
        self.ai_error = ""        # last AI failure (self-test / per-frame), '' = healthy
        self.ai_ms = None         # smoothed process_frame time (ms)
        self.ai_frames = 0        # frames processed by AI since connect
        self.ai_errors = 0
        self._last_err_log = 0.0
        self.last_event = None    # latest captured event (for the UI capture toast)

    def _on_event(self, ev):
        self.last_event = {
            "id": ev.get("id"),
            "direction": ev.get("direction"),
            "vehicle_type": ev.get("vehicle_type"),
            "plate": ev.get("plate", ""),
            "image_path": ev.get("image_path", ""),
            "timestamp": ev.get("timestamp"),
        }

    @property
    def connected(self):
        return bool(self.thread and self.thread.is_alive() and self._running)

    def start(self):
        self.stop()
        self._running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self._running = False
        with self._lock:
            self._jpeg = None

    def latest(self):
        with self._lock:
            return self._jpeg

    def apply_cfg(self, cfg):
        if self.engine is not None:
            self.engine.cfg = cfg
            try:
                self.engine.notifier.update(cfg)
                self.engine.detector.set_allowed(cfg.get("vehicle_classes") or ["car", "truck", "bus"])
            except Exception:
                pass

    def _run(self):
        try:
            self._run_impl()
        except Exception:
            import traceback

            clog("Worker CRASH:\n" + traceback.format_exc())
            self.status = "ERROR: engine crash — camera_log.txt dekhein"
            self._running = False

    def _live_status(self):
        s = "Connected — live monitoring chalu hai"
        if self.ai_error:
            s += " (AI ERROR — Settings > Diagnostics dekhein)"
        elif self.engine is None:
            s += " (AI OFF — model load fail, Settings > Diagnostics dekhein)"
        return s

    def _note_ai_error(self, where, exc_text):
        import traceback

        self.ai_errors += 1
        self.ai_error = f"{where}: {exc_text.strip().splitlines()[-1][:200]}"
        now = time.time()
        if now - self._last_err_log > 60:  # first error + at most once a minute (log size!)
            self._last_err_log = now
            clog(f"svc {where} error (#{self.ai_errors}):\n" + traceback.format_exc())

    def _draw_line_only(self, frame, cfg):
        try:
            ln = cfg.get("line") or {}
            h, w = frame.shape[:2]
            a = (int(float(ln.get("x1", 0.5)) * w), int(float(ln.get("y1", 0.0)) * h))
            b = (int(float(ln.get("x2", 0.5)) * w), int(float(ln.get("y2", 1.0)) * h))
            cv2.line(frame, a, b, (0, 255, 255), 2)
            if self.ai_error or self.engine is None:
                msg = "AI ERROR - detection band hai. Settings > Diagnostics dekhein"
                cv2.rectangle(frame, (0, h - 30), (w, h), (0, 0, 160), -1)
                cv2.putText(frame, msg, (10, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                            (255, 255, 255), 1)
        except Exception:
            pass
        return frame

    def _ai_selftest(self):
        """Run one real inference right after model load so a broken torch/
        torchvision build shows up immediately (not silently per frame)."""
        import numpy as np

        t0 = time.time()
        blank = np.zeros((config.DISPLAY_HEIGHT, config.DISPLAY_WIDTH, 3), dtype=np.uint8)
        dets = self.engine.detector.detect(blank)
        ms = (time.time() - t0) * 1000
        clog(f"svc: AI self-test OK ({ms:.0f} ms, {len(dets)} dets on blank frame)")
        return ms

    def _run_impl(self):
        cfg = _cfg()
        self.ai_error, self.ai_ms, self.ai_frames, self.ai_errors = "", None, 0, 0
        self.status = "AI model load ho raha hai..."
        clog("svc: loading AI model")
        try:
            self.engine = SecurityEngine(cfg=cfg, db=_db)
            self.engine.on_event = self._on_event
            clog("svc: AI model loaded")
        except Exception:
            import traceback

            clog("svc: MODEL LOAD FAILED:\n" + traceback.format_exc())
            self.engine = None
            self.ai_error = "model load: " + traceback.format_exc().strip().splitlines()[-1][:200]
        if self.engine is not None:
            try:
                self.ai_ms = self._ai_selftest()
            except Exception:
                self._note_ai_error("AI self-test", __import__("traceback").format_exc())
                clog("svc: AI self-test FAILED (detection disabled):\n"
                     + __import__("traceback").format_exc())
                self.engine = None

        url = cfg.get("rtsp_url", "").strip()
        source = normalize_rtsp_url(url) if url else 0
        self.status = "Camera se connect ho raha hai..."
        cap = open_stream(source, lambda: self._running)
        if cap is None or not cap.isOpened():
            if cap is not None:
                cap.release()
            if self._running:
                self.status = "ERROR: Camera nahi khula — 'Test' se step-by-step jaanch karein"
            self._running = False
            return
        self.status = self._live_status()
        clog("svc: streaming started")
        fail = 0
        paused = False
        last_ok = time.time()
        self.last_frame_ts = last_ok
        while self._running:
            ok, frame = cap.read()
            if not ok:
                fail += 1
                # watchdog: reconnect on 50 bad reads OR >15s without a good frame
                if fail > 50 or time.time() - last_ok > 15:
                    self.status = "Stream toota — dobara connect ho raha hai..."
                    clog("svc: stream lost, reconnecting (watchdog)")
                    cap.release()
                    cap = open_stream(source, lambda: self._running)
                    if cap is None:
                        break
                    if not paused:
                        self.status = self._live_status()
                    fail = 0
                    last_ok = time.time()
                time.sleep(0.02)
                continue
            fail = 0
            last_ok = time.time()
            self.last_frame_ts = last_ok
            small = cv2.resize(frame, (config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT))
            live_cfg = self.engine.cfg if self.engine else cfg
            capture_on = (not live_cfg.get("capture_schedule_enabled")) or config.in_time_window(
                live_cfg.get("capture_start", "18:00"), live_cfg.get("capture_end", "06:00")
            )
            if capture_on and paused:
                paused = False
                self.capture_paused = False
                self.status = self._live_status()
                clog("svc: capture resumed (schedule)")
            elif not capture_on and not paused:
                paused = True
                self.capture_paused = True
                self.status = (
                    f"Connected — capture PAUSED (schedule {live_cfg.get('capture_start')}"
                    f"-{live_cfg.get('capture_end')} ke bahar), video chalu hai"
                )
                clog("svc: capture paused (schedule)")
            if self.engine is not None and capture_on:
                try:
                    t0 = time.time()
                    annotated, _ = self.engine.process_frame(small, original=frame)
                    ms = (time.time() - t0) * 1000
                    self.ai_ms = ms if self.ai_ms is None else 0.9 * self.ai_ms + 0.1 * ms
                    self.ai_frames += 1
                    if self.ai_error:
                        self.ai_error = ""  # recovered
                        self.status = self._live_status()
                except Exception:
                    import traceback

                    self._note_ai_error("process_frame", traceback.format_exc())
                    self.status = self._live_status()
                    annotated = self._draw_line_only(small, live_cfg)
            else:
                # paused / AI off: line phir bhi dikhni chahiye (config feedback)
                annotated = self._draw_line_only(small, live_cfg)
            okj, buf = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if okj:
                with self._lock:
                    self._jpeg = buf.tobytes()
            time.sleep(0.01)
        cap.release()
        if self.status.startswith("Connected") or self.status.startswith("Stream"):
            self.status = "Disconnected."


worker = Worker()


# ---- auth ------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"ok": True, "version": updater.APP_VERSION}


DEFAULT_PASSWORD = "9xsecurity"


@app.post("/api/login")
def login(body: dict):
    cfg = _cfg()
    u = str(body.get("username", "")).strip()
    p = str(body.get("password", ""))
    if u == cfg.get("auth_user", "admin") and auth.verify_password(
        p, cfg.get("auth_salt", ""), cfg.get("auth_hash", "")
    ):
        t = secrets.token_hex(24)
        _tokens[t] = time.time()
        must_change = auth.verify_password(
            DEFAULT_PASSWORD, cfg.get("auth_salt", ""), cfg.get("auth_hash", "")
        )
        return {"token": t, "must_change_password": must_change}
    raise HTTPException(401, "Galat username ya password.")


@app.post("/api/logout")
def logout(request: Request):
    t = request.headers.get("x-auth-token") or request.query_params.get("t")
    if t:
        _tokens.pop(t, None)
    return {"ok": True}


# ---- state / camera ---------------------------------------------------------
@app.get("/api/state")
def state(request: Request):
    _check(request)
    cfg = _cfg()
    return {
        "connected": worker.connected,
        "status": worker.status,
        "version": updater.APP_VERSION,
        "rtsp_url": cfg.get("rtsp_url", ""),
        "wa_enabled": bool(cfg.get("wa_enabled")),
        "enable_plate": bool(cfg.get("enable_plate")),
        "vehicle_classes": cfg.get("vehicle_classes", ["car", "truck", "bus"]),
        "entry_direction": cfg.get("entry_direction", "pos"),
        "line": cfg.get("line"),
        "snapshot_dir": config.SNAPSHOT_DIR,
        "outbox_pending": _db.outbox_count(),
        "capture_paused": worker.capture_paused,
        "ai_loaded": worker.engine is not None,
        "ai_error": worker.ai_error,
        "ai_ms": round(worker.ai_ms) if worker.ai_ms is not None else None,
        "last_event": worker.last_event,
        "update_available": _update_info["available"],
        "update_latest": _update_info["latest"],
        "update_job": {"state": _update_job["state"], "percent": _update_job["percent"]},
        "frame_age": (
            round(time.time() - worker.last_frame_ts, 1)
            if worker.connected and worker.last_frame_ts
            else None
        ),
    }


@app.post("/api/camera/connect")
def camera_connect(body: dict, request: Request):
    _check(request)
    cfg = _cfg()
    cfg["rtsp_url"] = str(body.get("url", "")).strip()
    config.save_config(cfg)
    import ptz

    ptz.reset_cache()
    worker.start()
    return {"ok": True}


@app.post("/api/camera/disconnect")
def camera_disconnect(request: Request):
    _check(request)
    worker.stop()
    worker.status = "Disconnected."
    return {"ok": True}


@app.post("/api/camera/test")
def camera_test(body: dict, request: Request):
    _check(request)
    url = str(body.get("url", "")).strip()
    if not url:
        raise HTTPException(400, "Pehle RTSP URL daalein.")
    ok, steps = probe_rtsp(url)
    return {"ok": ok, "steps": [{"name": n, "ok": o, "detail": d} for n, o, d in steps]}


@app.get("/api/frame")
def frame(request: Request):
    """Latest JPEG frame (UI polls this instead of a long-lived MJPEG stream)."""
    _check(request)
    j = worker.latest()
    if j is None:
        raise HTTPException(404, "no frame")
    return Response(content=j, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/ptz/zoom")
def ptz_zoom(body: dict, request: Request):
    _check(request)
    import ptz

    cfg = _cfg()
    direction = "out" if str(body.get("dir")) == "out" else "in"
    action = "stop" if str(body.get("action")) == "stop" else "start"
    ok, supported, detail = ptz.zoom(cfg.get("rtsp_url", ""), direction, action)
    return {"ok": ok, "supported": supported, "detail": detail}


@app.get("/api/stream")
def stream(request: Request):
    _check(request)

    def gen():
        while True:
            j = worker.latest()
            if j is None:
                time.sleep(0.1)
                continue
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + j + b"\r\n"
            time.sleep(0.05)

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.post("/api/line")
def set_line(body: dict, request: Request):
    _check(request)
    cfg = _cfg()
    cfg["line"] = {k: float(body[k]) for k in ("x1", "y1", "x2", "y2")}
    config.save_config(cfg)
    worker.apply_cfg(cfg)
    return {"ok": True, "line": cfg["line"]}


@app.post("/api/swap")
def swap_direction(request: Request):
    _check(request)
    cfg = _cfg()
    cfg["entry_direction"] = "neg" if cfg.get("entry_direction") == "pos" else "pos"
    config.save_config(cfg)
    worker.apply_cfg(cfg)
    return {"ok": True, "entry_direction": cfg["entry_direction"]}


@app.post("/api/options")
def set_options(body: dict, request: Request):
    _check(request)
    cfg = _cfg()
    if "enable_plate" in body:
        cfg["enable_plate"] = bool(body["enable_plate"])
    if "vehicle_classes" in body:
        vc = [v for v in body["vehicle_classes"] if v in ("car", "truck", "bus")]
        cfg["vehicle_classes"] = vc or ["car", "truck", "bus"]
    config.save_config(cfg)
    worker.apply_cfg(cfg)
    return {"ok": True}


# ---- events -----------------------------------------------------------------
@app.get("/api/events")
def events(request: Request, date: str = "", direction: str = "All", all: int = 0, plate: str = ""):
    _check(request)
    rows = _db.get_events(
        date_filter=None if all else (date or None),
        direction_filter=direction,
        plate_filter=plate.strip() or None,
    )
    return {"events": rows}


@app.get("/api/counts")
def counts(request: Request):
    _check(request)
    out = _db.counts_today()
    out["outbox_pending"] = _db.outbox_count()
    return out


@app.get("/api/outbox")
def outbox(request: Request):
    _check(request)
    return {"pending": _db.outbox_count(), "items": _db.outbox_pending(limit=50)}


@app.get("/api/snapshot")
def snapshot(request: Request, path: str):
    _check(request)
    real = os.path.realpath(path)
    base = os.path.realpath(config.SNAPSHOT_DIR)
    if not (real == base or real.startswith(base + os.sep)):
        raise HTTPException(403, "forbidden")
    if not os.path.isfile(real):
        raise HTTPException(404, "not found")
    return FileResponse(real)


# ---- settings ---------------------------------------------------------------
_SETTINGS_KEYS = (
    "wa_enabled", "wa_base_url", "wa_api_key", "wa_recipients", "wa_send_image",
    "wa_account_email", "wa_account_password", "gh_token",
    "wa_schedule_enabled", "wa_start", "wa_end",
    "capture_schedule_enabled", "capture_start", "capture_end",
    "auto_delete_enabled",
)
_SECRET_KEYS = ("wa_api_key", "gh_token", "wa_account_password")
# The WhatsApp API key stays visible in Settings (user request: hidden key made it impossible
# to verify what is saved → failed sends). Other secrets remain write-only.
_VISIBLE_SECRETS = ("wa_api_key",)


@app.get("/api/settings")
def get_settings(request: Request):
    _check(request)
    cfg = _cfg()
    out = {k: cfg.get(k) for k in _SETTINGS_KEYS}
    for k in _SECRET_KEYS:
        out[f"{k}_set"] = bool(cfg.get(k))
        if k not in _VISIBLE_SECRETS:
            out[k] = ""  # never send write-only secrets back to the UI
    out["auth_user"] = cfg.get("auth_user", "admin")
    out["retention_days"] = int(cfg.get("retention_days", 7) or 7)
    out["gh_token_builtin"] = bool(updater.DEFAULT_TOKEN)
    return out


@app.post("/api/settings")
def save_settings(body: dict, request: Request):
    _check(request)
    cfg = _cfg()
    for k in _SETTINGS_KEYS:
        if k in body:
            if k in _SECRET_KEYS:
                val = str(body[k] or "").strip()  # pasted keys often carry spaces/newlines
                if not val and k not in _VISIBLE_SECRETS:
                    continue  # empty = keep existing write-only secret
                cfg[k] = val
                continue
            cfg[k] = body[k]
    if body.get("auth_user"):
        cfg["auth_user"] = str(body["auth_user"]).strip() or "admin"
    if "retention_days" in body:
        try:
            cfg["retention_days"] = max(1, min(365, int(body["retention_days"])))
        except Exception:
            pass
    if body.get("new_password"):
        salt, h = auth.hash_password(str(body["new_password"]))
        cfg["auth_salt"], cfg["auth_hash"] = salt, h
    config.save_config(cfg)
    worker.apply_cfg(cfg)
    return {"ok": True}


@app.post("/api/whatsapp/test")
def whatsapp_test(body: dict, request: Request):
    _check(request)
    cfg = _cfg()
    n = WhatsAppNotifier(
        {
            "wa_enabled": True,
            "wa_base_url": body.get("wa_base_url") or cfg.get("wa_base_url") or "https://wa.9x.design",
            "wa_api_key": (str(body.get("wa_api_key", "")).strip() or cfg.get("wa_api_key", "")),
            "wa_recipients": body.get("wa_recipients") or cfg.get("wa_recipients", []),
            "wa_send_image": bool(body.get("wa_send_image", True)),
        }
    )
    ok, detail = n.test_connection()
    return {"ok": ok, "detail": detail}


# ---- diagnostics (Settings > Diagnostics: user can copy-paste this) ----------
def _tail(path, n=80):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except Exception:
        return ""


def _snapshot_write_test():
    try:
        os.makedirs(config.SNAPSHOT_DIR, exist_ok=True)
        p = os.path.join(config.SNAPSHOT_DIR, ".write_test")
        with open(p, "wb") as f:
            f.write(b"ok")
        os.remove(p)
        return True, "OK"
    except Exception as e:
        return False, str(e)


@app.get("/api/diagnostics")
def diagnostics(request: Request):
    _check(request)
    import whatsapp
    from engine import CAMERA_LOG

    cfg = _cfg()
    st = _db.stats()
    last = st["last"]
    if last:
        last["image_exists"] = bool(last.get("image_path")) and os.path.isfile(last["image_path"])
    try:
        du = shutil.disk_usage(config.BASE_DIR)
        free_gb = round(du.free / 1e9, 1)
    except Exception:
        free_gb = None
    w_ok, w_detail = _snapshot_write_test()
    eng = worker.engine
    return {
        "version": updater.APP_VERSION,
        "time_now": datetime.now().isoformat(timespec="seconds"),
        "data_dir": config.BASE_DIR,
        "snapshot_dir": config.SNAPSHOT_DIR,
        "db_path": config.DB_PATH,
        "disk_free_gb": free_gb,
        "snapshot_write_ok": w_ok,
        "snapshot_write_detail": w_detail,
        "events_total": st["total"],
        "events_today": _db.counts_today(),
        "last_event": last,
        "outbox_pending": _db.outbox_count(),
        "engine": {
            "connected": worker.connected,
            "status": worker.status,
            "ai_loaded": eng is not None,
            "ai_error": worker.ai_error,
            "ai_ms": round(worker.ai_ms) if worker.ai_ms is not None else None,
            "ai_frames": worker.ai_frames,
            "ai_errors": worker.ai_errors,
            "capture_paused": worker.capture_paused,
            "tracks_now": len(eng.tracker.tracks) if eng else 0,
            "detections_now": len(eng.last_dets) if eng else 0,
            "plate_ocr": bool(eng and eng.plate_reader is not None),
            "line": cfg.get("line"),
            "entry_direction": cfg.get("entry_direction"),
            "vehicle_classes": cfg.get("vehicle_classes"),
            "confidence": cfg.get("confidence"),
        },
        "whatsapp": {
            "enabled": bool(cfg.get("wa_enabled")),
            "api_key_set": bool(cfg.get("wa_api_key")),
            "recipients": len(cfg.get("wa_recipients") or []),
            "send_image": bool(cfg.get("wa_send_image", True)),
            "schedule_enabled": bool(cfg.get("wa_schedule_enabled")),
        },
        "capture_schedule": {
            "enabled": bool(cfg.get("capture_schedule_enabled")),
            "start": cfg.get("capture_start"),
            "end": cfg.get("capture_end"),
        },
        "camera_log": _tail(CAMERA_LOG),
        "wa_log": _tail(whatsapp.LOG_PATH),
        "engine_out_log": _tail(os.path.join(config.BASE_DIR, "engine_out.log"), 40),
        "app_log": _tail(os.path.join(config.BASE_DIR, "app_log.txt"), 40),
        "versions": _lib_versions(),
    }


def _lib_versions():
    out = {}
    for name in ("torch", "torchvision", "ultralytics", "cv2", "easyocr", "numpy"):
        try:
            mod = __import__(name)
            out[name] = str(getattr(mod, "__version__", "?"))
        except Exception as e:
            out[name] = f"IMPORT FAIL: {str(e)[:80]}"
    return out


# ---- retention / auto-cleanup ------------------------------------------------
def _purge_old():
    try:
        cfg = _cfg()
        if not cfg.get("auto_delete_enabled", True):
            return
        days = max(1, int(cfg.get("retention_days", 7) or 7))
        removed = _db.purge_older_than(days)
        _db.outbox_purge_older_than(days)
        for p in removed:
            try:
                os.remove(p)
            except Exception:
                pass
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        base = config.SNAPSHOT_DIR
        for name in os.listdir(base):
            full = os.path.join(base, name)
            if os.path.isdir(full) and re.match(r"^\d{4}-\d{2}-\d{2}$", name) and name < cutoff:
                shutil.rmtree(full, ignore_errors=True)
        clog(f"purge: {len(removed)} old events removed (retention {days}d, cutoff {cutoff})")
    except Exception:
        import traceback

        clog("purge error:\n" + traceback.format_exc())


def _purge_loop():
    while True:
        _purge_old()
        time.sleep(6 * 3600)


# ---- WhatsApp outbox retry (auto-deliver when internet returns) ---------------
_outbox_sender = WhatsAppNotifier(config.load_config(), db=_db)


def _outbox_loop():
    while True:
        time.sleep(int(os.environ.get("OUTBOX_RETRY_SECONDS", "30")))
        try:
            if _db.outbox_count() == 0:
                continue
            _outbox_sender.update(_cfg())
            n = _outbox_sender.flush_outbox()
            if n:
                clog(f"outbox: {n} pending WhatsApp alert(s) delivered")
        except Exception:
            import traceback

            clog("outbox loop error:\n" + traceback.format_exc())


# ---- updates ----------------------------------------------------------------
_update_info = {"available": False, "latest": "", "page": "", "checked_at": 0.0}


def _refresh_update_info():
    """Background check feeding the sidebar 'New version' badge (via /api/state)."""
    cfg = _cfg()
    repo = updater.DEFAULT_REPO or cfg.get("github_repo", "").strip()
    if not repo:
        return
    try:
        tag, _asset, page = updater.check_latest(repo, token=updater.effective_token(cfg.get("gh_token")))
        _update_info.update(
            available=bool(tag) and updater.is_newer(tag), latest=tag, page=page, checked_at=time.time()
        )
    except Exception as e:
        clog(f"update auto-check failed: {e}")


def _update_check_loop():
    time.sleep(int(os.environ.get("UPDATE_CHECK_DELAY", "20")))
    while True:
        _refresh_update_info()
        time.sleep(int(os.environ.get("UPDATE_CHECK_SECONDS", str(6 * 3600))))


@app.get("/api/update/check")
def update_check(request: Request):
    _check(request)
    cfg = _cfg()
    repo = updater.DEFAULT_REPO or cfg.get("github_repo", "").strip()
    token = updater.effective_token(cfg.get("gh_token"))
    if not repo:
        return {"current": updater.APP_VERSION, "available": False,
                "message": "Update source sirf installed build me set hota hai (dev mode)."}
    try:
        tag, asset, page = updater.check_latest(repo, token=token)
    except Exception as e:
        raise HTTPException(502, str(e))
    _update_info.update(
        available=bool(tag) and updater.is_newer(tag), latest=tag, page=page, checked_at=time.time()
    )
    if not tag:
        hint = ("Token software me inbuilt hai — GitHub par release publish hui hai ya nahi, "
                "aur repo secret UPDATE_TOKEN ka access check karein."
                if updater.DEFAULT_TOKEN else
                "Repo PRIVATE ho to GitHub repo secret UPDATE_TOKEN set karke dobara build "
                "karein (token app me inbuilt ho jayega), ya Settings > Updates me token daalein, "
                "ya repo public karein.")
        return {"current": updater.APP_VERSION, "available": False,
                "message": "GitHub par koi release nahi dikh rahi. " + hint}
    if not updater.is_newer(tag):
        return {"current": updater.APP_VERSION, "latest": tag, "available": False,
                "message": f"Aap latest version par hain (v{updater.APP_VERSION})."}
    return {"current": updater.APP_VERSION, "latest": tag, "available": True,
            "has_asset": bool(asset), "page": page,
            "message": f"Nayi version v{tag} available hai!"}


_UPDATE_ACTIVE = ("checking", "downloading", "installing")
_update_job = {"state": "idle", "percent": 0, "read": 0, "total": 0, "message": "",
               "latest": "", "ok": False, "started_at": 0.0}
_update_cancel = threading.Event()


def _job_set(**kw):
    _update_job.update(kw)


def _run_update_job(repo, token):
    import tempfile

    dest = ""
    try:
        _job_set(state="checking", message="Release ki jaankari li ja rahi hai...")
        tag, asset, _page = updater.check_latest(repo, token=token)
        if not tag or not asset:
            raise RuntimeError("Release/asset nahi mila.")
        ext = ".zip" if asset.lower().endswith(".zip") else ".exe"
        dest = os.path.join(tempfile.gettempdir(), "9xSecuritySetup_new" + ext)
        _job_set(state="downloading", latest=tag, message=f"v{tag} download ho raha hai...")

        def prog(read, total):
            _job_set(read=read, total=total, percent=int(read * 100 / total) if total else 0)

        got = updater.download(asset, dest, progress=prog, token=token,
                               should_stop=_update_cancel.is_set)
        if got is None:
            _job_set(state="cancelled", message="Download cancel kiya gaya.")
            clog("update: download cancelled by user")
            return
        _job_set(state="installing", percent=100,
                 message="Installer chal raha hai — app band hoke nayi version ke saath khulegi.")
        clog(f"update: downloaded v{tag} -> {dest}, launching installer")
        launched = updater.apply_update(dest)
        _job_set(state="done", ok=launched,
                 message=("Installer chal gaya — app band hoke nayi version ke saath khulegi."
                          if launched else f"Dev mode: installer yahan save hua: {dest}"))
    except Exception as e:
        clog(f"update: FAILED {e}")
        _job_set(state="error", message=f"Update fail: {e}")
    finally:
        if _update_job["state"] in ("cancelled", "error") and dest:
            try:
                os.remove(dest)
            except Exception:
                pass


@app.post("/api/update/apply")
def update_apply(request: Request):
    _check(request)
    if _update_job["state"] in _UPDATE_ACTIVE:
        return dict(_update_job)  # already running: return current progress
    cfg = _cfg()
    repo = updater.DEFAULT_REPO or cfg.get("github_repo", "").strip()
    token = updater.effective_token(cfg.get("gh_token"))
    if not repo:
        raise HTTPException(400, "Update source sirf installed build me set hota hai (dev mode).")
    _update_cancel.clear()
    _update_job.update(state="checking", percent=0, read=0, total=0, message="Shuru ho raha hai...",
                       latest="", ok=False, started_at=time.time())
    threading.Thread(target=_run_update_job, args=(repo, token), daemon=True).start()
    return dict(_update_job)


@app.get("/api/update/progress")
def update_progress(request: Request):
    _check(request)
    return dict(_update_job)


@app.post("/api/update/cancel")
def update_cancel(request: Request):
    _check(request)
    if _update_job["state"] in ("checking", "downloading"):
        _update_cancel.set()
        return {"ok": True}
    return {"ok": False}


# ---- static web (container testing / optional) --------------------------------
WEB_DIR = os.environ.get("WEB_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "electron", "dist"
)
if os.path.isdir(WEB_DIR):
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


def selftest():
    """CI/packaging check: real YOLO inference + EasyOCR model load inside the
    (frozen) engine. Exit 1 if detection is broken so a bad build never ships."""
    import numpy as np

    from detector import VehicleDetector
    from plate_reader import PlateReader, _bundled_model_dir

    rc = 0
    try:
        t0 = time.time()
        VehicleDetector().detect(np.zeros((config.DISPLAY_HEIGHT, config.DISPLAY_WIDTH, 3), np.uint8))
        print(f"SELFTEST yolo OK ({(time.time() - t0) * 1000:.0f} ms)", flush=True)
    except Exception:
        import traceback

        print("SELFTEST yolo FAIL:\n" + traceback.format_exc(), flush=True)
        rc = 1
    print(f"SELFTEST easyocr models dir: {_bundled_model_dir()}", flush=True)
    pr = PlateReader()
    print("SELFTEST easyocr " + ("OK" if pr.warmup() else f"FAIL: {pr.last_error}"), flush=True)
    print("SELFTEST versions: " + str(_lib_versions()), flush=True)
    return rc


def main():
    if os.environ.get("NX_SELFTEST"):
        raise SystemExit(selftest())
    cfg = _cfg()
    if not cfg.get("auth_hash"):
        salt, h = auth.hash_password("9xsecurity")
        cfg["auth_salt"], cfg["auth_hash"] = salt, h
        config.save_config(cfg)
    clog(f"svc: starting on 127.0.0.1:{PORT} v{updater.APP_VERSION}")
    threading.Thread(target=_purge_loop, daemon=True).start()
    threading.Thread(target=_outbox_loop, daemon=True).start()
    threading.Thread(target=_update_check_loop, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
