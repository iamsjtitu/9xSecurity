"""9x Security - local engine service (FastAPI) powering the Electron UI.

Runs on 127.0.0.1 only. The Electron renderer talks to it over HTTP and
receives live video as an MJPEG stream.
"""
import os
import re
import secrets
import shutil
import socket
import sys
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
import chime
import config
import report
import updater
from database import EventDB
from engine import (
    HEVC_CODECS, LatestFrameReader, SecurityEngine, auto_fix_stream_url, clog, codec_name, draw_line2, line2_px,
    normalize_rtsp_url, open_stream, probe_rtsp, substream_url,
)
from whatsapp import WhatsAppNotifier, parse_recipients

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


AUTO_MODEL_MAX_MS = int(os.environ.get("AUTO_MODEL_MAX_MS", "350"))  # accurate model budget per frame
# PC Load meter thresholds (dashboard card): a camera whose AI sees < 4 frames/s can miss a fast vehicle
LOAD_FPS_WARN, LOAD_FPS_BAD = 8.0, 4.0
LOAD_AI_MS_WARN, LOAD_AI_MS_BAD = 250, 400
LOAD_CPU_WARN, LOAD_CPU_BAD = 70, 85


class SharedPlateReader:
    """One EasyOCR instance for all camera workers (models are heavy); reads are serialised."""

    def __init__(self):
        self._reader = None
        self._lock = threading.Lock()

    def _get(self):
        if self._reader is None:
            from plate_reader import PlateReader

            self._reader = PlateReader()
        return self._reader

    def warmup(self):
        with self._lock:
            return self._get().warmup()

    def read_many(self, crops, budget_s=8.0):
        with self._lock:
            return self._get().read_many(crops, budget_s=budget_s)

    def read(self, crop):
        with self._lock:
            return self._get().read(crop)

    @property
    def last_error(self):
        return getattr(self._reader, "last_error", "")

    @property
    def last_trace(self):
        return getattr(self._reader, "last_trace", [])


_plate_reader = SharedPlateReader()
_open_lock = threading.Lock()  # open_stream sets process-wide FFmpeg env options: one open at a time


