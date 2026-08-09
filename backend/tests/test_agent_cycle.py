from app.ai.agent_cycle import process_oos_result
from app.ai.agent_registry import AgentRegistry


def test_oos_result_flows_to_registry_and_ensemble(tmp_path):
    registry = AgentRegistry(tmp_path / "registry.json")
    result = process_oos_result(
        registry,
        agent="dense",
        symbol="BTCUSDT",
        timeframe="4h",
        model_version="v1",
        metrics={
            "trades": 100,
            "profit_factor": 1.5,
            "sharpe": 0.8,
            "sortino": 1.0,
            "max_drawdown_pct": 10,
            "positive_window_ratio": 0.8,
            "symbol_coverage": 0.8,
            "ensemble_score": 1.5,
        },
    )
    assert result["decision"]["decision"] == "PROMOTE"
    assert result["ensemble_weights"] == {"dense": 1.0}
    assert registry.latest("dense", "BTCUSDT", "4h")["metrics"]["promotion_decision"] == "PROMOTE"
