"""WhatsApp API key: visible in GET /api/settings, stripped on save, actionable errors."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import service  # noqa: E402
from whatsapp import WhatsAppNotifier  # noqa: E402


def _client(monkeypatch, store):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(service, "_cfg", lambda: dict(store))
    monkeypatch.setattr(service.config, "save_config", lambda c: store.update(c))
    tok = "k" * 48
    service._tokens[tok] = time.time()
    c = TestClient(service.app)
    c.headers["X-Auth-Token"] = tok
    return c


def test_wa_key_visible_and_stripped(monkeypatch):
    store = {**service.config.DEFAULTS, "wa_api_key": "", "gh_token": "ghsecret"}
    c = _client(monkeypatch, store)
    g = c.get("/api/settings").json()
    assert g["wa_api_key"] == "" and g["wa_api_key_set"] is False
    assert g["gh_token"] == "" and g["gh_token_set"] is True  # other secrets stay write-only

    assert c.post("/api/settings", json={"wa_api_key": "  wa9x_ABC123 \n"}).json()["ok"]
    assert store["wa_api_key"] == "wa9x_ABC123"
    g = c.get("/api/settings").json()
    assert g["wa_api_key"] == "wa9x_ABC123" and g["wa_api_key_set"] is True

    # saving other fields with the (visible) key unchanged keeps it
    c.post("/api/settings", json={"wa_api_key": "wa9x_ABC123", "wa_enabled": True})
    assert store["wa_api_key"] == "wa9x_ABC123"
    # empty gh_token (write-only) keeps the stored token
    c.post("/api/settings", json={"gh_token": ""})
    assert store["gh_token"] == "ghsecret"
    # clearing the visible WA key really clears it (user can see it's empty)
    c.post("/api/settings", json={"wa_api_key": ""})
    assert store["wa_api_key"] == ""


def test_explain_gives_actionable_hint():
    class R:
        def __init__(self, code, text):
            self.status_code, self.text, self.ok = code, text, code < 400

    e = WhatsAppNotifier._explain
    assert "API key galat" in e(R(401, '{"error":"Invalid API token"}'))
    assert "Invalid API token" in e(R(401, '{"error":"Invalid API token"}'))
    assert "QR" in e(R(403, ""))
    assert e(R(200, "ok")) == "HTTP 200"
    assert "Provider error" in e(R(503, ""))
    assert WhatsAppNotifier._is_network_error("HTTP 403: WhatsApp connection not open") is False
    assert WhatsAppNotifier._is_network_error("Internet/connection error: Max retries") is True
