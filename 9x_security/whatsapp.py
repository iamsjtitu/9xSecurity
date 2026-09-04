"""9x Security - WhatsApp alerts via wa.9x.design REST API (official v2 docs).

Text  : POST {base}/api/v2/sendMessage      (multipart: phonenumber, text)
Photo : POST {base}/api/v2/sendMessageFile  (multipart: phonenumber, file, caption)
Auth  : Authorization: Bearer <API_KEY>
Failed sends (no internet / provider down) are saved in a durable SQLite
outbox and retried automatically until delivered. Logged to wa_log.txt.
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


def _group_id(g):
    """Normalize a group (dict {id,name} or string) to '<digits>@g.us'; '' if invalid."""
    if isinstance(g, dict):
        g = g.get("id", "")
    s = str(g or "").strip().lower()
    if not s:
        return ""
    digits = re.sub(r"\D", "", s.split("@")[0])
    return f"{digits}@g.us" if len(digits) >= 10 else ""


def _is_group(to):
    return str(to).endswith("@g.us")


class WhatsAppNotifier:
    def __init__(self, cfg, db=None):
        self.db = db
        self._flush_lock = threading.Lock()
        self.update(cfg)

    def update(self, cfg):
        self.enabled = bool(cfg.get("wa_enabled", False))
        self.base = (cfg.get("wa_base_url") or "https://wa.9x.design").rstrip("/")
        self.api_key = cfg.get("wa_api_key", "").strip()
        numbers = [p for p in (_phone(r) for r in (cfg.get("wa_recipients", []) or [])) if p]
        groups = [g for g in (_group_id(x) for x in (cfg.get("wa_groups", []) or [])) if g]
        self.recipients = numbers + groups  # groups are '<digits>@g.us' strings
        self.send_image = bool(cfg.get("wa_send_image", True))
        self.schedule_enabled = bool(cfg.get("wa_schedule_enabled", False))
        self.wa_start = cfg.get("wa_start", "18:00")
        self.wa_end = cfg.get("wa_end", "06:00")

    def list_groups(self):
        """Groups the connected WhatsApp session is a member of → (ok, groups|detail)."""
        if not self.api_key:
            return False, "API key khaali hai. Pehle API key daalein."
        try:
            r = requests.get(f"{self.base}/api/v2/groupChat/getGroupList", headers=self._headers(), timeout=20)
        except Exception as e:
            return False, f"Internet/connection error: {e}"
        if not r.ok:
            return False, self._explain(r)
        try:
            data = r.json()
            groups = (data.get("data") or {}).get("groups") or data.get("groups") or []
            out = [{"id": _group_id(g.get("id", "")), "name": g.get("name", ""), "size": g.get("size", 0)}
                   for g in groups if _group_id(g.get("id", ""))]
            return True, out
        except Exception as e:
            return False, f"Group list parse fail: {e}"

    def allowed_now(self, now=None):
        if not self.schedule_enabled:
            return True
        return config.in_time_window(self.wa_start, self.wa_end, now=now)

    # ---- public -----------------------------------------------------------
    def notify(self, ev):
        if not self.enabled or not self.api_key or not self.recipients:
            return
        if not self.allowed_now():
            self._log("-", "schedule-skip", "-", f"outside {self.wa_start}-{self.wa_end}")
            return
        threading.Thread(target=self._send_all, args=(dict(ev),), daemon=True).start()

    def test_connection(self):
        """Synchronous test send used by the Settings 'Send Test Message' button.
        Returns (ok, detail_text)."""
        if not self.api_key:
            return False, "API key khaali hai. Settings me API key daalein."
        if not self.recipients:
            return False, "Koi recipient nahi mila. Ek number (91XXXXXXXXXX) ya WhatsApp group select karein."
        text = (
            "✅ 9x Security test alert\n"
            f"Time: {datetime.now().strftime('%d-%m-%Y %I:%M:%S %p')}\n"
            "Agar ye message aaya hai to setup sahi hai."
        )
        lines = []
        all_ok = True
        for to in self.recipients:
            ok, info = self._send_text(to, text)
            label = f"Group {to.split('@')[0]}" if _is_group(to) else to
            lines.append(f"{label}: {'SENT ✅' if ok else 'FAILED ❌'} ({info})")
            all_ok = all_ok and ok
        return all_ok, "\n".join(lines)

    def flush_outbox(self):
        """Try delivering pending outbox alerts. Returns count delivered."""
        if self.db is None or not self.enabled or not self.api_key:
            return 0
        if not self._flush_lock.acquire(blocking=False):
            return 0
        sent = 0
        try:
            for item in self.db.outbox_pending(limit=25):
                ok, err = self._deliver(item["recipient"], item["caption"], item.get("image_path") or "")
                if ok:
                    self.db.outbox_delete(item["id"])
                    self._log(item["recipient"], "outbox-sent", "-", f"id={item['id']}")
                    sent += 1
                else:
                    self.db.outbox_mark_failed(item["id"], err)
                    if self._is_network_error(err):
                        break  # still offline: stop, retry next cycle
        finally:
            self._flush_lock.release()
        return sent

    # ---- internals --------------------------------------------------------
    @staticmethod
    def _is_network_error(err):
        e = str(err).lower()
        if e.startswith("http "):
            return False  # provider answered: not a transport problem
        return any(s in e for s in (
            "connection", "timed out", "timeout", "resolve", "network",
            "unreachable", "getaddrinfo", "refused",
        ))
    def _caption(self, ev):
        raw = str(ev.get("timestamp", ""))
        try:
            when = datetime.fromisoformat(raw).strftime("%d-%m-%Y %I:%M:%S %p")
        except ValueError:
            when = raw.replace("T", " ")
        cap = (
            f"🚨 9x Security\n"
            f"{ev.get('direction', '')} - {str(ev.get('vehicle_type', '')).upper()}\n"
            f"Time: {when}\n"
            f"Number: {ev.get('plate') or 'Not detected'}"
        )
        return cap

    def _send_all(self, ev):
        caption = self._caption(ev)
        img = ev.get("image_path", "")
        for to in self.recipients:
            ok, err = self._deliver(to, caption, img)
            if not ok and self.db is not None:
                oid = self.db.outbox_add(to, caption, img if self.send_image else "")
                self._log(to, "outbox-queued", "-", f"id={oid} reason={err}")

    def _deliver(self, to, caption, img):
        """One full delivery attempt: photo first, text fallback if provider
        rejects the photo. Returns (ok, error_detail)."""
        if self.send_image and img and os.path.exists(img):
            ok, err = self._send_image(to, caption, img)
            if ok:
                return True, ""
            if self._is_network_error(err):
                return False, err  # offline: queue full alert, retry later with photo
            self._log(to, "image-rejected->text", "-", err)
        elif self.send_image and img:
            self._log(to, "image-missing->text", "-", img)
        return self._send_text(to, caption)

    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    @staticmethod
    def _explain(r):
        """Human-readable (Hinglish) reason for a provider response."""
        if r.ok:
            return f"HTTP {r.status_code}"
        hints = {
            401: "API key galat/expire hai — wa.9x.design Dashboard > API Key se dobara copy karein",
            403: "Access denied — WhatsApp session connected nahi (dashboard me QR scan karein) ya key galat",
            404: "Number/endpoint nahi mila — number 91XXXXXXXXXX format me hai?",
            429: "Bahut zyada messages — thodi der baad try karein",
        }
        hint = hints.get(r.status_code, "Provider error — thodi der baad try karein" if r.status_code >= 500 else "")
        snippet = " ".join(str(r.text or "")[:120].split())
        return f"HTTP {r.status_code}: {hint}" + (f" [{snippet}]" if snippet else "")

    def _send_text(self, to, text):
        group = _is_group(to)
        try:
            r = requests.post(
                f"{self.base}/api/v2/{'sendGroup' if group else 'sendMessage'}",
                headers=self._headers(),
                files={("groupId" if group else "phonenumber"): (None, to), "text": (None, text)},
                timeout=20,
            )
            self._log(to, "group-text" if group else "text", r.status_code, r.text)
            return r.ok, self._explain(r)
        except Exception as e:
            self._log(to, "text-error", "-", str(e))
            return False, f"Internet/connection error: {e}"

    def _send_image(self, to, caption, path):
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except Exception as e:
            self._log(to, "image-read-error", "-", str(e))
            return False, str(e)
        name = os.path.basename(path)
        group = _is_group(to)
        try:
            r = requests.post(
                f"{self.base}/api/v2/{'sendGroupFile' if group else 'sendMessageFile'}",
                headers=self._headers(),
                data={("groupId" if group else "phonenumber"): to, "caption": caption, "filename": name},
                files={"file": (name, raw, "image/jpeg")},
                timeout=45,
            )
            self._log(to, "group-image" if group else "image", r.status_code, r.text)
            return r.ok, self._explain(r)
        except Exception as e:
            self._log(to, "image-error", "-", str(e))
            return False, f"Internet/connection error: {e}"

    def _log(self, to, kind, status, body):
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(
                    f"{datetime.now().isoformat(timespec='seconds')} | {kind} | to={to} "
                    f"| status={status} | {str(body)[:300]}\n"
                )
        except Exception:
            pass
