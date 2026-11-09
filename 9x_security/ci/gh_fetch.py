"""CI helper: download GitHub release assets even when github.com's /releases/download front door
answers 504 Gateway Time-out (happens for minutes at a time). Retries with backoff and, from the second
attempt on, alternates with the api.github.com asset endpoint for the SAME file (GITHUB_TOKEN raises the
API rate limit; requests drops the Authorization header on the CDN redirect, as GitHub requires).

Usage:  python ci/gh_fetch.py <url> <dest> [--min-bytes N]
        python ci/gh_fetch.py --easyocr <models_dir>      (craft + english_g2, md5-verified, then a Reader load)
"""
import hashlib
import os
import re
import shutil
import sys
import time
import zipfile

import requests

UA = {"User-Agent": "9xSecurity-CI"}
WAITS = (5, 10, 20, 40, 60)


def _token():
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None


def api_asset_url(url):
    """github.com/<o>/<r>/releases/download/<tag>/<name> -> api.github.com/.../releases/assets/<id> (or None)."""
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/releases/download/([^/]+)/([^/?]+)", url)
    if not m:
        return None
    owner, repo, tag, name = m.groups()
    h = dict(UA, Accept="application/vnd.github+json")
    if _token():
        h["Authorization"] = f"Bearer {_token()}"
    r = requests.get(f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}", headers=h, timeout=30)
    r.raise_for_status()
    for a in r.json().get("assets", []):
        if a.get("name") == name:
            return a.get("url")
    return None


def fetch(url, dest, min_bytes=1, attempts=len(WAITS) + 1):
    """Download url (or its api.github.com twin) to dest atomically. Exits non-zero after all attempts."""
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    candidates, last = [url], None
    for i in range(attempts):
        if i >= 1 and len(candidates) == 1:
            try:
                alt = api_asset_url(url)
                if alt:
                    candidates.append(alt)
                    print(f"  alternate endpoint: {alt}")
            except Exception as e:  # noqa: BLE001
                print(f"  api lookup failed: {e}")
        u = candidates[i % len(candidates)]
        h = dict(UA)
        if "api.github.com" in u:
            h["Accept"] = "application/octet-stream"
            if _token():
                h["Authorization"] = f"Bearer {_token()}"
        try:
            with requests.get(u, headers=h, timeout=(20, 120), stream=True) as r:
                r.raise_for_status()
                with open(dest + ".part", "wb") as f:
                    shutil.copyfileobj(r.raw, f, 1 << 20)
            size = os.path.getsize(dest + ".part")
            if size < min_bytes:
                raise RuntimeError(f"too small: {size} bytes (< {min_bytes})")
            os.replace(dest + ".part", dest)
            print(f"fetched {os.path.basename(dest)} ({size} bytes) via {u.split('?')[0]}")
            return dest
        except Exception as e:  # noqa: BLE001
            last = e
            if i == attempts - 1:
                break
            w = WAITS[min(i, len(WAITS) - 1)]
            print(f"attempt {i + 1}/{attempts} failed: {str(e)[:160]} -> retry in {w}s")
            time.sleep(w)
    sys.exit(f"::error::download failed after {attempts} attempts: {url} ({last})")


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def easyocr_models(models_dir):
    """Bundle the EasyOCR 'en' models (detector craft + recognizer english_g2) into models_dir, md5-verified,
    then prove they load offline."""
    from easyocr.config import detection_models, recognition_models

    os.makedirs(models_dir, exist_ok=True)
    for m in (detection_models["craft"], recognition_models["gen2"]["english_g2"]):
        target = os.path.join(models_dir, m["filename"])
        if os.path.exists(target) and _md5(target) == m["md5sum"]:
            print(f"{m['filename']}: already present (md5 OK)")
            continue
        zpath = target + ".zip"
        fetch(m["url"], zpath, min_bytes=1_000_000)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(models_dir)
        os.remove(zpath)
        if not os.path.exists(target) or _md5(target) != m["md5sum"]:
            sys.exit(f"::error::{m['filename']}: md5 mismatch after download")
        print(f"{m['filename']}: downloaded + md5 OK")
    import easyocr

    easyocr.Reader(["en"], gpu=False, verbose=False, model_storage_directory=models_dir, download_enabled=False)
    print("models ready")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--easyocr"]:
        easyocr_models(args[1])
    else:
        mb = int(args[args.index("--min-bytes") + 1]) if "--min-bytes" in args else 1
        fetch(args[0], args[1], min_bytes=mb)
