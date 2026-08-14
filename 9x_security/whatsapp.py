"""9x Security - WhatsApp alerts via wa.9x.design REST API (official v2 docs).

Text  : POST {base}/api/v2/sendMessage      (multipart: phonenumber, text)
Photo : POST {base}/api/v2/sendMessageFile  (multipart: phonenumber, file, caption)
Auth  : Authorization: Bearer <API_KEY>
If photo sending fails, falls back to a plain text alert so notifications are
never silently lost. All attempts are logged to wa_log.txt.
"""
import os
import re
import threading
from datetime import datetime

import requests

import config

LOG_PATH = os.path.join(config.BASE_DIR, "wa_log.txt")


def _phone(num):
    """International format, digits only (no +, no spaces)."""
    return re.sub(r"\D", "", str(num or ""))


class WhatsAppNotifier:
    def __init__(self, cfg):
        self.update(cfg)

    def update(self, cfg):
        self.enabled = bool(cfg.get("wa_enabled", False))
        self.base = (cfg.get("wa_base_url") or "https://wa.9x.design").rstrip("/")
        self.api_key = cfg.get("wa_api_key", "").strip()
        self.recipients = [p for p in (_phone(r) for r in (cfg.get("wa_recipients", []) or [])) if p]
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
            return False, "API key khaali hai. Settings me API key daalein."
        if not self.recipients:
            return False, "Koi recipient number nahi mila. Ek number daalein (91XXXXXXXXXX)."
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

    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    def _send_text(self, to, text):
        try:
            r = requests.post(
                f"{self.base}/api/v2/sendMessage",
                headers=self._headers(),
                files={"phonenumber": (None, to), "text": (None, text)},
                timeout=20,
            )
            self._log(to, "text", r.status_code, r.text)
            return r.ok, f"HTTP {r.status_code}"
        except Exception as e:
            self._log(to, "text-error", "-", str(e))
            return False, str(e)

    def _send_image(self, to, caption, path):
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except Exception as e:
            self._log(to, "image-read-error", "-", str(e))
            return False, str(e)
        name = os.path.basename(path)
        try:
            r = requests.post(
                f"{self.base}/api/v2/sendMessageFile",
                headers=self._headers(),
                data={"phonenumber": to, "caption": caption, "filename": name},
                files={"file": (name, raw, "image/jpeg")},
                timeout=45,
            )
            self._log(to, "image", r.status_code, r.text)
            return r.ok, f"HTTP {r.status_code}"
        except Exception as e:
            self._log(to, "image-error", "-", str(e))
            return False, str(e)

    def _log(self, to, kind, status, body):
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(
                    f"{datetime.now().isoformat(timespec='seconds')} | {kind} | to={to} "
                    f"| status={status} | {str(body)[:300]}\n"
                )
        except Exception:
            pass
