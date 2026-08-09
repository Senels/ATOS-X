import pandas as pd

from app.backtest.per_symbol_oos import prepare_symbol, symbol_oos_fold


def test_symbol_data_stays_one_chronological_series():
    idx = pd.date_range("2026-01-01", periods=400, freq="h", tz="UTC")
    close = pd.Series(range(100, 500), index=idx, dtype=float)
    df = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1.0})
    both, X, y = prepare_symbol(df, horizon=4)
    assert len(X) == len(y) == len(both)
    assert both.index.is_monotonic_increasing
    assert both.index.is_unique


def test_per_symbol_has_oos_fold():
    idx = pd.date_range("2026-01-01", periods=500, freq="h", tz="UTC")
    close = pd.Series(100 + (pd.Series(range(500), index=idx) % 20), index=idx, dtype=float)
    df = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1.0})
    _, X, y, folds = symbol_oos_fold(df, horizon=4)
    assert folds
    tr, va, te = folds[0]
    assert tr.end <= va.start <= va.end <= te.start <= te.end
    assert len(X) == len(y)
