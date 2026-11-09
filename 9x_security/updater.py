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
import time
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
    return (_asset_urls(assets, use_api_url) or [None])[0]


def _asset_urls(assets, use_api_url=False):
    """Download candidates for the chosen asset, best first: [primary, alternate]. The alternate is the
    other GitHub endpoint for the SAME file (api.github.com asset url <-> github.com browser url); both
    redirect to the release CDN, so a 504 on one path is retried on the other."""
    keys = ("url", "browser_download_url") if use_api_url else ("browser_download_url", "url")
    setup = zip_a = exe_a = None
    for a in assets or []:
        name = str(a.get("name", "")).lower()
        if name.endswith(".exe") and "setup" in name and setup is None:
            setup = a
        elif name.endswith(".zip") and zip_a is None:
            zip_a = a
        elif name.endswith(".exe") and exe_a is None:
            exe_a = a
    a = setup or zip_a or exe_a
    if not a:
        return []
    if use_api_url:  # private repo: the browser url needs a logged-in session, it cannot be the alternate
        keys = ("url",)
    return [u for u in (a.get(k) for k in keys) if u]


def release_assets(repo, token=None):
    """Like check_latest, but returns every download candidate: (version, [urls], release_page_url)."""
    code, data = _api(f"https://api.github.com/repos/{repo}/releases/latest", token)
    if code == 404:
        return "", [], ""
    if code in (401, 403):
        raise RuntimeError(
            f"GitHub ne access mana kar diya (HTTP {code}). "
            "Agar repo PRIVATE hai to Settings > Updates me GitHub token daalein, "
            "ya thodi der baad try karein (rate limit)."
        )
    if code != 200:
        raise RuntimeError(f"GitHub API error (HTTP {code})")
    tag = (data.get("tag_name") or "").lstrip("vV")
    return tag, _asset_urls(data.get("assets"), use_api_url=bool(token)), data.get("html_url", "")


def check_latest(repo, token=None):
    """repo = 'owner/name'. Returns (version, asset_url, release_page_url).
    Empty version = no release visible (none published yet, or PRIVATE repo
    without a token)."""
    tag, urls, page = release_assets(repo, token)
    return tag, (urls[0] if urls else None), page


def _ver(v):
    nums = re.findall(r"\d+", v or "")
    return tuple(int(x) for x in nums) if nums else (0,)


def is_newer(latest, current=None):
    return _ver(latest) > _ver(APP_VERSION if current is None else current)


RETRY_STATUS = (408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524)
RETRY_WAIT = (3, 6, 12, 20, 30)  # seconds between attempts (6 attempts total)


class _Transient(Exception):
    pass


def _content_total(r, offset):
    """Total file size from a 206 Content-Range ('bytes a-b/total') or a 200 Content-Length."""
    cr = r.headers.get("Content-Range", "")
    if r.status_code == 206 and "/" in cr:
        try:
            return int(cr.rsplit("/", 1)[1])
        except ValueError:
            pass
    return int(r.headers.get("Content-Length", 0) or 0) + (offset if r.status_code == 206 else 0)


def download(asset_url, dest, progress=None, token=None, should_stop=None, status=None, attempts=None):
    """Streams the asset to `dest`. asset_url may be one url or a list of candidates (same file, different
    GitHub endpoints). GitHub's download front door sometimes answers 504/502 for a minute: every transient
    error (5xx, timeout, connection drop, short read) is retried on the next candidate with a growing pause,
    RESUMING from the bytes already written (Range header) so a 1 GB installer never restarts from zero.
    progress(read, total) per chunk (total may be 0 if unknown); status(text) on each retry.
    Returns dest, or None if should_stop() became true."""
    urls = [asset_url] if isinstance(asset_url, str) else [u for u in (asset_url or []) if u]
    if not urls:
        raise RuntimeError("Download link nahi mila.")
    attempts = attempts or len(RETRY_WAIT) + 1
    base = {"User-Agent": "9xSecurity", "Accept": "application/octet-stream"}
    if token:
        base["Authorization"] = f"Bearer {token}"
    # requests drops the Authorization header on the cross-host S3 redirect,
    # which is exactly what GitHub asset downloads require.
    read, total, last_err = 0, 0, ""
    with open(dest, "wb"):
        pass
    for i in range(attempts):
        if should_stop and should_stop():
            return None
        url = urls[i % len(urls)]
        h = dict(base)
        if read:
            h["Range"] = f"bytes={read}-"
        try:
            with requests.get(url, headers=h, timeout=(20, 90), stream=True) as r:
                if r.status_code in RETRY_STATUS:
                    raise _Transient(f"HTTP {r.status_code}")
                r.raise_for_status()
                if read and r.status_code != 206:  # server ignored Range: start over
                    read = 0
                total = _content_total(r, read) or total
                with open(dest, "ab" if read else "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        if should_stop and should_stop():
                            return None
                        f.write(chunk)
                        read += len(chunk)
                        if progress:
                            progress(read, total)
            if total and read < total:
                raise _Transient(f"download adhura ({read}/{total} bytes)")
            return dest
        except (_Transient, requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as e:
            last_err = str(e).split(" for url")[0] or e.__class__.__name__
            if i == attempts - 1:
                break
            wait = RETRY_WAIT[min(i, len(RETRY_WAIT) - 1)]
            if status:
                status(f"GitHub se download me rukawat ({last_err}) — {wait} sec baad dobara koshish "
                       f"({i + 2}/{attempts}){', jahan ruka wahin se' if read else ''}...")
            end = time.time() + wait
            while time.time() < end:
                if should_stop and should_stop():
                    return None
                time.sleep(0.25)
    raise RuntimeError(
        f"{attempts} baar try karne par bhi GitHub se download nahi hua ({last_err}). Internet check karke thodi der "
        "baad dobara 'Download & Install' dabayein — ya neeche 'Browser me download' se Setup lekar chala dein "
        "(purani version ke upar install ho jayegi, settings waise hi rahengi)."
    )


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
