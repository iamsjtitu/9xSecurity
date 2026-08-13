"""9x Security - Self-updater via GitHub Releases.

Checks the latest release of a configured GitHub repo, and if a newer version
is available, downloads its .exe asset and replaces the running executable.
Only self-replaces when running as a PyInstaller-built .exe (frozen).
"""
import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import urllib.request

APP_VERSION = "1.0.0"


def _api(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "9xSecurity", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=25, context=ssl.create_default_context()) as r:
        return json.loads(r.read().decode())


def check_latest(repo):
    """repo = 'owner/name'. Returns (version, exe_asset_url, release_page_url)."""
    data = _api(f"https://api.github.com/repos/{repo}/releases/latest")
    tag = (data.get("tag_name") or "").lstrip("vV")
    asset = None
    for a in data.get("assets", []):
        if str(a.get("name", "")).lower().endswith(".exe"):
            asset = a.get("browser_download_url")
            break
    return tag, asset, data.get("html_url", "")


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


def apply_and_restart(new_exe):
    """Replace the running frozen exe with new_exe and relaunch (Windows only).
    Returns True if an update batch was launched, False if running from source."""
    if not getattr(sys, "frozen", False):
        return False
    cur = sys.executable
    bat = os.path.join(tempfile.gettempdir(), "9x_update.bat")
    script = (
        "@echo off\r\n"
        "timeout /t 2 /nobreak >nul\r\n"
        f'move /y "{new_exe}" "{cur}"\r\n'
        f'start "" "{cur}"\r\n'
        'del "%~f0"\r\n'
    )
    with open(bat, "w") as f:
        f.write(script)
    # DETACHED_PROCESS so it survives after we exit.
    subprocess.Popen(["cmd", "/c", bat], creationflags=0x00000008)
    return True
