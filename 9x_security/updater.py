"""9x Security - Self-updater via GitHub Releases.

Builds ship as an Inno Setup installer attached to a GitHub Release
(tag v{APP_VERSION}). check_latest() finds the newest release; apply_update()
runs the installer silently and relaunches. Supports PRIVATE repos via an
optional GitHub token (Settings -> Updates). Only self-updates when running
as a PyInstaller build (frozen).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

import requests

APP_VERSION = "1.0.0"
DEFAULT_REPO = ""  # baked in at CI build time (owner/name)
DEFAULT_TOKEN = ""  # baked in at CI build time from repo secret UPDATE_TOKEN (private repo)


def effective_token(cfg_token=None):
    """User-entered token (Settings) overrides the build-time inbuilt token."""
    return (cfg_token or "").strip() or DEFAULT_TOKEN.strip() or None


def _api(url, token=None):
    h = {"User-Agent": "9xSecurity", "Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    r = requests.get(url, headers=h, timeout=25)
    return r.status_code, (r.json() if r.content else {})


def _pick_asset(assets, use_api_url=False):
    """Prefer the Setup installer .exe; fall back to .zip, then any .exe.
    For private repos (token flow) the API asset url must be used."""
    key = "url" if use_api_url else "browser_download_url"
    setup = zip_url = exe_url = None
    for a in assets or []:
        name = str(a.get("name", "")).lower()
        url = a.get(key)
        if name.endswith(".exe") and "setup" in name and setup is None:
            setup = url
        elif name.endswith(".zip") and zip_url is None:
            zip_url = url
        elif name.endswith(".exe") and exe_url is None:
            exe_url = url
    return setup or zip_url or exe_url


def check_latest(repo, token=None):
    """repo = 'owner/name'. Returns (version, asset_url, release_page_url).
    Empty version = no release visible (none published yet, or PRIVATE repo
    without a token)."""
    code, data = _api(f"https://api.github.com/repos/{repo}/releases/latest", token)
    if code == 404:
        return "", None, ""
    if code in (401, 403):
        raise RuntimeError(
            f"GitHub ne access mana kar diya (HTTP {code}). "
            "Agar repo PRIVATE hai to Settings > Updates me GitHub token daalein, "
            "ya thodi der baad try karein (rate limit)."
        )
    if code != 200:
        raise RuntimeError(f"GitHub API error (HTTP {code})")
    tag = (data.get("tag_name") or "").lstrip("vV")
    asset = _pick_asset(data.get("assets"), use_api_url=bool(token))
    return tag, asset, data.get("html_url", "")


def _ver(v):
    nums = re.findall(r"\d+", v or "")
    return tuple(int(x) for x in nums) if nums else (0,)


def is_newer(latest, current=None):
    return _ver(latest) > _ver(APP_VERSION if current is None else current)


def download(asset_url, dest, progress=None, token=None, should_stop=None):
    """Streams the asset to `dest`. progress(read, total) is called per chunk
    (total may be 0 if unknown). Returns dest, or None if should_stop() became true."""
    h = {"User-Agent": "9xSecurity", "Accept": "application/octet-stream"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    # requests drops the Authorization header on the cross-host S3 redirect,
    # which is exactly what GitHub asset downloads require.
    with requests.get(asset_url, headers=h, timeout=120, stream=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        read = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                if should_stop and should_stop():
                    return None
                f.write(chunk)
                read += len(chunk)
                if progress:
                    progress(read, total)
    return dest


def _install_dir():
    return os.path.dirname(os.path.abspath(sys.executable))


def _zip_app_root(extract_dir):
    for root, _dirs, files in os.walk(extract_dir):
        if "9xSecurity.exe" in files:
            return root
    return None


def apply_update(path):
    """path = downloaded Setup .exe (installer) or .zip (older folder build).
    Installs the new version and relaunches (Windows only).
    Returns True if an update was launched, False if running from source."""
    if not getattr(sys, "frozen", False):
        return False
    if str(path).lower().endswith(".zip"):
        return _apply_zip(path)
    return _run_installer(path)


def _run_installer(setup_exe):
    # electron-builder NSIS installer: /S = silent, --force-run = relaunch app after
    subprocess.Popen([setup_exe, "/S", "--force-run"])
    return True


def _apply_zip(zip_path):
    extract_dir = os.path.join(tempfile.gettempdir(), "9x_update_new")
    shutil.rmtree(extract_dir, ignore_errors=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(extract_dir)
    src = _zip_app_root(extract_dir)
    if not src:
        return False
    dst = _install_dir()
    exe = os.path.join(dst, os.path.basename(sys.executable))
    bat = os.path.join(tempfile.gettempdir(), "9x_update.bat")
    script = (
        "@echo off\r\n"
        "timeout /t 2 /nobreak >nul\r\n"
        f'xcopy /E /Y /I "{src}" "{dst}" >nul\r\n'
        f'start "" "{exe}"\r\n'
        f'rd /s /q "{extract_dir}" >nul 2>&1\r\n'
        f'del "{zip_path}" >nul 2>&1\r\n'
        'del "%~f0"\r\n'
    )
    with open(bat, "w") as f:
        f.write(script)
    subprocess.Popen(["cmd", "/c", bat], creationflags=0x00000008)
    return True
