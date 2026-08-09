import json

from starlette.testclient import TestClient

from app.api import research
from app.main import app


def test_research_summary_reports_missing_artifacts_as_not_run(tmp_path, monkeypatch):
    monkeypatch.setattr(research, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(research, "_REPORTS_ROOT", tmp_path / "reports")
    monkeypatch.setattr(research, "_REGISTRY_PATH", tmp_path / "backend" / "data" / "agent_registry.json")
    body = research.build_research_summary()
    assert body["live_trading"] is False
    assert body["status"] == "NOT_RUN"
    assert {stage["status"] for stage in body["stages"]} == {"NOT_RUN"}


def test_research_summary_preserves_blocked_state_and_promoted_weights(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "research_manifest.json").write_text(json.dumps({"status": "INSUFFICIENT_HISTORY"}), encoding="utf-8")
    (reports / "model_oos_pipeline.json").write_text(json.dumps({"status": "BLOCKED", "blocking_reasons": ["missing_lstm_model"]}), encoding="utf-8")
    (reports / "symbol_oos_scorecard.json").write_text(json.dumps({"reports": [{"symbol": "BTCUSDT", "models": {"dense": {"trades": 12, "profit_factor": 1.2}}}]}), encoding="utf-8")
    registry_path = tmp_path / "backend" / "data" / "agent_registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(json.dumps({"agents": {"dense:BTCUSDT:4h": {"agent": "dense", "symbol": "BTCUSDT", "timeframe": "4h", "runs": [{"timestamp": "2026-01-01T00:00:00Z", "metrics": {"promotion_decision": "PROMOTE", "ensemble_score": 2.0}}]}}}), encoding="utf-8")
    monkeypatch.setattr(research, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(research, "_REPORTS_ROOT", reports)
    monkeypatch.setattr(research, "_REGISTRY_PATH", registry_path)
    body = research.build_research_summary()
    assert body["status"] == "BLOCKED"
    assert "missing_lstm_model" in body["blocking_reasons"]
    assert body["registry"]["ensembles"] == [{"symbol": "BTCUSDT", "timeframe": "4h", "weights": {"dense": 1.0}}]
    assert body["scorecards"][0]["symbol"] == "BTCUSDT"


def test_research_summary_surfaces_download_failure(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "research_manifest.json").write_text(
        json.dumps({"status": "DOWNLOAD_FAILED", "blocking_reasons": ["download_failed_exit_1"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(research, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(research, "_REPORTS_ROOT", reports)
    monkeypatch.setattr(research, "_REGISTRY_PATH", tmp_path / "backend" / "data" / "agent_registry.json")
    body = research.build_research_summary()
    assert body["status"] == "BLOCKED"
    assert body["blocking_reasons"] == ["download_failed_exit_1"]


def test_research_dashboard_route_is_read_only_page():
    client = TestClient(app)
    response = client.get("/dashboard/research")
    assert response.status_code == 200
    assert "Research Control Room" in response.text
    assert "/api/v1/research/summary" in response.text
    assert "emir göndermez" in response.text
    client.close()
