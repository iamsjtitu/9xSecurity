import pytest


@pytest.fixture(autouse=True)
def _camera_host_reachable(request, monkeypatch):
    """Worker tests use fake rtsp://cam/... URLs with a mocked open_stream; the real TCP pre-check would report
    them unreachable and the fingerprint thread would hit the network and WRITE the real config.json
    (it once wiped the dev admin hash). Keep the real behaviour only in the files that cover it."""
    if request.node.fspath.basename in ("test_iter47_update_handoff.py", "test_iter48_camera_autofind.py"):
        return
    try:
        import service
    except Exception:
        return
    monkeypatch.setattr(service, "rtsp_port_reachable", lambda source, timeout=2.5: (True, ""))
    monkeypatch.setattr(service.Worker, "_maybe_fingerprint", lambda self, source, live: None)
