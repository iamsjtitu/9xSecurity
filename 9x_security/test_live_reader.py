"""Live-stream smoothness: LatestFrameReader keeps the decoder running while the AI
is busy (fixes HEVC 'Could not find ref with POC' / blurry frames every 1-2 s)."""
import os
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import engine  # noqa: E402
from engine import FFMPEG_OPTS, LatestFrameReader, codec_name  # noqa: E402


class FakeCam:
    """25 fps camera. If read() is not called ~continuously, a real decoder drops packets;
    here we just count how many reads happened (decoder kept busy) and number the frames."""

    def __init__(self, fps=25, frames=1000):
        self.i = 0
        self.dt = 1.0 / fps
        self.frames = frames
        self.reads = 0
        self.released = False

    def read(self):
        if self.i >= self.frames:
            return False, None
        time.sleep(self.dt)
        self.i += 1
        self.reads += 1
        f = np.full((4, 4, 3), self.i % 256, dtype=np.uint8)
        return True, f

    def isOpened(self):
        return not self.released

    def get(self, prop):
        return 0

    def release(self):
        self.released = True


def test_slow_consumer_gets_newest_frame_and_decoder_keeps_running():
    cam = FakeCam(fps=50)
    rd = LatestFrameReader(cam)
    try:
        seq = []
        for _ in range(6):
            time.sleep(0.2)          # "AI busy" 200 ms per iteration
            ok, f = rd.read()
            assert ok
            seq.append(int(f[0, 0, 0]))
        # frames handed out are recent (jumping ~10 frames each time), not a stale FIFO
        assert seq == sorted(seq) and seq[-1] - seq[0] >= 30
        assert rd.dropped > 20            # decoder consumed frames the AI never saw
        assert cam.reads >= 50            # decoder never stalled
    finally:
        rd.release()
    assert cam.released and not rd.isOpened()


def test_read_times_out_when_source_dies():
    cam = FakeCam(fps=100, frames=3)
    rd = LatestFrameReader(cam)
    try:
        time.sleep(0.1)
        ok, _ = rd.read(timeout=0.3)
        assert ok
        t0 = time.time()
        ok, f = rd.read(timeout=0.3)
        assert not ok and f is None and 0.25 <= time.time() - t0 < 1.0
    finally:
        rd.release()


def test_fast_consumer_never_misses_when_keeping_up():
    cam = FakeCam(fps=20, frames=10)
    rd = LatestFrameReader(cam)
    got = []
    try:
        for _ in range(10):
            ok, f = rd.read(timeout=1.0)
            if not ok:
                break
            got.append(int(f[0, 0, 0]))
    finally:
        rd.release()
    assert got == list(range(1, 11)) and rd.dropped == 0


def test_codec_name_and_ffmpeg_opts():
    class C:
        def get(self, p):
            return float(int.from_bytes(b"hevc", "little"))

    assert codec_name(C()) == "hevc"

    class Z:
        def get(self, p):
            return 0.0

    assert codec_name(Z()) == ""
    assert "fflags;discardcorrupt" in FFMPEG_OPTS
    src = open(os.path.join(os.path.dirname(__file__), "engine.py"), encoding="utf-8").read()
    assert '"-fflags", "+discardcorrupt"' in src


def test_worker_wraps_only_live_sources(monkeypatch):
    import service

    w = service.Worker()
    cam = FakeCam(fps=100, frames=5)
    monkeypatch.setattr(service, "open_stream", lambda s, r: cam)
    cap = w._open("rtsp://x/y", live=True)
    assert isinstance(cap, LatestFrameReader)
    cap.release()
    cam2 = FakeCam(fps=100, frames=5)
    monkeypatch.setattr(service, "open_stream", lambda s, r: cam2)
    cap2 = w._open("/tmp/file.mp4", live=False)
    assert cap2 is cam2  # files: every frame processed, nothing skipped
