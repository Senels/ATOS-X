from app.engine.pipeline import DecisionPipeline


def test_long_signal_can_be_paper_filled():
    pipe = DecisionPipeline()
    result = pipe.process(
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "timeframe": "1h",
            "price": 100000,
            "strategy": "ATOS_X_CORE",
        },
        equity=10000,
        decision={"verdict": "BUY", "confidence": 0.80},
        stop_price=99000,
        leverage=2,
    )
    assert result["status"] == "PAPER_FILLED"
    assert result["order"]["symbol"] == "BTCUSDT"


def test_hold_is_rejected_before_risk():
    pipe = DecisionPipeline()
    result = pipe.process(
        {"symbol": "BTCUSDT", "side": "LONG", "price": 100000},
        equity=10000,
        decision={"verdict": "HOLD", "confidence": 1.0},
    )
    assert result["status"] == "REJECTED"
    assert result["stage"] == "decision"


def test_direction_mismatch_is_rejected():
    pipe = DecisionPipeline()
    result = pipe.process(
        {"symbol": "BTCUSDT", "side": "LONG", "price": 100000},
        equity=10000,
        decision={"verdict": "SELL", "confidence": 0.9},
    )
    assert result["status"] == "REJECTED"
    assert result["stage"] == "decision"
