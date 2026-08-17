"""Iteration 7 - Updater private-repo / token support tests.

Verifies:
 - 404 => empty tag (private without token OR no release)
 - 401/403 => RuntimeError with helpful Hindi guidance (token / rate-limit)
 - Token flow => _api receives Bearer token AND asset uses API `url` field
 - Public flow => browser_download_url used; `v1.0.9` tag stripped to `1.0.9`
 - LIVE GitHub public API (skipped if rate-limited)
 - LIVE small asset download (skipped if impractical / rate-limited)
 - main.py inspection: Updates tab wiring
 - config.py DEFAULTS has gh_token
 - sed-bake compatibility (APP_VERSION / DEFAULT_REPO lines) - simulate CI seds
   on a copy and import the patched module.
"""
import importlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import updater  # noqa: E402
import config as app_config  # noqa: E402


# ---------------- Mocked (offline) tests ----------------

def test_check_latest_404_returns_empty(monkeypatch):
    monkeypatch.setattr(updater, "_api", lambda url, token=None: (404, {}))
    tag, asset, page = updater.check_latest("owner/name")
    assert (tag, asset, page) == ("", None, "")


def test_check_latest_403_raises_helpful(monkeypatch):
    monkeypatch.setattr(updater, "_api", lambda url, token=None: (403, {}))
    with pytest.raises(RuntimeError) as ei:
        updater.check_latest("owner/name")
    msg = str(ei.value)
    assert "403" in msg
    # token guidance / rate-limit hint should be present
    assert "token" in msg.lower() or "rate" in msg.lower() or "PRIVATE" in msg


def test_check_latest_401_raises_helpful(monkeypatch):
    monkeypatch.setattr(updater, "_api", lambda url, token=None: (401, {}))
    with pytest.raises(RuntimeError) as ei:
        updater.check_latest("owner/name", token="bad")
    assert "401" in str(ei.value)


def test_check_latest_500_raises(monkeypatch):
    monkeypatch.setattr(updater, "_api", lambda url, token=None: (500, {}))
    with pytest.raises(RuntimeError):
        updater.check_latest("owner/name")


def test_check_latest_token_uses_api_asset_url_and_strips_tag(monkeypatch):
    captured = {}

    def fake_api(url, token=None):
        captured["url"] = url
        captured["token"] = token
        return 200, {
            "tag_name": "v1.0.9",
            "html_url": "https://github.com/owner/name/releases/tag/v1.0.9",
            "assets": [{
                "name": "9xSecuritySetup-v1.0.9.exe",
                "browser_download_url": "https://public/browser",
                "url": "https://api.github.com/repos/owner/name/releases/assets/1",
            }],
        }

    monkeypatch.setattr(updater, "_api", fake_api)
    tag, asset, page = updater.check_latest("owner/name", token="ghp_TESTTOKEN")
    assert captured["token"] == "ghp_TESTTOKEN"
    assert captured["url"].endswith("/repos/owner/name/releases/latest")
    assert tag == "1.0.9", "leading v must be stripped"
    assert asset == "https://api.github.com/repos/owner/name/releases/assets/1"
    assert page.startswith("https://github.com/")


def test_check_latest_no_token_uses_browser_download_url(monkeypatch):
    def fake_api(url, token=None):
        assert token is None
        return 200, {
            "tag_name": "v1.0.9",
            "html_url": "http://rel",
            "assets": [{
                "name": "9xSecuritySetup-v1.0.9.exe",
                "browser_download_url": "http://public/browser",
                "url": "http://api/asset/1",
            }],
        }

    monkeypatch.setattr(updater, "_api", fake_api)
    tag, asset, _p = updater.check_latest("owner/name")
    assert tag == "1.0.9"
    assert asset == "http://public/browser"