class Worker:
    """One camera = one worker (own stream + own engine). All saved cameras run together; each
    stays in STANDBY (stream closed, no CPU) outside its own time window unless it is the camera
    being viewed on the dashboard."""

    def __init__(self, cam_id=""):
        self.cam_id = cam_id
        self._running = False
        self.user_stopped = False  # set by Disconnect: auto-connect stays quiet until Connect
        self._fix_attempts = {}   # source url -> ts of last stream-path discovery attempt
        self.open_error = ""      # why the last live open failed: '' | auth | none | no-answer | path-ok
        self._gen = 0             # run generation: a restarted loop never keeps an old thread alive
        self.thread = None
        self.status = "Idle — camera URL daal kar Connect dabayein"
        self.engine = None
        self._jpeg = None
        self._lock = threading.Lock()
        self.last_frame_ts = 0.0
        self.capture_paused = False
        self.standby = False      # outside own time window and not viewed: stream closed
        self.ai_error = ""        # last AI failure (self-test / per-frame), '' = healthy
        self.ai_ms = None         # smoothed process_frame time (ms)
        self.ai_frames = 0        # frames processed by AI since connect
        self.ai_errors = 0
        self._last_err_log = 0.0
        self.last_event = None    # latest captured event (for the UI capture toast)
        self.codec = ""           # stream codec (hevc/h264/...) for diagnostics
        self.frames_dropped = 0   # live frames skipped because the AI was busy (normal, keeps lag ~0)
        self.fps = None           # frames/s the AI loop actually processed (2 s window) — load meter
        self.drop_pct = None      # % of camera frames skipped in that window (camera faster than the AI)

    # ---- per-camera config -------------------------------------------------
    def camera(self, cfg=None):
        cfg = cfg or _cfg()
        return config.camera_by_id(cfg, self.cam_id)

    def cam_cfg(self, cfg=None):
        cfg = cfg or _cfg()
        cam = config.camera_by_id(cfg, self.cam_id)
        return config.camera_cfg(cfg, cam) if cam else cfg

    @property
    def name(self):
        cam = self.camera()
        return (cam or {}).get("name") or "Camera"

    def is_viewed(self, cfg=None):
        cfg = cfg or _cfg()
        return not self.cam_id or cfg.get("active_camera_id") == self.cam_id

    def window_open(self, cfg=None):
        cam = self.camera(cfg)
        return config.camera_window_open(cam) if cam else True

    def should_stream(self, cfg=None):
        """Stream only inside the camera's own window — or whenever the user is looking at it."""
        cfg = cfg or _cfg()
        return self.window_open(cfg) or self.is_viewed(cfg)

    def _log(self, msg):
        clog(f"svc[{self.name}]: {msg}" if self.cam_id else f"svc: {msg}")

    def _on_event(self, ev):
        self.last_event = {
            "id": ev.get("id"),
            "direction": ev.get("direction"),
            "vehicle_type": ev.get("vehicle_type"),
            "category": ev.get("category", "vehicle"),
            "count": int(ev.get("count") or 1),
            "gate": ev.get("gate", ""),
            "plate": ev.get("plate", ""),
            "plate_status": ev.get("plate_status", ""),
            "plate_source": ev.get("plate_source", ""),
            "image_path": ev.get("image_path", ""),
            "timestamp": ev.get("timestamp"),
        }
        if ev.get("plate_status") != "done":  # first emission only (OCR backfill re-fires the same event)
            try:
                if chime.maybe_play(self.engine.cfg if self.engine else _cfg(), ev):
                    clog(f"chime: {ev.get('category')} {ev.get('direction')} — ghanti bajayi (event {ev.get('id')})")
            except Exception as e:
                clog(f"chime: error {e}")

    @property
    def connected(self):
        return bool(self.thread and self.thread.is_alive() and self._running)

    def start(self):
        self.stop()
        old = self.thread
        if old is not None and old.is_alive() and old is not threading.current_thread():
            old.join(timeout=3)  # let the previous loop release the camera (session limits!)
        self._gen += 1
        self._running = True
        self.thread = threading.Thread(target=self._run, args=(self._gen,), daemon=True)
        self.thread.start()

    def stop(self):
        self._running = False
        self._gen += 1  # any older loop exits at its next iteration
        with self._lock:
            self._jpeg = None

    def _alive(self, gen):
        return self._running and gen == self._gen

    def latest(self):
        with self._lock:
            return self._jpeg

    def apply_cfg(self, cfg):
        if self.engine is not None:
            cfg = self.cam_cfg(cfg)
            self.engine.cfg = cfg
            try:
                self.engine.notifier.update(cfg)
                self.engine.detector.set_allowed(config.detect_classes(cfg))
                self.engine.detector.conf = float(cfg.get("confidence", 0.4))
            except Exception:
                pass

    def _run(self, gen):
        try:
            self._run_impl(gen)
        except Exception:
            import traceback

            clog("Worker CRASH:\n" + traceback.format_exc())
            if gen == self._gen:
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
            l2 = line2_px(cfg, w, h)
            if l2:
                draw_line2(frame, *l2)
            if self.ai_error or self.engine is None:
                msg = "AI ERROR - detection band hai. Settings > Diagnostics dekhein"
                cv2.rectangle(frame, (0, h - 30), (w, h), (0, 0, 160), -1)
                cv2.putText(frame, msg, (10, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                            (255, 255, 255), 1)
        except Exception:
            pass
        return frame

    def _open(self, source, live, gen=None):
        """open_stream + (for live RTSP) a latest-frame reader so the decoder never
        waits for the AI. Records the stream codec for diagnostics."""
        with _open_lock:
            cap = open_stream(source, (lambda: self._alive(gen)) if gen is not None else (lambda: self._running))
        if cap is None:
            return None
        self.codec = codec_name(cap)
        self.frames_dropped = 0
        if live:
            cap = LatestFrameReader(cap)
        self._log(f"source opened codec={self.codec or 'unknown'} live={live}")
        if self.codec in HEVC_CODECS:
            self._log("HEVC/H.265 stream — agar tasveer tooti/dhundli aaye to camera me H.264 "
                      "ya sub-stream (Hikvision: /Streaming/Channels/102, Dahua: subtype=1) use karein")
        return cap

    def _auto_fix_source(self, source):
        """Live RTSP open failed: ask the camera why (DESCRIBE) and, for a wrong/missing stream
        path, discover the real one and save it as THIS camera's URL. Re-tried per URL every 10 min."""
        now = time.time()
        last = self._fix_attempts.get(source)
        if last and now - last < 600:
            return ""
        self._fix_attempts[source] = now
        self.status = "Camera ka stream path dhundh raha hai (ONVIF / vendor paths)..."
        try:
            fixed, why = auto_fix_stream_url(source)
        except Exception as e:
            self._log(f"stream path discovery error: {e}")
            return ""
        self.open_error = why
        if not fixed:
            return ""
        cfg = _cfg()
        cam = config.camera_by_id(cfg, self.cam_id)
        if cam is not None:
            cam["rtsp_url"] = fixed
        if cam is None or cfg.get("active_camera_id") == self.cam_id:
            cfg["rtsp_url"] = fixed
        config.save_config(cfg)
        self._fix_attempts.pop(source, None)
        return fixed

    def _open_error_status(self):
        why = getattr(self, "open_error", "")
        if why == "auth":
            return "ERROR: Camera ka username/password galat hai (camera ne 401 diya) — URL check karein"
        if why == "none":
            return ("ERROR: Camera par ye stream path nahi hai aur auto-detect nahi hua — camera ke brand ka "
                    "RTSP path lagayein (Test dabakar hints dekhein)")
        return "ERROR: Camera nahi khula — 'Test' se step-by-step jaanch karein"

    def _ai_selftest(self):
        """Run real inferences right after model load so a broken torch/
        torchvision build shows up immediately (not silently per frame).
        Returns the ms of the 2nd (warm) inference."""
        import numpy as np

        blank = np.zeros((config.DISPLAY_HEIGHT, config.DISPLAY_WIDTH, 3), dtype=np.uint8)
        self.engine.detector.detect(blank)  # warm-up (lazy torch init)
        t0 = time.time()
        dets = self.engine.detector.detect(blank)
        ms = (time.time() - t0) * 1000
        self._log(f"AI self-test OK ({getattr(self.engine.detector, 'model_name', '?')}, {ms:.0f} ms, {len(dets)} dets on blank frame)")
        return ms

    def _standby_status(self, cam):
        sch = config.camera_schedule(cam)
        return (f"Standby — {cam.get('name', 'Camera')} ka time {config.fmt12(sch['start'])} se {config.fmt12(sch['end'])} tak hai; "
                f"us time apne aap chalu hoga (stream band, PC par load nahi)")

    def _wait_standby(self, gen):
        """Outside own window and not viewed: hold with the stream closed. Returns False if stopped."""
        cam = self.camera()
        if cam is None:
            return self._alive(gen)
        self.standby = True
        self.capture_paused = True
        self.status = self._standby_status(cam)
        self._log("standby — apne time window ke bahar, stream band")
        while self._alive(gen) and not self.should_stream():
            time.sleep(2)
        self.standby = False
        if self._alive(gen):
            self._log("standby khatam — stream khol raha hai")
        return self._alive(gen)

    def _run_impl(self, gen):
        cfg = self.cam_cfg()
        self.ai_error, self.ai_ms, self.ai_frames, self.ai_errors = "", None, 0, 0
        self.status = "AI model load ho raha hai..."
        self._log("loading AI model")
        try:
            self.engine = SecurityEngine(cfg=cfg, db=_db, plate_reader=_plate_reader if cfg.get("enable_plate") else None)
            self.engine.on_event = self._on_event
            self._log("AI model loaded")
        except Exception:
            import traceback

            self._log("MODEL LOAD FAILED:\n" + traceback.format_exc())
            self.engine = None
            self.ai_error = "model load: " + traceback.format_exc().strip().splitlines()[-1][:200]
        if self.engine is not None:
            try:
                self.ai_ms = self._ai_selftest()
                if (cfg.get("detector_model", "auto") == "auto" and self.engine.model_tier == "accurate"
                        and self.ai_ms > AUTO_MODEL_MAX_MS):
                    name = self.engine.use_fast_model()
                    self._log(f"accurate model too slow here ({self.ai_ms:.0f} ms > {AUTO_MODEL_MAX_MS}) -> {name}")
                    self.ai_ms = self._ai_selftest()
            except Exception:
                self._note_ai_error("AI self-test", __import__("traceback").format_exc())
                self._log("AI self-test FAILED (detection disabled):\n" + __import__("traceback").format_exc())
                self.engine = None

        while self._alive(gen):
            if not self.should_stream() and not self._wait_standby(gen):
                break
            cfg = self.cam_cfg()
            if self.engine is not None:
                self.engine.cfg = cfg
            if not self._stream_once(gen, cfg):
                break
        if gen == self._gen and (self.status.startswith("Connected") or self.status.startswith("Stream")):
            self.status = "Disconnected."

    def _stream_once(self, gen, cfg):
        """Open the camera and process frames until stop / standby. Returns True to continue (standby)."""
        url = cfg.get("rtsp_url", "").strip()
        source = normalize_rtsp_url(url) if url else 0
        live = isinstance(source, str) and source.lower().startswith("rtsp")
        self.status = "Camera se connect ho raha hai..."
        cap = self._open(source, live, gen)
        if (cap is None or not cap.isOpened()) and live and self._alive(gen):
            fixed = self._auto_fix_source(source)
            if fixed:
                if cap is not None:
                    cap.release()
                source = fixed
                self.status = "Stream path mil gaya — dobara connect ho raha hai..."
                cap = self._open(source, live, gen)
        if cap is None or not cap.isOpened():
            if cap is not None:
                cap.release()
            if self._alive(gen):
                self.status = self._open_error_status()
                self._running = False
            return False
        self.status = self._live_status()
        self._log("streaming started")
        fail = 0
        paused = False
        last_ok = time.time()
        self.last_frame_ts = last_ok
        last_sched_check = 0.0
        to_standby = False
        self.fps, self.drop_pct = None, None
        win_t0, win_n, win_drop0 = time.time(), 0, getattr(cap, "dropped", 0)
        while self._alive(gen):
            ok, frame = cap.read()
            if not self._alive(gen):
                break
            if time.time() - last_sched_check > 2:
                last_sched_check = time.time()
                if not self.should_stream():
                    to_standby = True
                    break
            if not ok:
                fail += 1
                # watchdog: reconnect on 50 bad reads OR >15s without a good frame
                if fail > 50 or time.time() - last_ok > 15:
                    self.status = "Stream toota — dobara connect ho raha hai..."
                    self._log("stream lost, reconnecting (watchdog)")
                    cap.release()
                    cap = self._open(source, live, gen)
                    if cap is None:
                        return False
                    if not paused:
                        self.status = self._live_status()
                    fail = 0
                    last_ok = time.time()
                time.sleep(0.02)
                continue
            fail = 0
            last_ok = time.time()
            self.last_frame_ts = last_ok
            self.frames_dropped = getattr(cap, "dropped", 0)
            win_n += 1
            if last_ok - win_t0 >= 2.0:  # load meter: processed fps + skipped share over the last 2 s
                dd = max(0, self.frames_dropped - win_drop0)
                self.fps = round(win_n / (last_ok - win_t0), 1)
                self.drop_pct = round(100.0 * dd / (dd + win_n)) if (dd + win_n) else 0
                win_t0, win_n, win_drop0 = last_ok, 0, self.frames_dropped
            small = cv2.resize(frame, (config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT))
            live_cfg = self.engine.cfg if self.engine else cfg
            cam = self.camera()
            window_open = config.camera_window_open(cam) if cam else True
            capture_on = window_open and ((not live_cfg.get("capture_schedule_enabled")) or config.in_time_window(
                live_cfg.get("capture_start", "18:00"), live_cfg.get("capture_end", "06:00")
            ))
            if capture_on and paused:
                paused = False
                self.capture_paused = False
                self.status = self._live_status()
                self._log("capture resumed (schedule)")
            elif not capture_on and not paused:
                paused = True
                self.capture_paused = True
                if not window_open:
                    sch = config.camera_schedule(cam)
                    self.status = (f"Connected — {cam.get('name', 'Camera')} ka time {config.fmt12(sch['start'])} se "
                                   f"{config.fmt12(sch['end'])} tak: abhi capture PAUSED (sirf live view)")
                else:
                    self.status = (
                        f"Connected — capture PAUSED (schedule {live_cfg.get('capture_start')}"
                        f"-{live_cfg.get('capture_end')} ke bahar), video chalu hai"
                    )
                self._log("capture paused (schedule)")
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
            if okj and gen == self._gen:  # a superseded loop must not paint over the new one
                with self._lock:
                    self._jpeg = buf.tobytes()
            time.sleep(0.01)
        cap.release()
        if self.engine is not None:
            try:
                self.engine.flush_pending(force=True)  # pending person group must not be lost
            except Exception:
                pass
        if to_standby:
            with self._lock:
                self._jpeg = None
        return to_standby


class WorkerPool:
    """One Worker per saved camera; all run together (each in its own time window)."""

    def __init__(self):
        self.workers = {}
        self._lock = threading.Lock()

    def get(self, cam_id):
        with self._lock:
            w = self.workers.get(cam_id)
            if w is None:
                w = self.workers[cam_id] = Worker(cam_id)
            return w

    def active(self, cfg=None):
        """Worker of the camera being VIEWED on the dashboard (line drawing, Connect/Disconnect)."""
        cfg = cfg or _cfg()
        return self.get(cfg.get("active_camera_id") or "")

    def all(self):
        with self._lock:
            return list(self.workers.values())

    def apply_cfg(self, cfg):
        for w in self.all():
            w.apply_cfg(cfg)

    def last_event(self):
        evs = [w.last_event for w in self.all() if w.last_event]
        return max(evs, key=lambda e: e.get("timestamp") or "") if evs else None

    def any_connected(self):
        return any(w.connected for w in self.all())

    def sync(self, cfg, auto=False):
        """Make the running workers match the camera list: monitored cameras with a URL run,
        the rest stop; removed cameras are dropped. auto=True also (re)starts stopped workers
        (auto-connect loop)."""
        cams = {c["id"]: c for c in cfg.get("cameras") or []}
        for cid, w in list(self.workers.items()):
            if (cid and cid not in cams) or (not cid and cams):  # removed camera / legacy no-id worker
                w.stop()
                with self._lock:
                    self.workers.pop(cid, None)
        started = []
        for cid, cam in cams.items():
            w = self.get(cid)
            wanted = bool(cam.get("monitor", True)) and bool((cam.get("rtsp_url") or "").strip())
            if not wanted:
                if w.connected or w._running:
                    w.stop()
                    w.status = "Monitoring OFF — Connect dabakar chalu karein" if cam.get("rtsp_url") else \
                        "Is camera ka RTSP URL khaali hai — Settings > Cameras me URL daalein"
                continue
            if auto and not w.connected and not w._running and not w.user_stopped:
                w.status = "Auto-connect: camera se connect ho raha hai..."
                w.start()
                started.append(cam.get("name", cid))
        return started


pool = WorkerPool()


class _ActiveWorkerProxy:
    """Back-compat: `worker.<attr>` always means the camera currently viewed on the dashboard."""

    def __getattr__(self, name):
        return getattr(pool.active(), name)

    def __setattr__(self, name, value):
        setattr(pool.active(), name, value)


worker = _ActiveWorkerProxy()


# ---- auth ------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"ok": True, "version": updater.APP_VERSION}


@app.get("/api/public/status")
def public_status():
    """Lock/login screen proof that monitoring keeps running (no secrets)."""
    return {
        "connected": pool.any_connected(),
        "events_today": _db.counts_today(),
        "last_event_time": (pool.last_event() or {}).get("timestamp"),
    }


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
        "cameras": _cameras_public(cfg),
        "active_camera_id": cfg.get("active_camera_id", ""),
        "gate": config.gate_name(cfg),
        "wa_enabled": bool(cfg.get("wa_enabled")),
        "enable_plate": bool(cfg.get("enable_plate")),
        "vehicle_classes": cfg.get("vehicle_classes", ["car", "truck", "bus"]),
        "enable_person": bool(cfg.get("enable_person")),
        "enable_two_wheeler": bool(cfg.get("enable_two_wheeler")),
        "cat_schedules": cfg.get("cat_schedules", config.DEFAULTS["cat_schedules"]),
        "detector_model": cfg.get("detector_model", "auto"),
        "confidence": cfg.get("confidence", 0.4),
        "ai_model": worker.engine.detector.model_name if worker.engine else None,
        "ai_tier": worker.engine.model_tier if worker.engine else None,
        "entry_direction": cfg.get("entry_direction", "pos"),
        "line": cfg.get("line"),
        "line2": cfg.get("line2") or None,
        "entry_direction2": config.entry_direction2(cfg),
        "ignore_zones": cfg.get("ignore_zones") or [],
        "line_hints": list(getattr(worker.engine, "line_hints", []) or []) if worker.engine else [],
        "snapshot_dir": config.SNAPSHOT_DIR,
        "outbox_pending": _db.outbox_count(),
        "auto_lock_minutes": int(cfg.get("auto_lock_minutes", 10) or 0),
        "setup_done": bool(cfg.get("setup_done")) or bool(cfg.get("rtsp_url")),
        "capture_paused": worker.capture_paused,
        "ai_loaded": worker.engine is not None,
        "ai_error": worker.ai_error,
        "ai_ms": round(worker.ai_ms) if worker.ai_ms is not None else None,
        "codec": worker.codec,
        "frames_dropped": worker.frames_dropped,
        "substream_url": _substream_suggestion(cfg),
        "rtsp_url_main": cfg.get("rtsp_url_main", ""),
        "last_event": pool.last_event(),
        "cameras_running": sum(1 for w in pool.all() if w.connected and not w.standby),
        "load": _load_summary(cfg),
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
    """Connect the camera being viewed: save its URL, turn its monitoring ON and start its worker."""
    _check(request)
    cfg = _cfg()
    cfg["rtsp_url"] = str(body.get("url", "")).strip()
    config.save_config(cfg)  # sync: creates 'Camera 1' on a fresh install / updates the viewed camera
    cam = config.active_camera(cfg)
    if cam is not None:
        cam["monitor"] = True
        config.save_config(cfg)
    import ptz

    ptz.reset_cache()
    w = pool.active(cfg)
    w.user_stopped = False
    w.start()
    pool.sync(cfg)
    return {"ok": True}


# ---- saved cameras (all run together, one is VIEWED) ---------------------
def _camera_load(w):
    """Load meter level of one camera: None (not running) | idle (no AI) | warming | ok | warn | bad."""
    if not w or not w.connected or w.standby:
        return None
    if w.engine is None or w.capture_paused:
        return "idle"
    if not w.last_frame_ts or time.time() - w.last_frame_ts > 10:
        return "connecting"
    if w.fps is None:
        return "warming"
    ai = w.ai_ms or 0
    if w.fps < LOAD_FPS_BAD or ai > LOAD_AI_MS_BAD:
        return "bad"
    if w.fps < LOAD_FPS_WARN or ai > LOAD_AI_MS_WARN:
        return "warn"
    return "ok"


def _camera_status(cam):
    w = pool.workers.get(cam["id"])
    sch = config.camera_schedule(cam)
    return {
        "monitor": bool(cam.get("monitor", True)),
        "schedule": sch,
        "window_open": config.camera_window_open(cam),
        "connected": bool(w and w.connected),
        "standby": bool(w and w.standby),
        "capture_paused": bool(w and w.capture_paused),
        "status": (w.status if w else ("Monitoring OFF" if not cam.get("monitor", True) else "Idle")),
        # load meter (dashboard card + diagnostics)
        "ai_ms": (round(w.ai_ms) if w and w.ai_ms is not None else None),
        "ai_frames": w.ai_frames if w else 0,
        "ai_errors": w.ai_errors if w else 0,
        "ai_model": (w.engine.detector.model_name if w and w.engine else None),
        "codec": w.codec if w else "",
        "frames_dropped": w.frames_dropped if w else 0,
        "fps": w.fps if w else None,
        "drop_pct": w.drop_pct if w else None,
        "possible_misses": getattr(w.engine, "possible_misses", 0) if w and w.engine else 0,
        "frame_age": (round(time.time() - w.last_frame_ts, 1) if w and w.connected and w.last_frame_ts else None),
        "load": _camera_load(w),
    }


_sys_load = {"cpu_pct": None, "ram_pct": None, "app_ram_mb": None, "cores": os.cpu_count()}


def _load_sampler():
    """Every 2 s: PC CPU %, RAM %, this engine's RAM (psutil; values stay None when unavailable)."""
    try:
        import psutil
    except Exception:
        clog("svc: psutil nahi mila — PC Load meter me CPU/RAM nahi dikhega")
        return
    proc = psutil.Process()
    psutil.cpu_percent(interval=None)
    while True:
        time.sleep(2.0)
        try:
            _sys_load["cpu_pct"] = round(psutil.cpu_percent(interval=None))
            _sys_load["ram_pct"] = round(psutil.virtual_memory().percent)
            _sys_load["app_ram_mb"] = round(proc.memory_info().rss / 1e6)
        except Exception:
            pass


def _load_summary(cfg):
    """Dashboard 'PC Load' card: PC CPU/RAM + overall level + Hinglish hint naming the slow cameras."""
    cams = cfg.get("cameras") or []
    levels = {c["id"]: _camera_load(pool.workers.get(c["id"])) for c in cams}
    slow = [c.get("name") or c["id"] for c in cams if levels[c["id"]] in ("warn", "bad")]
    cpu = _sys_load["cpu_pct"]
    level = "ok"
    if (cpu is not None and cpu >= LOAD_CPU_BAD) or "bad" in levels.values():
        level = "bad"
    elif (cpu is not None and cpu >= LOAD_CPU_WARN) or "warn" in levels.values():
        level = "warn"
    hint = ""
    if level != "ok":
        hint = ("PC par load zyada hai" + (f" — {', '.join(slow)} ka detection dheema hai" if slow else "")
                + ": us camera ko sub-stream URL dein, time window lagayein (Standby = 0 load), "
                "ya Detection Controls me AI Model 'fast' chunein")
    running = [w for w in pool.all() if w.connected and not w.standby]
    return {**_sys_load, "level": level, "hint": hint, "running": len(running),
            "ai_total_ms": round(sum((w.ai_ms or 0) * (w.fps or 0) for w in running))}  # AI ms spent per second


def _cameras_public(cfg):
    return [{"id": c["id"], "name": c.get("name", ""), "gate": c.get("gate", ""),
             "rtsp_url": c.get("rtsp_url", ""), "active": c["id"] == cfg.get("active_camera_id"),
             **_camera_status(c)}
            for c in cfg.get("cameras") or []]


def _cameras_response(cfg):
    return {"ok": True, "cameras": _cameras_public(cfg), "active_camera_id": cfg.get("active_camera_id", ""),
            "rtsp_url": cfg.get("rtsp_url", ""), "gate": config.gate_name(cfg)}


def _clean_schedule(raw, old):
    """{enabled,start,end} from the request; bad times keep the old value."""
    sch = dict(old)
    if not isinstance(raw, dict):
        return sch
    if "enabled" in raw:
        sch["enabled"] = bool(raw["enabled"])
    for k in ("start", "end"):
        v = str(raw.get(k, "") or "")
        if re.match(r"^\d{1,2}:\d{2}$", v):
            sch[k] = v
    return sch


def _sync_workers(cfg):
    """Camera list changed: start/stop/remove workers to match it (auto-connect rules apply)."""
    import ptz

    ptz.reset_cache()
    started = pool.sync(cfg, auto=bool(cfg.get("auto_connect", True)))
    if started:
        clog(f"svc: cameras started: {', '.join(started)}")


@app.get("/api/cameras")
def cameras_list(request: Request):
    _check(request)
    return _cameras_response(_cfg())


@app.post("/api/cameras")
def cameras_save(body: dict, request: Request):
    """Add ({name, gate, rtsp_url, schedule?, monitor?}) or edit ({id, name?, gate?, rtsp_url?,
    schedule?, monitor?}) a saved camera. schedule = {enabled, start, end} = this camera's own
    capture window (outside it: standby, no snaps, no WhatsApp)."""
    _check(request)
    cfg = _cfg()
    name = str(body.get("name", "") or "").strip()[:40]
    gate = str(body.get("gate", "") or "").strip()[:40]
    url = str(body.get("rtsp_url", "") or "").strip()
    cid = str(body.get("id", "") or "").strip()
    if cid:
        cam = next((c for c in cfg["cameras"] if c.get("id") == cid), None)
        if cam is None:
            raise HTTPException(404, "Camera nahi mila")
        if "name" in body:
            cam["name"] = name or cam.get("name") or "Camera"
        if "gate" in body:
            cam["gate"] = gate
        if "schedule" in body:
            cam["schedule"] = _clean_schedule(body["schedule"], config.camera_schedule(cam))
        if "monitor" in body:
            cam["monitor"] = bool(body["monitor"])
            if cam["monitor"]:
                pool.get(cid).user_stopped = False
        url_changed = "rtsp_url" in body and url != (cam.get("rtsp_url") or "")
        if url_changed:
            cam["rtsp_url"], cam["rtsp_url_main"] = url, ""
            if cid == cfg.get("active_camera_id"):
                cfg["rtsp_url"], cfg["rtsp_url_main"] = url, ""
        config.save_config(cfg)
        pool.apply_cfg(cfg)
        if url_changed and (pool.get(cid).connected or pool.get(cid)._running):
            pool.get(cid).start()  # new URL: the running stream must follow it
        _sync_workers(cfg)
        sch = config.camera_schedule(cam)
        clog(f"svc: camera saved {cid} '{cam['name']}' gate='{cam['gate']}' monitor={cam.get('monitor', True)} "
             f"window={'off' if not sch['enabled'] else sch['start'] + '-' + sch['end']}")
        return _cameras_response(cfg)
    if len(cfg["cameras"]) >= config.MAX_CAMERAS:
        raise HTTPException(400, f"Zyada se zyada {config.MAX_CAMERAS} camera save ho sakte hain")
    new_id = config.new_camera_id(cfg)
    cam = {"id": new_id, "name": name or f"Camera {len(cfg['cameras']) + 1}", "gate": gate,
           "rtsp_url": url, "rtsp_url_main": "", "line": dict(config.DEFAULTS["line"]),
           "entry_direction": "pos", "line2": None, "entry_direction2": "", "ignore_zones": [],
           "monitor": bool(body.get("monitor", True)),
           "schedule": _clean_schedule(body.get("schedule"), config.CAMERA_SCHEDULE_DEFAULT)}
    cfg["cameras"].append(cam)
    first = config.active_camera(cfg) is None
    if first:
        config.activate_camera(cfg, new_id)
    config.save_config(cfg)
    clog(f"svc: camera added {new_id} '{cam['name']}' gate='{gate}'" + (" (active)" if first else ""))
    _sync_workers(cfg)
    return _cameras_response(cfg)


@app.post("/api/cameras/{cid}/activate")
def cameras_activate(cid: str, request: Request):
    """Switch the camera being VIEWED (dashboard video, line drawing). All cameras keep running;
    a viewed camera streams even outside its window (live view only, no capture)."""
    _check(request)
    cfg = _cfg()
    if cid == cfg.get("active_camera_id"):
        return _cameras_response(cfg)
    try:
        cam = config.activate_camera(cfg, cid)
    except ValueError:
        raise HTTPException(404, "Camera nahi mila")
    config.save_config(cfg)
    pool.apply_cfg(cfg)
    clog(f"svc: viewing camera -> {cid} '{cam.get('name', '')}' gate='{cam.get('gate', '')}'")
    _sync_workers(cfg)
    return _cameras_response(cfg)


@app.delete("/api/cameras/{cid}")
def cameras_delete(cid: str, request: Request):
    _check(request)
    cfg = _cfg()
    cams = cfg["cameras"]
    if not any(c.get("id") == cid for c in cams):
        raise HTTPException(404, "Camera nahi mila")
    was_active = cid == cfg.get("active_camera_id")
    cfg["cameras"] = [c for c in cams if c.get("id") != cid]
    if was_active:
        cfg["active_camera_id"] = ""
        if not cfg["cameras"]:
            # last camera removed: nothing is configured any more (sync would otherwise re-create it)
            for k in config.CAMERA_KEYS:
                cfg[k] = config._copy(config.DEFAULTS.get(k, ""))
    config.save_config(cfg)  # sync adopts the first remaining camera when the active one was removed
    clog(f"svc: camera deleted {cid}" + (" (was active)" if was_active else ""))
    pool.apply_cfg(cfg)
    _sync_workers(cfg)
    return _cameras_response(cfg)


def _substream_suggestion(cfg):
    """Sub-stream URL to offer when the live stream is H.265 (or '' when n/a)."""
    if not worker.connected or worker.codec not in HEVC_CODECS:
        return ""
    cur = cfg.get("rtsp_url", "").strip()
    sub = substream_url(cur)
    return sub if sub and sub != cur else ""


@app.post("/api/camera/substream")
def camera_substream(request: Request):
    """One-click switch to the camera's lighter sub-stream (keeps the main URL for revert)."""
    _check(request)
    cfg = _cfg()
    cur = cfg.get("rtsp_url", "").strip()
    sub = substream_url(cur)
    if not sub or sub == cur:
        raise HTTPException(400, "Is camera URL ka sub-stream pattern pata nahi — camera app se sub-stream URL lein.")
    cfg["rtsp_url_main"] = cur
    cfg["rtsp_url"] = sub
    config.save_config(cfg)
    clog(f"svc: switched to sub-stream {sub}")
    import ptz

    ptz.reset_cache()
    worker.start()
    return {"ok": True, "url": sub}


@app.post("/api/camera/mainstream")
def camera_mainstream(request: Request):
    _check(request)
    cfg = _cfg()
    main = (cfg.get("rtsp_url_main") or "").strip()
    if not main:
        raise HTTPException(400, "Main stream URL saved nahi hai.")
    cfg["rtsp_url"] = main
    cfg["rtsp_url_main"] = ""
    config.save_config(cfg)
    clog(f"svc: switched back to main stream {main}")
    worker.start()
    return {"ok": True, "url": main}


@app.post("/api/camera/disconnect")
def camera_disconnect(request: Request):
    """Disconnect the camera being viewed: its monitoring goes OFF (persisted) and its worker stops."""
    _check(request)
    cfg = _cfg()
    cam = config.active_camera(cfg)
    if cam is not None:
        cam["monitor"] = False
        config.save_config(cfg)
    w = pool.active(cfg)
    w.user_stopped = True  # auto-connect must not fight a manual Disconnect
    w.stop()
    w.status = "Disconnected."
    return {"ok": True}


@app.get("/api/camera/brands")
def camera_brands(request: Request):
    """Brand catalog for the URL builder (single source of truth: rtsp_discover.BRANDS)."""
    _check(request)
    from rtsp_discover import BRANDS
    return {"brands": BRANDS}


@app.post("/api/camera/build_url")
def camera_build_url(body: dict, request: Request):
    _check(request)
    from rtsp_discover import build_url
    ip = str(body.get("ip", "")).strip()
    if not ip or not re.match(r"^[A-Za-z0-9.\-]+$", ip):
        raise HTTPException(400, "Camera ka IP address daalein (jaise 192.168.1.28)")
    try:
        url = build_url(
            str(body.get("brand", "auto")), ip, str(body.get("user", "")), str(body.get("password", "")),
            port=body.get("port") or None, channel=body.get("channel") or 1,
            stream=str(body.get("stream", "main")), custom_path=str(body.get("custom_path", "")),
        )
    except (ValueError, KeyError, IndexError) as e:
        raise HTTPException(400, f"URL nahi ban paya: {e}")
    return {"url": url}


@app.post("/api/camera/scan")
def camera_scan(body: dict, request: Request):
    """LAN scan: ONVIF WS-Discovery + RTSP port sweep of the PC's /24 subnets (+ saved camera's subnet)."""
    _check(request)
    from net_scan import scan_network
    from rtsp_discover import split_url
    extra = []
    try:
        saved = _cfg().get("rtsp_url", "")
        if saved:
            extra.append(split_url(normalize_rtsp_url(saved))[0])
    except Exception:
        pass
    port = int(body.get("port") or 554)
    subnets = body.get("subnets") or None
    if subnets and not all(re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}$", str(s)) for s in subnets):
        raise HTTPException(400, "Subnet aise likhein: 192.168.1")
    return scan_network(subnets=subnets, port=port, extra_hosts=extra, log=clog)


@app.post("/api/camera/test")
def camera_test(body: dict, request: Request):
    _check(request)
    url = str(body.get("url", "")).strip()
    if not url:
        raise HTTPException(400, "Pehle RTSP URL daalein.")
    ok, steps, effective = probe_rtsp(url)
    fixed = effective if effective != normalize_rtsp_url(url) else ""
    if fixed:
        cfg = _cfg()
        cfg["rtsp_url"] = fixed
        config.save_config(cfg)
    return {"ok": ok, "steps": [{"name": n, "ok": o, "detail": d} for n, o, d in steps], "url": fixed}


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


@app.post("/api/ignore_zones")
def set_ignore_zones(body: dict, request: Request):
    """Replace the parking/ignore zones: {zones:[{x1,y1,x2,y2}]} normalized 0-1 (max 6)."""
    _check(request)
    zones = []
    for z in (body.get("zones") or [])[:6]:
        try:
            r = {k: min(1.0, max(0.0, float(z[k]))) for k in ("x1", "y1", "x2", "y2")}
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "zone me x1,y1,x2,y2 (0-1) chahiye")
        if abs(r["x2"] - r["x1"]) < 0.02 or abs(r["y2"] - r["y1"]) < 0.02:
            raise HTTPException(400, "zone bahut chhota hai")
        zones.append(r)
    cfg = _cfg()
    cfg["ignore_zones"] = zones
    config.save_config(cfg)
    pool.apply_cfg(cfg)
    clog(f"ignore zones set: {len(zones)}")
    return {"ok": True, "ignore_zones": zones}


