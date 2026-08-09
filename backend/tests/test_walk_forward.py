"""Walk-forward ve maliyet muhasebesi testleri."""
import numpy as np
import pandas as pd
import pytest


def _make_df(n: int = 300) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    idx = pd.date_range("2022-01-01", periods=n, freq="4h")
    close = 100.0 + np.cumsum(rng.normal(0, 1, n))
    return pd.DataFrame({
        "open": close - 0.5,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": rng.uniform(1000, 5000, n),
    }, index=idx)


def test_import():
    from app.optimization.walk_forward import walk_forward  # noqa: F401


def test_split_count():
    from app.optimization.walk_forward import walk_forward
    results = walk_forward(df=_make_df(), param_grid={"rr_ratio": [1.5]}, n_splits=3, train_pct=0.7)
    assert isinstance(results, dict)
    assert "folds" in results
    assert len(results["folds"]) >= 1


def test_fold_structure():
    from app.optimization.walk_forward import walk_forward
    results = walk_forward(df=_make_df(), param_grid={"rr_ratio": [1.5]}, n_splits=3, train_pct=0.7)
    for fold in results["folds"]:
        assert "is_score" in fold
        assert "oos_score" in fold
        assert "best_params" in fold


def test_overfitting_ratio_present():
    from app.optimization.walk_forward import walk_forward
    results = walk_forward(df=_make_df(), param_grid={"rr_ratio": [1.5]}, n_splits=2, train_pct=0.7)
    assert "overfit_ratio" in results


def test_empty_param_grid():
    from app.optimization.walk_forward import walk_forward
    assert isinstance(walk_forward(df=_make_df(), param_grid={}, n_splits=2, train_pct=0.7), dict)


def test_short_df_graceful():
    from app.optimization.walk_forward import walk_forward
    assert isinstance(walk_forward(df=_make_df(20), param_grid={"rr_ratio": [1.5]}, n_splits=5, train_pct=0.7), dict)


def test_multiple_params():
    from app.optimization.walk_forward import walk_forward
    results = walk_forward(df=_make_df(400), param_grid={"rr_ratio": [1.5, 2.0], "atr_mult": [1.0, 1.5]}, n_splits=3, train_pct=0.7)
    assert len(results["folds"]) >= 1
    for fold in results["folds"]:
        assert "best_params" in fold


def test_train_pct_respected():
    from app.optimization.walk_forward import walk_forward
    results = walk_forward(df=_make_df(400), param_grid={"rr_ratio": [1.5]}, n_splits=3, train_pct=0.8)
    for fold in results["folds"]:
        train_n, oos_n = fold.get("train_bars"), fold.get("test_bars")
        if train_n and oos_n:
            actual_ratio = train_n / (train_n + oos_n)
            assert 0.6 <= actual_ratio <= 0.95


def test_cost_aware_walk_forward_primitives():
    from app.backtest.walk_forward import CostModel, evaluate_walk_forward, make_windows, trade_return
    windows = make_windows(300, train_size=100, test_size=40, step=40, purge=10)
    assert windows[0].train_end == 100
    assert windows[0].test_start == 110
    costs = CostModel(fee_rate=0.001, slippage_rate=0.001, funding_rate_per_bar=0.0001)
    assert trade_return(0.01, direction=1, bars_held=2, costs=costs) == pytest.approx(0.0058)
    result = evaluate_walk_forward([(1, 0.01, 1), (-1, 0.005, 2), (0, 0.5, 1)], costs=costs)
    assert result["trades"] == 2
    assert 0.0 <= result["win_rate"] <= 1.0
