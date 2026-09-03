"""Sidebar 'New version' badge: background update check exposed via /api/state."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import updater  # noqa: E402


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    import service

    tok = "u" * 48
    service._tokens[tok] = time.time()
    c = TestClient(service.app)
    c.headers["X-Auth-Token"] = tok
    return c


def test_state_badge_fields(client, monkeypatch):
    import service

    monkeypatch.setattr(updater, "APP_VERSION", "1.0.5")
    monkeypatch.setattr(updater, "DEFAULT_REPO", "owner/repo")
    monkeypatch.setattr(updater, "check_latest", lambda repo, token=None: ("1.0.9", "http://a", "http://p"))
    service._refresh_update_info()
    st = client.get("/api/state").json()
    assert st["update_available"] is True and st["update_latest"] == "1.0.9"

    # already latest -> badge off (manual check also refreshes cache)
    monkeypatch.setattr(updater, "check_latest", lambda repo, token=None: ("1.0.5", "http://a", "http://p"))
    r = client.get("/api/update/check").json()
    assert r["available"] is False
    assert client.get("/api/state").json()["update_available"] is False

    # no release / network error -> no crash, badge stays off
    monkeypatch.setattr(updater, "check_latest", lambda repo, token=None: ("", None, ""))
    service._refresh_update_info()
    assert client.get("/api/state").json()["update_available"] is False

    def boom(repo, token=None):
        raise RuntimeError("offline")

    monkeypatch.setattr(updater, "check_latest", boom)
    service._refresh_update_info()
    assert client.get("/api/state").json()["update_available"] is False


def test_dev_mode_no_repo_skips(monkeypatch):
    import service

    monkeypatch.setattr(updater, "DEFAULT_REPO", "")
    monkeypatch.setattr(service, "_cfg", lambda: {**service.config.DEFAULTS, "github_repo": ""})
    called = []
    monkeypatch.setattr(updater, "check_latest", lambda *a, **k: called.append(1))
    service._refresh_update_info()
    assert not called
