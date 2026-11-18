import pytest


@pytest.fixture(autouse=True)
def _camera_host_reachable(request, monkeypatch):
    """Worker tests use fake rtsp://cam/... URLs with a mocked open_stream; the real TCP pre-check would
    report them unreachable. Keep the real check only in the test file that covers it."""
    if request.node.fspath.basename == "test_iter47_update_handoff.py":
        return
    try:
        import service
    except Exception:
        return
    monkeypatch.setattr(service, "rtsp_port_reachable", lambda source, timeout=2.5: (True, ""))
