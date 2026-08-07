import numpy as np
import pandas as pd

from app.strategy import coin_intel


def _df(close, spread=1.0, volume=100.0):
    n = len(close)
    high = close + spread
    low = close - spread
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": np.full(n, volume)})


def test_score_up_trend_positive():
    df = _df(np.linspace(100, 200, 120))
    s = coin_intel.coin_score(df)
    assert s["score"] > 0
    assert s["trend"] == "UP"
    assert s["momentum_pct"] > 0
    assert s["r20_pct"] > 0


def test_score_down_trend_negative():
    df = _df(np.linspace(200, 100, 120))
    s = coin_intel.coin_score(df)
    assert s["score"] < 0
    assert s["trend"] == "DOWN"
    assert s["momentum_pct"] < 0


def test_score_flat_is_neutral():
    df = _df(np.full(120, 100.0))
    s = coin_intel.coin_score(df)
    assert s["trend"] == "RANGE"
    assert abs(s["momentum_pct"]) < 1e-6


def test_score_momentum_beats_trend_in_early_reversal():
    close = np.linspace(100, 150, 100)
    close = np.concatenate([close, np.linspace(150, 165, 20)])
    df = _df(close)
    s = coin_intel.coin_score(df)
    assert s["score"] > 0
    assert "reason" not in s


def test_score_too_short_returns_zero():
    df = _df(np.linspace(100, 110, 10))
    s = coin_intel.coin_score(df)
    assert s["score"] == 0.0
    assert "reason" in s


def test_score_has_explained_components():
    df = _df(np.linspace(100, 200, 120))
    s = coin_intel.coin_score(df)
    for k in ("score", "momentum_pct", "r1_pct", "r5_pct", "r10_pct", "r20_pct",
              "trend", "trend_score", "atr_pct", "volatility", "vol_penalty"):
        assert k in s