@app.post("/api/line")
def set_line(body: dict, request: Request):
    _check(request)
    cfg = _cfg()
    cfg["line"] = {k: float(body[k]) for k in ("x1", "y1", "x2", "y2")}
    config.save_config(cfg)
    pool.apply_cfg(cfg)
    return {"ok": True, "line": cfg["line"]}


@app.post("/api/line2")
def set_line2(body: dict, request: Request):
    """Person/bike line: only person + two-wheeler are judged on it (car/truck/bus keep the main
    line). {clear: true} removes it -> persons/bikes go back to the main line."""
    _check(request)
    cfg = _cfg()
    if body.get("clear"):
        cfg["line2"], cfg["entry_direction2"] = None, ""
        clog("person/bike line removed — person & two-wheeler ab main line par count honge")
    else:
        try:
            cfg["line2"] = {k: float(body[k]) for k in ("x1", "y1", "x2", "y2")}
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "line ke 2 points chahiye (x1,y1,x2,y2)")
        cfg["entry_direction2"] = config.infer_entry_direction2(cfg)
        clog(f"person/bike line set {cfg['line2']} entry_direction2={cfg['entry_direction2']} "
             "(car/truck/bus main line par, person/bike is line par)")
    config.save_config(cfg)
    pool.apply_cfg(cfg)
    return {"ok": True, "line2": cfg["line2"], "entry_direction2": config.entry_direction2(cfg)}


