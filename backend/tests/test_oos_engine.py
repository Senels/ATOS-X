import pandas as pd

from app.backtest.oos_engine import BacktestConfig, run_oos_backtest


def test_oos_engine_accounts_for_costs_and_returns_trade_log():
    idx = pd.date_range("2026-01-01", periods=8, freq="h", tz="UTC")
    frame = pd.DataFrame({
        "open": [100, 100, 102, 104, 106, 104, 102, 101],
        "high": [101, 103, 105, 107, 108, 105, 103, 102],
        "low": [99, 99, 101, 103, 104, 102, 100, 99],
        "close": [100, 102, 104, 106, 105, 103, 101, 100],
    }, index=idx)
    result = run_oos_backtest(
        frame,
        [1, 0, 0, 0, -1, 0, 0, 0],
        BacktestConfig(initial_equity=1000, fee_rate=0.0005, slippage_bps=2),
    )
    assert result["trades"] >= 1
    assert result["fees"] > 0
    assert "trade_log" in result
