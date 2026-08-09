from app.ai.agent_registry import AgentRegistry


def test_registry_persists_and_reads_latest(tmp_path):
    path = tmp_path / "registry.json"
    registry = AgentRegistry(path)
    registry.record("dense", "BTCUSDT", "4h", {"profit_factor": 1.4}, "v1")
    latest = registry.latest("dense", "BTCUSDT", "4h")
    assert latest is not None
    assert latest["metrics"]["profit_factor"] == 1.4

    reloaded = AgentRegistry(path)
    assert reloaded.latest("dense", "BTCUSDT", "4h")["metrics"]["profit_factor"] == 1.4
