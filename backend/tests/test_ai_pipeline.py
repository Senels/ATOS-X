import numpy as np
import pandas as pd

from app.backtest.ai_pipeline import AIGateConfig, build_ai_blocks


def test_ai_blocks_follow_confidence_gate():
    orders = pd.DataFrame({"signal": [1, -1, 0, 1]})
    blocks, ledger = build_ai_blocks(
        orders,
        symbol="BTCUSDT",
        confidence=np.array([0.80, 0.59, 0.99, 0.61]),
        config=AIGateConfig(min_confidence=0.60),
    )
    assert blocks.tolist() == [False, True, True, False]
    assert [x["approved"] for x in ledger] == [True, False, False, True]


def test_confidence_shape_is_validated():
    orders = pd.DataFrame({"signal": [1, -1]})
    try:
        build_ai_blocks(orders, symbol="BTCUSDT", confidence=[0.9])
    except ValueError as exc:
        assert "confidence length" in str(exc)
    else:
        raise AssertionError("expected ValueError")
