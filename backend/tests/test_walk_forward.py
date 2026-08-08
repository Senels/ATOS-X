"""Walk-forward optimizasyon modülü için birim testler (Sprint 18)."""
import numpy as np
import pandas as pd


def _make_df(n: int = 300) -> pd.DataFrame:
    """Backtest engine'in ihtiyaç duyduğu minimum sütunlarla sahte OHLCV DataFrame."""
    rng = np.random.default_rng(42)
    idx = pd.date_range("2022-01-01", periods=n, freq="4h")
    close = 100.0 + np.cumsum(rng.normal(0, 1, n))
    df = pd.DataFrame({
        "open": close - 0.5,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": rng.uniform(1000, 5000, n),
    }, index=idx)
    return df


# ── import guard ──────────────────────────────────────────────────────────────

def test_import():
    from app.optimization.walk_forward import walk_forward  # noqa: F401


# ── split logic ───────────────────────────────────────────────────────────────

def test_split_count():
    """walk_forward n_splits=3 ile en az 1 fold döndürmeli."""
    from app.optimization.walk_forward import walk_forward

    df = _make_df(300)
    results = walk_forward(
        df=df,
        param_grid={"rr_ratio": [1.5]},
        n_splits=3,
        train_pct=0.7,
    )
    assert isinstance(results, dict)
    assert "folds" in results
    assert len(results["folds"]) >= 1


def test_fold_structure():
    """Her fold is/oos metriklerini içermeli."""
    from app.optimization.walk_forward import walk_forward

    df = _make_df(300)
    results = walk_forward(
        df=df,
        param_grid={"rr_ratio": [1.5]},
        n_splits=3,
        train_pct=0.7,
    )
    for fold in results["folds"]:
        assert "is_score" in fold
        assert "oos_score" in fold
        assert "best_params" in fold


def test_overfitting_ratio_present():
    """Sonuçlar overfit_ratio alanı içermeli."""
    from app.optimization.walk_forward import walk_forward

    df = _make_df(300)
    results = walk_forward(
        df=df,
        param_grid={"rr_ratio": [1.5]},
        n_splits=2,
        train_pct=0.7,
    )
    assert "overfit_ratio" in results


def test_empty_param_grid():
    """Boş param_grid da hata vermemeli."""
    from app.optimization.walk_forward import walk_forward

    df = _make_df(300)
    results = walk_forward(
        df=df,
        param_grid={},
        n_splits=2,
        train_pct=0.7,
    )
    assert isinstance(results, dict)


def test_short_df_graceful():
    """Çok kısa DataFrame için walk_forward hata yerine boş/minimal sonuç dönmeli."""
    from app.optimization.walk_forward import walk_forward

    df = _make_df(20)
    results = walk_forward(
        df=df,
        param_grid={"rr_ratio": [1.5, 2.0]},
        n_splits=5,
        train_pct=0.7,
    )
    # En az dict dönmeli; exception olmamalı
    assert isinstance(results, dict)


def test_multiple_params():
    """Birden fazla parametre kombinasyonu çalışmalı."""
    from app.optimization.walk_forward import walk_forward

    df = _make_df(400)
    results = walk_forward(
        df=df,
        param_grid={"rr_ratio": [1.5, 2.0], "atr_mult": [1.0, 1.5]},
        n_splits=3,
        train_pct=0.7,
    )
    assert len(results["folds"]) >= 1
    for fold in results["folds"]:
        assert "best_params" in fold


def test_train_pct_respected():
    """train_pct her fold'da yaklaşık olarak uygulanmalı."""
    from app.optimization.walk_forward import walk_forward

    df = _make_df(400)
    results = walk_forward(
        df=df,
        param_grid={"rr_ratio": [1.5]},
        n_splits=3,
        train_pct=0.8,
    )
    for fold in results["folds"]:
        train_n = fold.get("train_bars", None)
        oos_n = fold.get("test_bars", None)
        if train_n and oos_n:
            actual_ratio = train_n / (train_n + oos_n)
            assert 0.6 <= actual_ratio <= 0.95
