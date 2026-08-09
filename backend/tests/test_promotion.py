from app.ai.promotion import PromotionPolicy, ensemble_weights, evaluate_agent


def test_promote_requires_oos_evidence():
    result = evaluate_agent({
        "trades": 100, "profit_factor": 1.5, "sharpe": 0.8,
        "sortino": 1.1, "max_drawdown_pct": 12,
        "positive_window_ratio": 0.75, "symbol_coverage": 0.8,
    })
    assert result["decision"] == "PROMOTE"


def test_low_trade_count_rejects():
    result = evaluate_agent({"trades": 10, "profit_factor": 2, "sharpe": 2, "sortino": 2})
    assert result["decision"] == "REJECT"


def test_weights_use_promoted_agents_only():
    snapshot = {"agents": {
        "dense:BTCUSDT:4h": {"agent": "dense", "symbol": "BTCUSDT", "timeframe": "4h",
            "runs": [{"metrics": {"promotion_decision": "PROMOTE", "ensemble_score": 2}}]},
        "lstm:BTCUSDT:4h": {"agent": "lstm", "symbol": "BTCUSDT", "timeframe": "4h",
            "runs": [{"metrics": {"promotion_decision": "RETRAIN", "ensemble_score": 8}}]},
    }}
    assert ensemble_weights(snapshot, "BTCUSDT", "4h") == {"dense": 1.0}
