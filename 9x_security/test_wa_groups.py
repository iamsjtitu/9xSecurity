"""WhatsApp groups: normalization, endpoint routing (sendGroup / sendGroupFile), group list,
settings persistence, outbox with group recipient."""
import http.server
import json
import os
import sys
import tempfile
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import service  # noqa: E402
from database import EventDB  # noqa: E402
from whatsapp import WhatsAppNotifier, _group_id, _is_group  # noqa: E402

HITS = []


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = {"success": True, "statusCode": 200, "data": {"groups": [
            {"id": "120363424861931093@g.us", "name": "Gate Staff", "size": 14},
            {"id": "120363000000000001", "name": "Family", "size": 5},
        ]}}
        HITS.append(("GET", self.path, self.headers.get("Authorization"), ""))
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n).decode("latin-1")
        HITS.append(("POST", self.path, self.headers.get("Authorization"), raw))
        self.send_response(201 if "File" not in self.path else 200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"success":true}')


@pytest.fixture(scope="module")
def server():
    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_group_id_normalization():
    assert _group_id("120363424861931093@g.us") == "120363424861931093@g.us"
    assert _group_id(" 120363424861931093 ") == "120363424861931093@g.us"
    assert _group_id({"id": "120363424861931093@G.US", "name": "x"}) == "120363424861931093@g.us"
    assert _group_id("12345") == "" and _group_id("") == "" and _group_id(None) == ""
    assert _is_group("120363424861931093@g.us") and not _is_group("919876543210")


def test_recipients_merge_numbers_and_groups(server):
    n = WhatsAppNotifier({"wa_enabled": True, "wa_base_url": server, "wa_api_key": "k",
                          "wa_recipients": ["91 98765 43210"],
                          "wa_groups": [{"id": "120363424861931093@g.us", "name": "Gate Staff"}, "120363000000000001"]})
    assert n.recipients == ["919876543210", "120363424861931093@g.us", "120363000000000001@g.us"]


def test_text_and_image_route_to_group_endpoints(server, tmp_path):
    HITS.clear()
    n = WhatsAppNotifier({"wa_enabled": True, "wa_base_url": server, "wa_api_key": "k",
                          "wa_recipients": ["919876543210"], "wa_groups": ["120363424861931093@g.us"]})
    ok, detail = n.test_connection()
    assert ok and "Group 120363424861931093: SENT" in detail and "919876543210: SENT" in detail
    paths = [h[1] for h in HITS]
    assert "/api/v2/sendMessage" in paths and "/api/v2/sendGroup" in paths
    grp = [h for h in HITS if h[1] == "/api/v2/sendGroup"][0]
    assert 'name="groupId"' in grp[3] and "120363424861931093@g.us" in grp[3] and grp[2] == "Bearer k"

    img = tmp_path / "snap.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"0" * 100)
    HITS.clear()
    ok, _ = n._deliver("120363424861931093@g.us", "cap", str(img))
    assert ok and HITS[0][1] == "/api/v2/sendGroupFile" and 'name="groupId"' in HITS[0][3]
    ok, _ = n._deliver("919876543210", "cap", str(img))
    assert ok and HITS[1][1] == "/api/v2/sendMessageFile" and 'name="phonenumber"' in HITS[1][3]


def test_list_groups(server):
    n = WhatsAppNotifier({"wa_base_url": server, "wa_api_key": "k"})
    ok, groups = n.list_groups()
    assert ok and groups == [
        {"id": "120363424861931093@g.us", "name": "Gate Staff", "size": 14},
        {"id": "120363000000000001@g.us", "name": "Family", "size": 5},
    ]
    assert WhatsAppNotifier({"wa_base_url": server, "wa_api_key": ""}).list_groups()[0] is False


def test_outbox_queues_and_flushes_group_recipient(server, tmp_path):
    db = EventDB(db_path=str(tmp_path / "t.db"))
    n = WhatsAppNotifier({"wa_enabled": True, "wa_base_url": "http://127.0.0.1:9", "wa_api_key": "k",
                          "wa_groups": ["120363424861931093@g.us"], "wa_send_image": False}, db=db)
    n._send_all({"direction": "Entry", "vehicle_type": "car", "timestamp": "2026-09-03T10:00:00", "image_path": ""})
    assert db.outbox_count() == 1 and db.outbox_pending()[0]["recipient"] == "120363424861931093@g.us"
    n.update({"wa_enabled": True, "wa_base_url": server, "wa_api_key": "k", "wa_groups": ["120363424861931093@g.us"]})
    HITS.clear()
    assert n.flush_outbox() == 1 and db.outbox_count() == 0 and HITS[0][1] == "/api/v2/sendGroup"


def test_settings_and_groups_endpoint(server, monkeypatch):
    from fastapi.testclient import TestClient

    store = {**service.config.DEFAULTS, "wa_api_key": "k", "wa_base_url": server}
    monkeypatch.setattr(service, "_cfg", lambda: dict(store))
    monkeypatch.setattr(service.config, "save_config", lambda c: store.update(c))
    tok = "g" * 48
    service._tokens[tok] = time.time()
    c = TestClient(service.app)
    c.headers["X-Auth-Token"] = tok
    groups = [{"id": "120363424861931093@g.us", "name": "Gate Staff"}]
    assert c.post("/api/settings", json={"wa_groups": groups}).json()["ok"]
    assert c.get("/api/settings").json()["wa_groups"] == groups
    r = c.post("/api/whatsapp/groups", json={}).json()
    assert r["ok"] and r["groups"][0]["name"] == "Gate Staff"
    r = c.post("/api/whatsapp/test", json={"wa_api_key": "k", "wa_recipients": [], "wa_groups": groups}).json()
    assert r["ok"] and "Group 120363424861931093: SENT" in r["detail"]
    d = c.get("/api/diagnostics").json()
    assert d["whatsapp"]["groups"] == 1
