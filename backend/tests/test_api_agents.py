"""Agent konseyi API endpoint testleri."""
from starlette.testclient import TestClient

from app.core.database import Database
from app.main import app


class _FakeTrader:
    equity = 10000.0
    active_positions = {}
    running = True


def _client():
    app.state.auto_trader = _FakeTrader()
    return TestClient(app)


def _patch_db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "t.db")
    Database(db_path)
    fake_db = type("D", (), {"db_path": db_path})()
    monkeypatch.setattr("app.core.database.Database", lambda: fake_db)


def test_agents_summary(tmp_path, monkeypatch):
    _patch_db(monkeypatch, tmp_path)
    with _client() as client:
        res = client.get("/api/v1/agents/summary")
    assert res.status_code == 200
    body = res.json()
    assert "memory" in body
    assert len(body["agents"]) == 50
    assert "use_agent_council" in body
    cats = {a["category"] for a in body["agents"]}
    assert {"technical", "macro", "microstructure", "risk",
            "statistical", "ai"} <= cats


def test_agents_history_unknown(tmp_path, monkeypatch):
    _patch_db(monkeypatch, tmp_path)
    with _client() as client:
        res = client.get("/api/v1/agents/history", params={"agent_id": "yok"})
    assert res.status_code == 200
    assert res.json()["error"] == "bilinmeyen ajan"


def test_agents_history_known(tmp_path, monkeypatch):
    _patch_db(monkeypatch, tmp_path)
    with _client() as client:
        res = client.get("/api/v1/agents/history",
                         params={"agent_id": "trend_ema"})
    assert res.status_code == 200
    assert res.json()["votes"] == []
