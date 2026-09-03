"""Inbuilt (CI-baked) GitHub update token: precedence + service wiring + CI sed anchor."""
import os
import re
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import updater  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def test_effective_token_precedence(monkeypatch):
    monkeypatch.setattr(updater, "DEFAULT_TOKEN", "")
    assert updater.effective_token("") is None
    assert updater.effective_token(None) is None
    assert updater.effective_token(" user ") == "user"
    monkeypatch.setattr(updater, "DEFAULT_TOKEN", "baked")
    assert updater.effective_token("") == "baked"
    assert updater.effective_token("user") == "user"  # Settings override wins


def test_sed_anchor_matches_ci_pattern():
    src = open(os.path.join(HERE, "updater.py"), encoding="utf-8").read()
    assert re.search(r'^DEFAULT_TOKEN = ""', src, re.M)
    wf = open(os.path.join(HERE, "..", ".github", "workflows", "build-windows.yml"), encoding="utf-8").read()
    assert "secrets.UPDATE_TOKEN" in wf
    assert 's|^DEFAULT_TOKEN = .*|DEFAULT_TOKEN = \\"$UPDATE_TOKEN\\"  # baked|' in wf
    tmp = "/tmp/_u_bake.py"
    open(tmp, "w").write(src)
    subprocess.run(
        ["bash", "-c", 'sed -i "s|^DEFAULT_TOKEN = .*|DEFAULT_TOKEN = \\"$UPDATE_TOKEN\\"  # baked|" ' + tmp],
        env={**os.environ, "UPDATE_TOKEN": "github_pat_ABC_123"}, check=True,
    )
    ns = {}
    exec(compile(open(tmp).read(), tmp, "exec"), ns)
    assert ns["DEFAULT_TOKEN"] == "github_pat_ABC_123"
    assert ns["effective_token"]("") == "github_pat_ABC_123"


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    import service

    tok = "t" * 48
    service._tokens[tok] = time.time()
    c = TestClient(service.app)
    c.headers["X-Auth-Token"] = tok
    return c


def test_service_uses_baked_token_and_exposes_flag(client, monkeypatch):
    import service

    seen = {}

    def fake_check(repo, token=None):
        seen["repo"], seen["token"] = repo, token
        return "1.0.0", None, ""

    monkeypatch.setattr(updater, "DEFAULT_REPO", "owner/repo")
    monkeypatch.setattr(updater, "DEFAULT_TOKEN", "baked_tok")
    monkeypatch.setattr(updater, "check_latest", fake_check)
    monkeypatch.setattr(service, "_cfg", lambda: {**service.config.DEFAULTS, "gh_token": ""})

    r = client.get("/api/update/check")
    assert r.status_code == 200 and seen["token"] == "baked_tok"
    assert client.get("/api/settings").json()["gh_token_builtin"] is True

    # user-entered token overrides inbuilt one
    monkeypatch.setattr(service, "_cfg", lambda: {**service.config.DEFAULTS, "gh_token": "mine"})
    client.get("/api/update/check")
    assert seen["token"] == "mine"

    # no baked token -> flag false, message tells how to set the secret
    monkeypatch.setattr(updater, "DEFAULT_TOKEN", "")
    monkeypatch.setattr(updater, "check_latest", lambda repo, token=None: ("", None, ""))
    assert client.get("/api/settings").json()["gh_token_builtin"] is False
    msg = client.get("/api/update/check").json()["message"]
    assert "UPDATE_TOKEN" in msg