@app.post("/api/swap")
def swap_direction(request: Request):
    """Camera-level Entry/Exit swap: both lines flip together (they stay consistent)."""
    _check(request)
    cfg = _cfg()
    cfg["entry_direction"] = "neg" if cfg.get("entry_direction") == "pos" else "pos"
    if cfg.get("line2"):
        cfg["entry_direction2"] = "neg" if config.entry_direction2(cfg) == "pos" else "pos"
    config.save_config(cfg)
    pool.apply_cfg(cfg)
    return {"ok": True, "entry_direction": cfg["entry_direction"], "entry_direction2": config.entry_direction2(cfg)}


@app.post("/api/swap2")
def swap_direction2(request: Request):
    """Entry/Exit swap for the person/bike line only."""
    _check(request)
    cfg = _cfg()
    if not cfg.get("line2"):
        raise HTTPException(400, "Pehle Person/Bike line banayein")
    cfg["entry_direction2"] = "neg" if config.entry_direction2(cfg) == "pos" else "pos"
    config.save_config(cfg)
    pool.apply_cfg(cfg)
    return {"ok": True, "entry_direction2": cfg["entry_direction2"]}


@app.post("/api/options")
def set_options(body: dict, request: Request):
    _check(request)
    cfg = _cfg()
    if "enable_plate" in body:
        cfg["enable_plate"] = bool(body["enable_plate"])
    if "vehicle_classes" in body:
        cfg["vehicle_classes"] = [v for v in body["vehicle_classes"] if v in ("car", "truck", "bus")]
    if "enable_person" in body:
        cfg["enable_person"] = bool(body["enable_person"])
    if "enable_two_wheeler" in body:
        cfg["enable_two_wheeler"] = bool(body["enable_two_wheeler"])
    if isinstance(body.get("cat_schedules"), dict):
        sch = dict(cfg.get("cat_schedules") or config.DEFAULTS["cat_schedules"])
        for cat in config.CATEGORIES:
            c = body["cat_schedules"].get(cat)
            if isinstance(c, dict):
                cur = dict(sch.get(cat) or {})
                cur["enabled"] = bool(c.get("enabled", cur.get("enabled", False)))
                for k in ("start", "end"):
                    if re.match(r"^\d{1,2}:\d{2}$", str(c.get(k, ""))):
                        cur[k] = c[k]
                sch[cat] = cur
        cfg["cat_schedules"] = sch
    if body.get("detector_model") in ("auto", "fast", "accurate"):
        cfg["detector_model"] = body["detector_model"]
    if "confidence" in body:
        try:
            cfg["confidence"] = min(0.9, max(0.15, float(body["confidence"])))
        except (TypeError, ValueError):
            pass
    if "setup_done" in body:
        cfg["setup_done"] = bool(body["setup_done"])
    if "entry_direction" in body and body["entry_direction"] in ("pos", "neg"):
        cfg["entry_direction"] = body["entry_direction"]
    config.save_config(cfg)
    pool.apply_cfg(cfg)
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