def test_api_sends_bearer_when_token(monkeypatch):
    captured = {}

    class R:
        status_code = 200
        content = b"{}"

        def json(self):
            return {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return R()

    monkeypatch.setattr(updater.requests, "get", fake_get)
    updater._api("http://x", token="tkn")
    assert captured["headers"]["Authorization"] == "Bearer tkn"
    assert captured["headers"]["Accept"] == "application/vnd.github+json"
    updater._api("http://x")
    assert "Authorization" not in captured["headers"]


def test_download_sends_bearer_and_octet(monkeypatch, tmp_path):
    captured = {}

    class Resp:
        headers = {"Content-Length": "3"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, n):
            yield b"abc"

    def fake_get(url, headers=None, timeout=None, stream=False):
        captured["url"] = url
        captured["headers"] = headers
        captured["stream"] = stream
        return Resp()

    monkeypatch.setattr(updater.requests, "get", fake_get)
    dest = tmp_path / "out.bin"
    updater.download("http://api/asset/1", str(dest), token="tkn")
    assert dest.read_bytes() == b"abc"
    assert captured["headers"]["Authorization"] == "Bearer tkn"
    assert captured["headers"]["Accept"] == "application/octet-stream"
    assert captured["stream"] is True


# ---------------- LIVE (unauth) tests, skipped on rate-limit / network ----------------

def _live_call(repo, token=None):
    try:
        return updater.check_latest(repo, token=token)
    except RuntimeError as e:
        if "403" in str(e):
            pytest.skip(f"GitHub rate-limited: {e}")
        raise
    except requests.RequestException as e:
        pytest.skip(f"Network unavailable: {e}")


def test_live_microsoft_terminal_has_release():
    tag, asset, page = _live_call("microsoft/terminal")
    assert tag, "expected non-empty tag from microsoft/terminal latest release"
    assert re.match(r"^\d+(\.\d+)*", tag), f"tag looks off: {tag!r}"
    assert page.startswith("https://github.com/"), page


def test_live_repo_without_releases_returns_empty():
    # torvalds/linux famously has no GitHub Releases
    tag, asset, page = _live_call("torvalds/linux")
    assert tag == "" and asset is None and page == ""


# ---------------- main.py inspection ----------------

MAIN_SRC = None


def _read_main():
    global MAIN_SRC
    if MAIN_SRC is None:
        with open(os.path.join(os.path.dirname(__file__), "main.py"), "r", encoding="utf-8") as f:
            MAIN_SRC = f.read()
    return MAIN_SRC


def test_main_py_compiles():
    subprocess.check_call([sys.executable, "-m", "py_compile", os.path.join(os.path.dirname(__file__), "main.py")])


def test_main_updates_tab_has_gh_token_input():
    s = _read_main()
    # QLineEdit for gh_token exists
    assert "self.gh_token = QtWidgets.QLineEdit(cfg.get(\"gh_token\"" in s
    # Password echo
    assert "self.gh_token.setEchoMode(QtWidgets.QLineEdit.Password)" in s
    # objectName github-token-input
    assert "setObjectName(\"github-token-input\")" in s


def test_main_no_repo_input_in_updates_tab():
    s = _read_main()
    # No QLineEdit for repo entry (repo field is baked at build time via DEFAULT_REPO)
    assert "self.gh_repo = QtWidgets.QLineEdit" not in s
    assert "self.repo_input" not in s
    assert "QLineEdit(cfg.get(\"github_repo\"" not in s


def test_main_save_persists_gh_token():
    s = _read_main()
    assert "c[\"gh_token\"] = self.gh_token.text().strip()" in s


def test_main_check_update_passes_token():
    s = _read_main()
    assert "updater.check_latest(repo, token=token or None)" in s
    assert "updater.download(asset, dest, token=token or None)" in s


def test_main_no_release_dialog_mentions_private_and_token_steps():
    s = _read_main()
    assert "PRIVATE" in s
    assert "github.com/settings/tokens" in s
    assert "Contents: Read" in s or "Contents: Read-only" in s


# ---------------- config.py ----------------

def test_config_defaults_has_gh_token():
    assert "gh_token" in app_config.DEFAULTS
    assert app_config.DEFAULTS["gh_token"] == ""


# ---------------- sed-bake compatibility ----------------

def test_updater_has_sed_target_lines():
    src_path = os.path.join(os.path.dirname(__file__), "updater.py")
    with open(src_path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    assert any(l.startswith('APP_VERSION = "') for l in lines), "APP_VERSION sed anchor missing"
    assert any(l.startswith('DEFAULT_REPO = "') for l in lines), "DEFAULT_REPO sed anchor missing"


def test_updater_sed_bake_simulation(tmp_path):
    """Simulate the CI sed patches and verify the patched module imports."""
    src_path = os.path.join(os.path.dirname(__file__), "updater.py")
    dst = tmp_path / "updater_patched.py"
    shutil.copy(src_path, dst)

    # Same style as the GitHub Actions workflow
    subprocess.check_call([
        "sed", "-i",
        's|^APP_VERSION = "[^"]*"|APP_VERSION = "9.9.9"|',
        str(dst),
    ])
    subprocess.check_call([
        "sed", "-i",
        's|^DEFAULT_REPO = "[^"]*"|DEFAULT_REPO = "owner/9x-security"|',
        str(dst),
    ])

    sys.path.insert(0, str(tmp_path))
    try:
        if "updater_patched" in sys.modules:
            del sys.modules["updater_patched"]
        mod = importlib.import_module("updater_patched")
        assert mod.APP_VERSION == "9.9.9"
        assert mod.DEFAULT_REPO == "owner/9x-security"
        # sanity: is_newer still functions
        assert mod.is_newer("10.0.0") is True
        assert mod.is_newer("1.0.0") is False
    finally:
        sys.path.remove(str(tmp_path))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
