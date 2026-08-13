"""9x Security - WhatsApp alerts via wa.9x.design REST API.

On every Entry/Exit event, sends the snapshot photo (with a caption) to all
configured recipients. Photo is attempted using several known WhatsApp-gateway
payload formats (auto-detect) and the first one that succeeds is used. If image
sending fails entirely, it falls back to a plain text alert so notifications are
never silently lost. All attempts are logged to wa_log.txt.
"""
import base64
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

    def test_connection(self):
        """Synchronous test send used by the Settings 'Send Test Message' button.
        Returns (ok, detail_text)."""
        if not self.api_key:
            return False, "X-API-Key khaali hai. Settings me API key daalein."
        if not self.recipients:
            return False, "Koi recipient number nahi mila. Ek number daalein."
        text = (
            "✅ 9x Security test alert\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            "Agar ye message aaya hai to setup sahi hai."
        )
        lines = []
        all_ok = True
        for to in self.recipients:
            ok, info = self._send_text(to, text)
            lines.append(f"{to}: {'SENT ✅' if ok else 'FAILED ❌'} ({info})")
            all_ok = all_ok and ok
        return all_ok, "\n".join(lines)

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
            ok = False
            if self.send_image and img and os.path.exists(img):
                ok, _ = self._send_image(to, caption, img)
            if not ok:
                self._send_text(to, caption)

    def _endpoint(self):
        return f"{self.base}/api/v1/messages"

    def _headers(self, json_mode=False):
        h = {"X-API-Key": self.api_key}
        if json_mode:
            h["Content-Type"] = "application/json"
        return h

    def _send_text(self, to, text):
        try:
            r = requests.post(
                self._endpoint(),
                headers=self._headers(json_mode=True),
                json={"to": to, "text": text},
                timeout=20,
            )
            self._log(to, "text", r.status_code, r.text)
            return r.ok, f"HTTP {r.status_code}"
        except Exception as e:
            self._log(to, "text-error", "-", str(e))
            return False, str(e)

    def _send_image(self, to, caption, path):
        """Try several known gateway image formats; use the first that returns 2xx."""
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except Exception as e:
            self._log(to, "image-read-error", "-", str(e))
            return False, str(e)
        name = os.path.basename(path)
        b64 = base64.b64encode(raw).decode()

        attempts = [
            # (label, kwargs for requests.post)
            ("multipart:file", dict(
                files={"file": (name, raw, "image/jpeg")},
                data={"to": to, "caption": caption, "type": "image"},
                headers=self._headers())),
            ("multipart:media", dict(
                files={"media": (name, raw, "image/jpeg")},
                data={"to": to, "caption": caption},
                headers=self._headers())),
            ("multipart:image", dict(
                files={"image": (name, raw, "image/jpeg")},
                data={"to": to, "caption": caption},
                headers=self._headers())),
            ("json:image_b64", dict(
                json={"to": to, "image": b64, "caption": caption},
                headers=self._headers(json_mode=True))),
            ("json:media_obj", dict(
                json={"to": to, "type": "image",
                      "media": {"data": b64, "mimetype": "image/jpeg", "caption": caption}},
                headers=self._headers(json_mode=True))),
        ]
        for label, kwargs in attempts:
            try:
                r = requests.post(self._endpoint(), timeout=30, **kwargs)
                self._log(to, "image:" + label, r.status_code, r.text)
                if r.ok:
                    return True, label
            except Exception as e:
                self._log(to, "image-error:" + label, "-", str(e))
        return False, "all image formats failed"

    def _log(self, to, kind, status, body):
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(
                    f"{datetime.now().isoformat(timespec='seconds')} | {kind} | to={to} "
                    f"| status={status} | {str(body)[:300]}\n"
                )
        except Exception:
            pass