_PLATE_RE = re.compile(r"^[A-Z0-9]{4,12}$")


@app.post("/api/events/{eid}/plate")
def set_event_plate(eid: int, body: dict, request: Request):
    """Manual plate correction (also clears with ''). Searchable like OCR plates."""
    _check(request)
    plate = re.sub(r"[^A-Z0-9]", "", str(body.get("plate", "")).upper())
    if plate and not _PLATE_RE.match(plate):
        raise HTTPException(400, "Plate 4-12 letters/digits ki honi chahiye (jaise MH12AB1234)")
    row = _db.update_event_plate(eid, plate, source="manual", status="done")
    if row is None:
        raise HTTPException(404, "event nahi mila")
    for w in pool.all():
        if w.last_event and w.last_event.get("id") == eid:
            w.last_event = {**w.last_event, "plate": plate, "plate_status": "done",
                            "plate_source": "manual" if plate else ""}
    clog(f"plate manual: event {eid} -> '{plate or '(cleared)'}'")
    return {"ok": True, "event": row}


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
    "wa_enabled", "wa_base_url", "wa_api_key", "wa_recipients", "wa_groups", "wa_send_image",
    "wa_account_email", "wa_account_password", "gh_token",
    "wa_schedule_enabled", "wa_start", "wa_end",
    "capture_schedule_enabled", "capture_start", "capture_end",
    "daily_report_enabled", "daily_report_time",
    "pr_enabled", "pr_api_key", "pr_mode",
    "person_chime_enabled", "vehicle_chime_enabled", "two_wheeler_chime_enabled",
    "person_chime_schedule_enabled", "person_chime_start", "person_chime_end",
    "auto_delete_enabled",
)
_SECRET_KEYS = ("wa_api_key", "gh_token", "wa_account_password", "pr_api_key")
# The WhatsApp API key stays visible in Settings (user request: hidden key made it impossible
# to verify what is saved → failed sends). Other secrets remain write-only.
_VISIBLE_SECRETS = ("wa_api_key", "pr_api_key")


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
    out["auto_lock_minutes"] = int(cfg.get("auto_lock_minutes", 10) or 0)
    out["auto_connect"] = bool(cfg.get("auto_connect", True))
    out["person_chime_volume"] = int(cfg.get("person_chime_volume", 70))
    out["person_chime_supported"] = chime.winsound is not None
    out["cat_schedules"] = cfg.get("cat_schedules", config.DEFAULTS["cat_schedules"])
    out["enable_person"] = bool(cfg.get("enable_person"))
    out["enable_two_wheeler"] = bool(cfg.get("enable_two_wheeler"))
    out["gh_token_builtin"] = bool(updater.DEFAULT_TOKEN)
    out["daily_report_last"] = cfg.get("daily_report_last", "")
    out["daily_report_sent_at"] = cfg.get("daily_report_sent_at", "")
    out["daily_report_next"] = report.next_run(cfg) if cfg.get("daily_report_enabled") else ""
    return out


