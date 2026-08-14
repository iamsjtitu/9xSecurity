"""9x Security - Self-updater via GitHub Releases (folder-zip builds).

New builds ship as a .zip containing the app folder (9xSecurity/ with the exe
and _internal libs). The updater downloads the zip, extracts it, swaps the
install folder via a batch script and restarts. Legacy single .exe assets are
still supported. Only self-updates when running as a PyInstaller build (frozen).
"""
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

APP_VERSION = "1.0.0"


def _api(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "9xSecurity", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=25, context=ssl.create_default_context()) as r:
        return json.loads(r.read().decode())


def _pick_asset(assets):
    """Prefer the Setup installer .exe; fall back to .zip, then any .exe."""
    setup = zip_url = exe_url = None
    for a in assets or []:
        name = str(a.get("name", "")).lower()
        url = a.get("browser_download_url")
        if name.endswith(".exe") and "setup" in name and setup is None:
            setup = url
        elif name.endswith(".zip") and zip_url is None:
            zip_url = url
        elif name.endswith(".exe") and exe_url is None:
            exe_url = url
    return setup or zip_url or exe_url


def check_latest(repo):
    """repo = 'owner/name'. Returns (version, asset_url, release_page_url)."""
    data = _api(f"https://api.github.com/repos/{repo}/releases/latest")
    tag = (data.get("tag_name") or "").lstrip("vV")
    return tag, _pick_asset(data.get("assets")), data.get("html_url", "")


def _ver(v):
    nums = re.findall(r"\d+", v or "")
    return tuple(int(x) for x in nums) if nums else (0,)


def is_newer(latest, current=APP_VERSION):
    return _ver(latest) > _ver(current)


def download(asset_url, dest, progress=None):
    req = urllib.request.Request(asset_url, headers={"User-Agent": "9xSecurity"})
    with urllib.request.urlopen(req, timeout=120, context=ssl.create_default_context()) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length", 0))
        read = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            read += len(chunk)
            if progress and total:
                progress(read, total)
    return dest


def _install_dir():
    return os.path.dirname(os.path.abspath(sys.executable))


def _zip_app_root(extract_dir):
    """Folder inside the extracted zip that contains 9xSecurity.exe."""
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
    """Silent Inno Setup upgrade-in-place; relaunches the app after install."""
    subprocess.Popen(
        [setup_exe, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/FORCECLOSEAPPLICATIONS"]
    )
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
    # DETACHED_PROCESS so it survives after we exit.
    subprocess.Popen(["cmd", "/c", bat], creationflags=0x00000008)
    return True
