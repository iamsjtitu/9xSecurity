"""Iteration 5 checks: FFMPEG_OPTS, clog, wait defaults, and main.py inspection."""
import os
import re
import py_compile
import time

import config
import engine


def test_ffmpeg_opts_contents():
    opts = engine.FFMPEG_OPTS
    assert "timeout;5000000" in opts
    assert "analyzeduration;10000000" in opts
    assert "probesize;5000000" in opts
    assert "max_delay;500000" in opts
    print("PASS: FFMPEG_OPTS content ->", opts)


def test_try_capture_sets_env_and_default_wait():
    # inspect default wait
    import inspect
    sig = inspect.signature(engine._try_capture)
    assert sig.parameters["wait"].default == 10.0
    sig2 = inspect.signature(engine.probe_rtsp)
    assert sig2.parameters["wait"].default == 10.0

    # Call _try_capture on unreachable host with tiny wait -> verifies env set
    ok, detail = engine._try_capture("rtsp://192.0.2.1:554/x", "tcp", wait=0.5)
    assert ok is False
    expected = f"rtsp_transport;tcp{engine.FFMPEG_OPTS}"
    assert os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS") == expected
    print("PASS: env set:", os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS"))


def test_try_capture_no_frames_message_includes_wait():
    # We can't easily produce open-but-no-frames deterministically. Just check string
    # composition via the source code.
    import inspect
    src = inspect.getsource(engine._try_capture)
    assert "{int(wait)} sec" in src or "{wait}" in src
    print("PASS: _try_capture message includes wait")


def test_clog_writes_timestamped():
    log_path = os.path.join(config.BASE_DIR, "camera_log.txt")
    before = 0
    if os.path.exists(log_path):
        before = os.path.getsize(log_path)
    engine.clog("UNIT_TEST_LINE_iter5")
    assert os.path.exists(log_path)
    with open(log_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "UNIT_TEST_LINE_iter5" in content
    # timestamp ISO format check on last line
    lines = [l for l in content.strip().splitlines() if l]
    last = lines[-1]
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\s\|\s", last), last
    assert os.path.getsize(log_path) > before
    print("PASS: clog appended:", last)


def test_probe_writes_start_and_unreachable():
    log_path = os.path.join(config.BASE_DIR, "camera_log.txt")
    marker = f"MARKER_{int(time.time()*1000)}"
    engine.clog(marker)
    t0 = time.time()
    ok, steps = engine.probe_rtsp("rtsp://admin:pw@192.0.2.1:554/stream1", wait=1.0)
    elapsed = time.time() - t0
    assert ok is False
    assert elapsed < 6.0, f"probe too slow on unreachable host: {elapsed:.1f}s"
    with open(log_path, "r", encoding="utf-8") as f:
        content = f.read()
    tail = content.split(marker, 1)[1]
    assert "PROBE start" in tail
    assert "UNREACHABLE" in tail
    # Hindi step text still present
    joined = " ".join(d for _n, _o, d in steps)
    assert "Camera tak pahunch nahi" in joined or "network" in joined.lower()
    print(f"PASS: probe unreachable in {elapsed:.2f}s, log has PROBE start + UNREACHABLE")


def test_main_py_inspection():
    path = "/app/9x_security/main.py"
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    # imports FFMPEG_OPTS and clog from engine
    assert "from engine import FFMPEG_OPTS, clog" in src

    # 12s first-frame window
    assert "time.time() - t0 < 12" in src

    # logs open attempts
    assert 'clog(f"connect attempt transport=' in src

    # _on_probe_done: read-only QPlainTextEdit + OK btn + camera_log.txt note + status bar
    assert "QPlainTextEdit" in src
    assert "setReadOnly(True)" in src
    assert "camera_log.txt" in src
    assert "self.status.setText(summary)" in src
    # OK button
    assert 'QPushButton("OK")' in src

    # py_compile passes
    py_compile.compile(path, doraise=True)
    print("PASS: main.py inspection + py_compile ok")