@app.post("/api/settings")
def save_settings(body: dict, request: Request):
    _check(request)
    cfg = _cfg()
    old_cfg = {"daily_report_enabled": cfg.get("daily_report_enabled"), "daily_report_time": cfg.get("daily_report_time")}
    if "wa_recipients" in body:
        # '8598800000, 9166175477' typed on one line must become two numbers, never one 20-digit one
        numbers, bad = parse_recipients(body["wa_recipients"])
        if bad:
            raise HTTPException(400, "WhatsApp number galat hai: " + ", ".join(bad[:3])
                                + " — har number 10-15 digit ka, alag line ya comma se likhein")
        body = {**body, "wa_recipients": numbers}
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
    if "auto_connect" in body:
        cfg["auto_connect"] = bool(body["auto_connect"])
    for k in ("person_chime_enabled", "vehicle_chime_enabled", "two_wheeler_chime_enabled", "person_chime_schedule_enabled"):
        if k in body:
            cfg[k] = bool(body[k])
    for k in ("person_chime_start", "person_chime_end"):
        if k in body and not re.match(r"^\d{1,2}:\d{2}$", str(body[k])):
            cfg[k] = config.DEFAULTS[k]  # garbage time -> default, never a broken window
    if "daily_report_time" in body and report.parse_time(body["daily_report_time"], default=None) is None:
        raise HTTPException(400, "Report ka time HH:MM (00:00-23:59) me hona chahiye, jaise 21:00")
    if "daily_report_enabled" in body:
        cfg["daily_report_enabled"] = bool(body["daily_report_enabled"])
    if "pr_enabled" in body:
        cfg["pr_enabled"] = bool(body["pr_enabled"])
    if "pr_mode" in body and body["pr_mode"] not in ("always", "fallback"):
        cfg["pr_mode"] = "always"
    if cfg.get("daily_report_enabled") and (
        (not old_cfg.get("daily_report_enabled")) or old_cfg.get("daily_report_time") != cfg.get("daily_report_time")
    ):
        # switched on / time changed: the first PDF goes at the NEXT scheduled time, nothing retroactive
        report.mark_baseline(cfg)
        clog(f"daily report: ON at {cfg.get('daily_report_time')} — pehli PDF {report.next_run(cfg)}")
    if "person_chime_volume" in body:
        try:
            cfg["person_chime_volume"] = max(0, min(100, int(body["person_chime_volume"])))
        except (TypeError, ValueError):
            raise HTTPException(400, "Chime volume 0-100 ke beech hona chahiye")
    if "auto_lock_minutes" in body:
        try:
            cfg["auto_lock_minutes"] = max(0, min(720, int(body["auto_lock_minutes"])))
        except (TypeError, ValueError):
            raise HTTPException(400, "auto_lock_minutes must be a number (0 = off)")
    if "retention_days" in body:
        try:
            cfg["retention_days"] = max(1, min(365, int(body["retention_days"])))
        except Exception:
            pass
    if body.get("new_password"):
        salt, h = auth.hash_password(str(body["new_password"]))
        cfg["auth_salt"], cfg["auth_hash"] = salt, h
    config.save_config(cfg)
    pool.apply_cfg(cfg)
    return {"ok": True}


@app.post("/api/plate/cloud/test")
def plate_cloud_test(body: dict, request: Request):
    """Settings 'Key check karo': validates the Plate Recognizer token (typed or saved) and returns
    this month's usage. Costs no lookup."""
    _check(request)
    from cloud_plate import CloudPlateReader

    key = str((body or {}).get("pr_api_key") or _cfg().get("pr_api_key") or "").strip()
    res = CloudPlateReader(key).statistics(key)
    clog(f"plate cloud: key check -> {'OK' if res.get('valid') else 'FAIL'} {res.get('message', '')[:120]}")
    return res


