"""9x Security - WhatsApp alerts via wa.9x.design REST API.

On every Entry/Exit event, sends the snapshot photo (with a caption) to all
configured recipients. If the media (image) send fails, it falls back to a
plain text alert so notifications are never silently lost.

Sending happens on a background thread so it never blocks video processing.
All attempts are logged to wa_log.txt for easy debugging / format tuning.
"""
import os
import threading
from datetime import datetime

import requests

import config

LOG_PATH = os.path.join(config.BASE_DIR, "wa_log.txt")


class WhatsAppNotifier:
    def __init__(self, cfg):
        self.update(cfg)

    def update(self, cfg):
        self.enabled = bool(cfg.get("wa_enabled", False))
        self.base = (cfg.get("wa_base_url") or "https://wa.9x.design").rstrip("/")
        self.api_key = cfg.get("wa_api_key", "").strip()
        self.recipients = cfg.get("wa_recipients", []) or []
        self.send_image = bool(cfg.get("wa_send_image", True))

    # ---- public -----------------------------------------------------------
    def notify(self, ev):
        if not self.enabled or not self.api_key or not self.recipients:
            return
        threading.Thread(target=self._send_all, args=(dict(ev),), daemon=True).start()

    # ---- internals --------------------------------------------------------
    def _caption(self, ev):
        when = str(ev.get("timestamp", "")).replace("T", " ")
        cap = (
            f"🚨 9x Security\n"
            f"{ev.get('direction', '')} - {str(ev.get('vehicle_type', '')).upper()}\n"
            f"Time: {when}"
        )
        if ev.get("plate"):
            cap += f"\nPlate: {ev['plate']}"
        return cap

    def _send_all(self, ev):
        caption = self._caption(ev)
        img = ev.get("image_path", "")
        for to in self.recipients:
            sent = False
            if self.send_image and img and os.path.exists(img):
                sent = self._send_image(to, caption, img)
            if not sent:
                self._send_text(to, caption)

    def _headers(self):
        return {"X-API-Key": self.api_key}

    def _send_text(self, to, text):
        try:
            r = requests.post(
                f"{self.base}/api/v1/messages",
                headers={**self._headers(), "Content-Type": "application/json"},
                json={"to": to, "text": text},
                timeout=20,
            )
            self._log(to, "text", r.status_code, r.text)
            return r.ok
        except Exception as e:
            self._log(to, "text-error", "-", str(e))
            return False

    def _send_image(self, to, caption, path):
        # Multipart upload (common gateway pattern). Falls back to text on failure.
        try:
            with open(path, "rb") as f:
                files = {"file": (os.path.basename(path), f, "image/jpeg")}
                data = {"to": to, "caption": caption, "type": "image"}
                r = requests.post(
                    f"{self.base}/api/v1/messages",
                    headers=self._headers(),
                    files=files,
                    data=data,
                    timeout=30,
                )
            self._log(to, "image", r.status_code, r.text)
            return r.ok
        except Exception as e:
            self._log(to, "image-error", "-", str(e))
            return False

    def _log(self, to, kind, status, body):
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(
                    f"{datetime.now().isoformat(timespec='seconds')} | {kind} | to={to} "
                    f"| status={status} | {str(body)[:300]}\n"
                )
        except Exception:
            pass
