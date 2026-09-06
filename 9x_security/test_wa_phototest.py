"""Send Test Message must also prove the PHOTO channel (sendMessageFile) and
report the provider's message id + delivery status, so the user can forward it
to wa.9x.design when photos do not arrive."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import whatsapp

CALLS = []


class _Fake(BaseHTTPRequestHandler):
    photo_status = 200

    def log_message(self, *a):
        pass

    def _reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        CALLS.append((self.path, len(raw), b"image/jpeg" in raw))
        if self.path.endswith("/sendMessage"):
            self._reply(201, {"success": True, "data": {"id": "txt-1"}})
        elif self.path.endswith("/sendMessageFile"):
            if _Fake.photo_status == 200:
                self._reply(200, {"success": True, "data": {"messageId": "pic-77", "fileType": "jpg"}})
            else:
                self._reply(_Fake.photo_status, {"success": False, "error": "media upload failed"})
        else:
            self._reply(404, {"error": "no"})

    def do_GET(self):
        CALLS.append((self.path, 0, False))
        self._reply(200, {"success": True, "result": {"status": "OK", "statusInfo": "message successfully sent.", "delivery": "device"}})


@pytest.fixture(scope="module")
def server():
    srv = HTTPServer(("127.0.0.1", 0), _Fake)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _notifier(base, send_image=True):
    return whatsapp.WhatsAppNotifier({
        "wa_enabled": True, "wa_base_url": base, "wa_api_key": "k",
        "wa_recipients": ["919876543210"], "wa_send_image": send_image,
    })


def test_test_message_sends_text_and_photo_and_reports_status(server, monkeypatch):
    monkeypatch.setattr(whatsapp.WhatsAppNotifier, "_message_status", _fast_status)
    CALLS.clear()
    _Fake.photo_status = 200
    ok, detail = _notifier(server).test_connection()
    assert ok, detail
    assert "TEXT: SENT" in detail and "PHOTO: SENT" in detail
    assert "id=pic-77" in detail
    assert "message successfully sent" in detail
    paths = [c[0] for c in CALLS]
    assert any(p.endswith("/sendMessage") for p in paths)
    assert any(p.endswith("/sendMessageFile") for p in paths)
    assert any("/message/status" in p for p in paths)
    photo_call = [c for c in CALLS if c[0].endswith("/sendMessageFile")][0]
    assert photo_call[2] and photo_call[1] > 2000  # real jpeg bytes went up


def test_photo_rejected_is_reported_with_provider_error(server, monkeypatch):
    monkeypatch.setattr(whatsapp.WhatsAppNotifier, "_message_status", _fast_status)
    CALLS.clear()
    _Fake.photo_status = 500
    ok, detail = _notifier(server).test_connection()
    assert not ok
    assert "TEXT: SENT" in detail
    assert "PHOTO: FAILED" in detail and "HTTP 500" in detail and "media upload failed" in detail


def test_photo_skipped_when_send_image_off(server):
    CALLS.clear()
    _Fake.photo_status = 200
    ok, detail = _notifier(server, send_image=False).test_connection()
    assert ok
    assert "PHOTO" not in detail
    assert not any(c[0].endswith("/sendMessageFile") for c in CALLS)


_ORIG_STATUS = whatsapp.WhatsAppNotifier._message_status


def _fast_status(self, mid, wait_s=0):
    return _ORIG_STATUS(self, mid, 0)
