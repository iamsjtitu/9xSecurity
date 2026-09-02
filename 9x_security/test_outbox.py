import http.server, json, os, sys, tempfile, threading, time
sys.path.insert(0, "/app/9x_security")
from database import EventDB
from whatsapp import WhatsAppNotifier

tmp = tempfile.mktemp(suffix=".db")
db = EventDB(db_path=tmp)

img = tempfile.mktemp(suffix=".jpg")
open(img, "wb").write(b"\xff\xd8fakejpg")

cfg = {
    "wa_enabled": True,
    "wa_base_url": "http://127.0.0.1:9",  # unreachable => connection refused
    "wa_api_key": "test-key",
    "wa_recipients": ["919876543210", "919999999999"],
    "wa_send_image": True,
}
n = WhatsAppNotifier(cfg, db=db)
ev = {"vehicle_type": "car", "direction": "Entry", "plate": "HR26AB1234",
      "image_path": img, "timestamp": "2026-06-01T10:00:00"}

# 1. offline send -> queued
n._send_all(ev)
pending = db.outbox_pending()
assert len(pending) == 2, f"expected 2 queued, got {len(pending)}"
assert pending[0]["image_path"] == img
print("PASS 1: offline alert queued for both recipients, image path stored")

# 2. flush while still offline -> stays, attempts increments, network short-circuit
n.flush_outbox()
pending = db.outbox_pending()
assert len(pending) == 2
assert pending[0]["attempts"] == 1
assert pending[1]["attempts"] == 0, "network error should stop batch after first item"
print("PASS 2: still offline -> kept in outbox, attempts=1, batch short-circuited")

# 3. persistence across restart (new DB connection)
db.close()
db2 = EventDB(db_path=tmp)
assert db2.outbox_count() == 2
print("PASS 3: outbox survives restart")

# 4. internet returns (mock server returns 200) -> delivered & removed
hits = []
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        hits.append(self.path)
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"success":true}')
    def log_message(self, *a): pass

srv = http.server.HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
cfg["wa_base_url"] = f"http://127.0.0.1:{srv.server_port}"
n2 = WhatsAppNotifier(cfg, db=db2)
sent = n2.flush_outbox()
assert sent == 2, f"expected 2 sent, got {sent}"
assert db2.outbox_count() == 0, "outbox should be empty after delivery"
assert any("sendMessageFile" in h for h in hits), "photo endpoint should be used on retry"
print("PASS 4: internet back -> both delivered WITH photo, outbox empty (no duplicates)")

# 5. second flush sends nothing (duplicate safety)
hits.clear()
assert n2.flush_outbox() == 0 and not hits
print("PASS 5: re-flush sends nothing (duplicate-safe)")

# 6. purge old pending
db2.outbox_add("911111111111", "old alert", "")
db2.conn.execute("UPDATE outbox SET created_at='2020-01-01T00:00:00'")
db2.conn.commit()
assert db2.outbox_purge_older_than(7) == 1 and db2.outbox_count() == 0
print("PASS 6: retention purge removes stale outbox rows")

os.remove(tmp); os.remove(img)
print("ALL OUTBOX TESTS PASSED")
