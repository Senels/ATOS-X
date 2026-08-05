from fastapi.testclient import TestClient

import app.main as main_mod
from app.main import app


def test_get_strategy_settings(monkeypatch):
    monkeypatch.setattr(main_mod.strat_settings, "get_settings",
                        lambda: {"rr_ratio": 2.0, "confirmations": {}})
    client = TestClient(app)
    resp = client.get("/api/v1/strategy/settings")
    client.close()
    assert resp.status_code == 200
    body = resp.json()
    assert body["settings"]["rr_ratio"] == 2.0
    assert "timestamp" in body


def test_update_strategy_settings(monkeypatch):
    calls = []
    monkeypatch.setattr(main_mod.strat_settings, "update_settings",
                        lambda data: calls.append(data) or {})
    client = TestClient(app)
    resp = client.post("/api/v1/strategy/settings", json={"rr_ratio": 3.0})
    client.close()
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "message": "Settings saved"}
    assert calls == [{"rr_ratio": 3.0}]


def test_update_strategy_settings_error(monkeypatch):
    def _boom(data):
        raise ValueError("gecersiz parametre")

    monkeypatch.setattr(main_mod.strat_settings, "update_settings", _boom)
    client = TestClient(app)
    resp = client.post("/api/v1/strategy/settings", json={"rr_ratio": "x"})
    client.close()
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert "gecersiz parametre" in body["error"]


def test_get_default_settings(monkeypatch):
    monkeypatch.setattr(main_mod.strat_settings, "default_settings",
                        lambda: {"initial_equity": 10000.0})
    client = TestClient(app)
    resp = client.get("/api/v1/strategy/defaults")
    client.close()
    assert resp.status_code == 200
    assert resp.json()["settings"]["initial_equity"] == 10000.0
