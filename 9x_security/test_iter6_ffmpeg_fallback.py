"""Iteration 6: VLC-grade FFmpeg pipe fallback validation."""
import os
import time
import py_compile
import inspect

import numpy as np
import pytest

import config
import engine
from engine import FFmpegPipeSource, _try_ffmpeg_pipe, probe_rtsp


BASE = config.BASE_DIR


# ---- FFmpegPipeSource with lavfi testsrc --------------------------------
def test_ffmpeg_pipe_source_lavfi_multiple_frames():
    src = FFmpegPipeSource(
        "testsrc=duration=3:size=320x240:rate=5",
        input_args=["-f", "lavfi"],
    )
    try:
        assert src.isOpened() is True
        frames = []
        for _ in range(3):
            ok, f = src.read()
            assert ok, "expected consecutive frames from lavfi testsrc"
            assert f.shape == (config.DISPLAY_HEIGHT, config.DISPLAY_WIDTH, 3)
            assert f.dtype == np.uint8
            frames.append(f)
        # non-uniform pixels => real decoded content, not zeros
        assert frames[0].std() > 0, "testsrc frame appears uniform/zero"
    finally:
        src.release()
    # give OS a tick then verify process is dead (no zombie)
    time.sleep(0.3)
    assert src.proc.poll() is not None, "ffmpeg subprocess did not terminate after release()"
    print("PASS: lavfi frames shape/dtype/std ok; process terminated cleanly")


def test_ffmpeg_err_and_camera_log_written():
    err_path = os.path.join(BASE, "ffmpeg_err.txt")
    log_path = os.path.join(BASE, "camera_log.txt")
    err_before = os.path.getsize(err_path) if os.path.exists(err_path) else 0
    log_before = os.path.getsize(log_path) if os.path.exists(log_path) else 0

    # Use FFmpegPipeSource directly to guarantee ffmpeg_err.txt & 'starting' log
    src = FFmpegPipeSource(
        "testsrc=duration=1:size=160x120:rate=5",
        input_args=["-f", "lavfi"],
    )
    try:
        ok, _f = src.read()
        assert ok
    finally:
        src.release()
    # 'OK first frame' line is emitted by _try_ffmpeg_pipe wrapper - exercise it
    # via a monkeypatched FFmpegPipeSource that uses lavfi (real network unreachable).
    original_cls = engine.FFmpegPipeSource

    class _LavfiSrc(original_cls):
        def __init__(self, url, input_args=None):
            super().__init__("testsrc=duration=1:size=160x120:rate=5",
                             input_args=["-f", "lavfi"])
    engine.FFmpegPipeSource = _LavfiSrc
    try:
        ok, _detail = engine._try_ffmpeg_pipe("rtsp://fake/stream")
        assert ok, "expected pipe wrapper to succeed with lavfi source"
    finally:
        engine.FFmpegPipeSource = original_cls

    assert os.path.exists(err_path), "ffmpeg_err.txt not created"
    assert os.path.getsize(err_path) >= err_before  # file exists / appended
    with open(log_path, "r", encoding="utf-8") as f:
        content = f.read()
    tail = content[log_before:]
    assert "ffmpeg-pipe: starting" in tail
    assert "OK first frame" in tail
    print("PASS: ffmpeg_err.txt exists; camera_log has start + OK first frame")


# ---- _try_ffmpeg_pipe on unreachable RTSP -------------------------------
def test_try_ffmpeg_pipe_unreachable_returns_false_fast():
    t0 = time.time()
    ok, detail = _try_ffmpeg_pipe("rtsp://u:p@192.0.2.1:554/s")
    elapsed = time.time() - t0
    assert ok is False
    assert elapsed < 35.0, f"ffmpeg pipe hung too long: {elapsed:.1f}s"
    # Hindi detail present
    assert any(w in detail for w in ("nahi", "check")), detail
    print(f"PASS: _try_ffmpeg_pipe unreachable in {elapsed:.1f}s -> {detail!r}")


# ---- probe_rtsp source: FFmpeg engine step present ---------------------
def test_probe_rtsp_source_has_ffmpeg_step():
    src = inspect.getsource(probe_rtsp)
    # tcp before udp before ffmpeg engine
    i_tcp = src.find("Video stream (TCP)")
    i_udp = src.find("Video stream (UDP)")
    i_ff = src.find("Video stream (FFmpeg engine)")
    assert 0 < i_tcp < i_udp < i_ff, (i_tcp, i_udp, i_ff)
    print("PASS: probe_rtsp: TCP -> UDP -> FFmpeg engine order in source")


def test_probe_rtsp_unreachable_still_early_returns():
    # TEST-NET-1: network step must fail and early-return, skipping ffmpeg step
    ok, steps = probe_rtsp("rtsp://u:p@192.0.2.1:554/s", wait=1.0)
    assert ok is False
    step_names = [s[0] for s in steps]
    assert not any("FFmpeg engine" in n for n in step_names), step_names
    print("PASS: unreachable host early-returns before FFmpeg engine step")


# ---- main.py inspection --------------------------------------------------
def test_main_py_ffmpeg_fallback_wiring():
    path = "/app/9x_security/main.py"
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    # Import & fallback usage
    assert "from engine import FFmpegPipeSource" in src
    # tries after tcp/udp loop
    assert "FFmpegPipeSource(source)" in src
    # first-frame check
    assert "ok, _first = src.read()" in src
    # honors self._running
    assert "if ok and self._running" in src
    # default backend fallback still present
    assert "return cv2.VideoCapture(source)" in src
    # py_compile passes (PyQt5 stubbed absent is fine; only syntax check)
    py_compile.compile(path, doraise=True)
    print("PASS: main.py has FFmpegPipeSource fallback wired + compiles")


# ---- requirements + workflow --------------------------------------------
def test_requirements_has_imageio_ffmpeg():
    with open("/app/9x_security/requirements.txt", "r", encoding="utf-8") as f:
        req = f.read()
    assert "imageio-ffmpeg==0.6.0" in req
    print("PASS: requirements.txt pins imageio-ffmpeg==0.6.0")


def test_workflow_collect_binaries_and_yaml_valid():
    path = "/app/.github/workflows/build-windows.yml"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "--collect-binaries imageio_ffmpeg" in content
    try:
        import yaml
        data = yaml.safe_load(content)
        assert "jobs" in data
    except ImportError:
        pytest.skip("pyyaml not installed")
    print("PASS: workflow YAML valid & has --collect-binaries imageio_ffmpeg")


def test_readme_pyinstaller_collect_binaries():
    # README optional; check if present + pyinstaller line has flag
    candidates = ["/app/9x_security/README.md", "/app/README.md"]
    found = False
    for p in candidates:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                txt = f.read()
            if "pyinstaller" in txt.lower():
                found = True
                assert "--collect-binaries imageio_ffmpeg" in txt, \
                    f"README {p} pyinstaller cmd missing --collect-binaries imageio_ffmpeg"
    if not found:
        pytest.skip("no README with pyinstaller command found")
    print("PASS: README pyinstaller command includes --collect-binaries imageio_ffmpeg")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
