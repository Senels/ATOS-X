from app.backtest.ai_gate import TradeCost, evaluate_signal, net_return


def test_low_confidence_is_rejected():
    d = evaluate_signal(timestamp=1, symbol="BTCUSDT", signal="BUY", confidence=0.59)
    assert not d.approved
    assert d.reason == "below_confidence_threshold"


def test_hold_is_rejected():
    d = evaluate_signal(timestamp=1, symbol="BTCUSDT", signal="HOLD", confidence=0.99)
    assert not d.approved
    assert d.reason == "non_actionable_signal"


def test_approved_trade_net_return_includes_costs():
    d = evaluate_signal(timestamp=1, symbol="BTCUSDT", signal="BUY", confidence=0.80)
    assert d.approved
    costs = TradeCost(fee_rate=0.001, slippage_rate=0.0005, funding_rate=0.0002)
    assert abs(net_return(0.01, costs) - 0.0083) < 1e-12
