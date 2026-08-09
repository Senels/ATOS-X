from app.dashboard.operational_view import build_operational_view


def test_operational_view_is_compact_and_prioritizes_alerts():
    state = {
        "system": {"connection": "ONLINE", "mode": "PAPER", "kill_switch": False},
        "portfolio": {"equity": 1000, "today_pnl": 10, "drawdown": 0.02, "net_exposure": 200},
        "risk": {"state": "OK"},
        "ai": {"symbol": "BTCUSDT", "direction": "BUY", "confidence": 0.81, "decision": "APPROVED"},
        "opportunities": [{"symbol": f"S{i}"} for i in range(12)],
        "positions": [{"symbol": f"P{i}"} for i in range(20)],
        "alerts": [
            {"severity": "WARNING", "message": "w"},
            {"severity": "CRITICAL", "message": "c"},
            {"severity": "HIGH", "message": "h"},
        ],
    }
    view = build_operational_view(state)
    assert len(view["opportunities"]) == 8
    assert len(view["positions"]) == 12
    assert [x["severity"] for x in view["alerts"]] == ["CRITICAL", "HIGH", "WARNING"]
    assert view["ai"]["decision"] == "APPROVED"