@app.post("/api/chime/test")
def chime_test(body: dict, request: Request):
    """Play a chime once at the given volume (Settings 'Test sound'); category person|vehicle|two_wheeler."""
    _check(request)
    body = body or {}
    try:
        vol = max(0, min(100, int(body.get("volume", _cfg().get("person_chime_volume", 70)))))
    except (TypeError, ValueError):
        raise HTTPException(400, "volume 0-100")
    category = str(body.get("category", "person") or "person")
    if category not in chime.TONES:
        raise HTTPException(400, "category person|vehicle|two_wheeler")
    supported, detail = chime.play(vol, category)
    clog(f"chime: test {category} ({'played' if supported else 'unsupported'}) vol={vol}")
    return {"ok": True, "supported": supported, "detail": detail, "category": category,
            "wav": os.path.basename(chime.wav_path(category))}


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
            "wa_groups": body.get("wa_groups") if body.get("wa_groups") is not None else cfg.get("wa_groups", []),
            "wa_send_image": bool(body.get("wa_send_image", cfg.get("wa_send_image", True))),
        }
    )
    ok, detail = n.test_connection()
    return {"ok": ok, "detail": detail}


@app.post("/api/whatsapp/groups")
def whatsapp_groups(body: dict, request: Request):
    """List WhatsApp groups of the connected session (for the Settings group picker)."""
    _check(request)
    cfg = _cfg()
    n = WhatsAppNotifier(
        {
            "wa_base_url": body.get("wa_base_url") or cfg.get("wa_base_url") or "https://wa.9x.design",
            "wa_api_key": (str(body.get("wa_api_key", "")).strip() or cfg.get("wa_api_key", "")),
        }
    )
    ok, res = n.list_groups()
    if not ok:
        return {"ok": False, "detail": res, "groups": []}
    return {"ok": True, "groups": res}


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
            "codec": worker.codec,
            "frames_dropped": worker.frames_dropped,
            "capture_paused": worker.capture_paused,
            "tracks_now": (len(eng.tracker.tracks) + len(eng.tracker2.tracks)) if eng else 0,
            "detections_now": len(eng.last_dets) if eng else 0,
            "plate_ocr": bool(eng and eng.plate_reader is not None),
            "line": cfg.get("line"),
            "entry_direction": cfg.get("entry_direction"),
            "line2": cfg.get("line2") or None,
            "entry_direction2": config.entry_direction2(cfg) if cfg.get("line2") else None,
            "vehicle_classes": cfg.get("vehicle_classes"),
            "confidence": cfg.get("confidence"),
            "detector_model": cfg.get("detector_model", "auto"),
            "ai_model": eng.detector.model_name if eng else None,
            "ai_tier": eng.model_tier if eng else None,
            "possible_misses": getattr(eng, "possible_misses", 0) if eng else 0,
            "detect_classes": config.detect_classes(cfg),
        },
        "cameras": [
            {
                "id": c["id"], "name": c.get("name"), "gate": c.get("gate"), "viewed": c["id"] == cfg.get("active_camera_id"),
                **_camera_status(c),
            }
            for c in cfg.get("cameras") or []
        ],
        "load": _load_summary(cfg),
        "whatsapp": {
            "enabled": bool(cfg.get("wa_enabled")),
            "api_key_set": bool(cfg.get("wa_api_key")),
            "recipients": len(cfg.get("wa_recipients") or []),
            "groups": len(cfg.get("wa_groups") or []),
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


# ---- Daily PDF report (per gate: Entry/Exit per vehicle type) -------------------
_report_lock = threading.Lock()


def _send_daily_report(day, manual=False):
    """Build the PDF for `day` and WhatsApp it to every recipient. Returns (ok, detail, pdf_path)."""
    with _report_lock:
        cfg = _cfg()
        summary, path, text = report.generate(_db, day, cfg)
        _outbox_sender.update(cfg)
        ok, detail = _outbox_sender.send_report(text, path)
    clog(f"daily report {day}{' (manual)' if manual else ''} [{report.period_text(summary)}]: Entry {summary['entry']} / "
         f"Exit {summary['exit']} -> {'SENT' if ok else 'PROBLEM'}: {' | '.join(detail.splitlines())[:300]}")
    if ok or "queue me" in detail:
        cfg = _cfg()
        cfg["daily_report_sent_at"] = datetime.now().isoformat(timespec="seconds")
        config.save_config(cfg)
    return ok, detail, path


def _daily_report_loop():
    """Every 30 s: send the report for any day that is due (today at the set time; yesterday too
    when the PC was off at that time). Each day is sent once (daily_report_last)."""
    time.sleep(int(os.environ.get("REPORT_LOOP_DELAY", "40")))
    while True:
        try:
            cfg = _cfg()
            for day in report.due_days(cfg):
                try:
                    _send_daily_report(day)
                except Exception as e:
                    clog(f"daily report {day}: FAILED {e!r}")
                cfg = _cfg()
                cfg["daily_report_last"] = day  # once per day — a failed send is logged, not retried every 30 s
                config.save_config(cfg)
        except Exception:
            import traceback

            clog("daily report loop error:\n" + traceback.format_exc())
        time.sleep(int(os.environ.get("REPORT_LOOP_SECONDS", "30")))


def _report_day(date):
    day = str(date or "").strip() or datetime.now().strftime("%Y-%m-%d")
    try:
        return datetime.strptime(day, "%Y-%m-%d").strftime("%Y-%m-%d")  # normalised (2026-1-5 -> 2026-01-05)
    except ValueError:
        raise HTTPException(400, "date YYYY-MM-DD me hona chahiye")


@app.get("/api/report/daily")
def report_daily(request: Request, date: str = ""):
    """Summary + freshly written PDF for one day's report window (default today — 'abhi tak' if the
    report time has not come yet)."""
    _check(request)
    day = _report_day(date)
    with _report_lock:
        summary, path, text = report.generate(_db, day, _cfg())
    return {"summary": summary, "period": report.period_text(summary), "pdf_path": path, "folder": report.REPORT_DIR,
            "caption": text, "pdf_url": f"/api/report/daily.pdf?date={day}"}


@app.get("/api/report/daily.pdf")
def report_daily_pdf(request: Request, date: str = ""):
    _check(request)
    day = _report_day(date)
    with _report_lock:
        _summary, path, _text = report.generate(_db, day, _cfg())
    return FileResponse(path, media_type="application/pdf", filename=os.path.basename(path))


@app.post("/api/report/send")
def report_send(body: dict, request: Request):
    """'Abhi bhejo' button: today's (or the given day's) PDF to every WhatsApp recipient now."""
    _check(request)
    day = _report_day((body or {}).get("date"))
    ok, detail, path = _send_daily_report(day, manual=True)
    return {"ok": ok, "detail": detail, "pdf_path": path, "date": day}


# ---- Range report (any dates, PDF or Excel) ----------------------------------------
def _range_days(start, end):
    s, e = _report_day(start), _report_day(end)
    try:
        report.range_window(s, e)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return s, e


@app.get("/api/report/range")
def report_range(request: Request, start: str = "", end: str = "", fmt: str = "pdf"):
    """Summary + per-day breakdown for whole days start..end; writes the PDF/Excel into the reports
    folder and returns its path (Electron opens it) plus download URLs (browser)."""
    _check(request)
    s, e = _range_days(start, end)
    fmt = "xlsx" if fmt == "xlsx" else "pdf"
    with _report_lock:
        summary, days, path = report.generate_range(_db, s, e, fmt)
    clog(f"range report {s}..{e} ({fmt}): {summary['events']} events, Entry {summary['entry']} / Exit {summary['exit']}")
    return {"summary": summary, "days": days, "period": report.period_text(summary), "path": path,
            "folder": report.REPORT_DIR, "pdf_url": f"/api/report/range.pdf?start={s}&end={e}",
            "xlsx_url": f"/api/report/range.xlsx?start={s}&end={e}"}


@app.get("/api/report/range.pdf")
def report_range_pdf(request: Request, start: str = "", end: str = ""):
    _check(request)
    s, e = _range_days(start, end)
    with _report_lock:
        _summary, _days, path = report.generate_range(_db, s, e, "pdf")
    return FileResponse(path, media_type="application/pdf", filename=os.path.basename(path))


@app.get("/api/report/range.xlsx")
def report_range_xlsx(request: Request, start: str = "", end: str = ""):
    _check(request)
    s, e = _range_days(start, end)
    with _report_lock:
        _summary, _days, path = report.generate_range(_db, s, e, "xlsx")
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        filename=os.path.basename(path))


# ---- Plate search (dashboard) -----------------------------------------------------
@app.get("/api/plate/search")
def plate_search(request: Request, q: str = ""):
    """Every Entry/Exit of a vehicle number (partial match, e.g. last 4 digits) across all days."""
    _check(request)
    needle = re.sub(r"[^A-Z0-9]", "", str(q or "").upper())
    if len(needle) < 2:
        raise HTTPException(400, "Kam se kam 2 letters/digits likhein (jaise 1234 ya MH12)")
    rows = _db.get_events(plate_filter=needle, limit=1000, by_time=True)
    fuzzy = False
    if not rows and len(needle) >= 4:  # 'MH121234' typed without the series letters -> MH12AB1234
        rows = _db.get_events(plate_filter="%".join(needle), limit=1000, by_time=True)
        fuzzy = bool(rows)
    entries = sum(1 for r in rows if r.get("direction") == "Entry")
    plates = sorted({r.get("plate") for r in rows if r.get("plate")})
    return {"q": needle, "fuzzy": fuzzy, "events": rows, "entries": entries, "exits": len(rows) - entries, "plates": plates,
            "first_seen": rows[-1]["timestamp"] if rows else "", "last_seen": rows[0]["timestamp"] if rows else ""}


# ---- Plate Recognizer credits (dashboard) ------------------------------------------
_usage_cache = {"checked": 0.0, "ttl": 0.0, "key": "", "data": None}
USAGE_CACHE_S = 15 * 60


@app.get("/api/plate/cloud/usage")
def plate_cloud_usage(request: Request, refresh: int = 0):
    """Monthly lookups used/left for the dashboard card (cached 15 min; refresh=1 forces a check)."""
    _check(request)
    cfg = _cfg()
    key = str(cfg.get("pr_api_key") or "").strip()
    out = {"enabled": bool(cfg.get("pr_enabled")) and bool(key), "key_set": bool(key)}
    if not key:
        return {**out, "valid": False, "message": "API key nahi daali"}
    fresh = _usage_cache["data"] is not None and _usage_cache["key"] == key and time.time() < _usage_cache["ttl"]
    if refresh or not fresh:
        from cloud_plate import CloudPlateReader

        data = CloudPlateReader(key).statistics(key)
        now = time.time()
        # a transient network failure must not hide the real numbers for 15 min: keep it 60 s only
        _usage_cache.update(checked=now, key=key, data=data, ttl=now + (USAGE_CACHE_S if (data.get("valid") or data.get("status")) else 60))
    data = dict(_usage_cache["data"])
    if data.get("valid"):
        limit, remaining = int(data.get("limit") or 0), int(data.get("remaining") or 0)
        data["low"] = remaining <= max(100, int(limit * 0.10)) if limit else False
        data["critical"] = remaining <= max(25, int(limit * 0.02)) if limit else False
    else:
        data["message"] = re.sub(r"\s*\[.*\]$", "", str(data.get("message") or ""))  # no raw provider JSON on the card
    data["checked_at"] = datetime.fromtimestamp(_usage_cache["checked"]).isoformat(timespec="seconds")
    return {**out, **data}


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
    for tier in ("fast", "accurate"):
        try:
            from detector import resolve_model_path

            path, got = resolve_model_path(tier)
            if got != tier:
                print(f"SELFTEST yolo {tier}: model file missing ({os.path.basename(path)} used instead)", flush=True)
                if tier == "accurate":
                    print("::warning::yolov8s.pt not bundled — accurate mode unavailable", flush=True)
                continue
            det = VehicleDetector(model_path=path)
            blank = np.zeros((config.DISPLAY_HEIGHT, config.DISPLAY_WIDTH, 3), np.uint8)
            det.detect(blank)
            t0 = time.time()
            det.detect(blank)
            print(f"SELFTEST yolo {tier} ({det.model_name}) OK ({(time.time() - t0) * 1000:.0f} ms)", flush=True)
        except Exception:
            import traceback

            print(f"SELFTEST yolo {tier} FAIL:\n" + traceback.format_exc(), flush=True)
            rc = 1
    print(f"SELFTEST easyocr models dir: {_bundled_model_dir()}", flush=True)
    pr = PlateReader()
    if pr.warmup():
        try:  # exercise the real read path (scipy/skimage/torch extensions inside the bundle)
            img = np.full((120, 420, 3), 245, np.uint8)
            cv2.putText(img, "MH12AB1234", (14, 84), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (10, 10, 10), 5, cv2.LINE_AA)
            pr.candidates(img)
            print("SELFTEST easyocr OK (read path works: " + " | ".join(pr.last_trace)[:160] + ")", flush=True)
        except Exception as e:
            print(f"SELFTEST easyocr FAIL (read): {e}", flush=True)
            rc = 1
    else:
        print(f"SELFTEST easyocr FAIL: {pr.last_error}", flush=True)
        rc = 1  # plates are a core feature: a build without working OCR must not ship
    print("SELFTEST versions: " + str(_lib_versions()), flush=True)
    return rc


def _port_free(port):
    """True when nobody is LISTENING on the port (bind-probing would be fooled by
    TIME_WAIT connections of the previous engine and report 'busy' for a minute)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _auto_connect_loop():
    """After a PC reboot the app starts with Windows: connect every monitored camera by itself
    and keep retrying (camera/network often come up minutes after the PC)."""
    time.sleep(4)  # let uvicorn come up first
    last_log = 0.0
    while True:
        try:
            cfg = _cfg()
            started = pool.sync(cfg, auto=bool(cfg.get("auto_connect", True)))
            if started:
                if time.time() - last_log > 300:
                    clog(f"svc: auto-connect — {', '.join(started)} se connect kar raha hai")
                    last_log = time.time()
                time.sleep(25)  # give the open/AI-load a chance before judging
        except Exception as e:
            clog(f"svc: auto-connect error {e}")
        time.sleep(15)


def _parent_watchdog(ppid):
    """Exit together with the Electron app. An orphan engine (app killed by the
    installer / Task Manager / crash) keeps the camera and port busy, and the next
    app start ends up with two engines — the UI talking to the OLD one."""
    try:
        if sys.platform == "win32":
            import ctypes

            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(0x00100000, False, int(ppid))  # SYNCHRONIZE
            if not h:
                return
            k32.WaitForSingleObject(h, 0xFFFFFFFF)  # returns when the parent exits
        else:
            while os.getppid() == int(ppid):
                time.sleep(2)
    except Exception:
        return
    clog("svc: parent app exited — engine shutting down")
    os._exit(0)


def main():
    if os.environ.get("NX_SELFTEST"):
        raise SystemExit(selftest())
    cfg = _cfg()
    if not cfg.get("auth_hash"):
        salt, h = auth.hash_password("9xsecurity")
        cfg["auth_salt"], cfg["auth_hash"] = salt, h
        config.save_config(cfg)
    clog(f"svc: starting on 127.0.0.1:{PORT} v{updater.APP_VERSION}")
    if os.environ.get("NX_PARENT_PID"):
        threading.Thread(target=_parent_watchdog, args=(os.environ["NX_PARENT_PID"],), daemon=True).start()
    for i in range(30):  # an old engine may still be closing after an update/restart
        if _port_free(PORT):
            break
        if i == 0:
            clog(f"svc: port {PORT} busy — purana engine band hone ka wait (15s)")
        time.sleep(0.5)
    else:
        clog(f"svc: port {PORT} still busy — dusra engine chal raha hai, ye instance exit kar raha hai")
        os._exit(3)
    threading.Thread(target=_purge_loop, daemon=True).start()
    threading.Thread(target=_outbox_loop, daemon=True).start()
    threading.Thread(target=_daily_report_loop, daemon=True).start()
    threading.Thread(target=_load_sampler, daemon=True).start()
    threading.Thread(target=_update_check_loop, daemon=True).start()
    threading.Thread(target=_auto_connect_loop, daemon=True).start()
    # any crash must leave a trace in camera_log (the UI only sees 'fetch failed')
    sys.excepthook = lambda et, ev, tb: clog(f"svc: UNHANDLED {et.__name__}: {ev}")
    threading.excepthook = lambda a: clog(f"svc: THREAD {a.thread.name} died: {a.exc_type.__name__}: {a.exc_value}")
    try:
        uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
        clog("svc: http server stopped — exiting")
    except Exception as e:
        clog(f"svc: http server CRASHED: {e!r} — exiting so the app can restart the engine")
        os._exit(2)
    os._exit(0)  # never linger as a camera-holding zombie without HTTP


if __name__ == "__main__":
    main()
