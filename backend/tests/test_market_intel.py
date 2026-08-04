import numpy as np
import pandas as pd

from app.strategy import market_intel


def _df(close, spread=1.0, volume=100.0):
    n = len(close)
    high = close + spread
    low = close - spread
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": np.full(n, volume)})


def test_atr_pct_positive():
    df = _df(np.linspace(100, 200, 120))
    s = market_intel.atr_pct(df)
    assert len(s) == 120
    assert s.dropna().gt(0).all()


def test_trend_up():
    df = _df(np.linspace(100, 200, 120))
    t = market_intel.trend_regime(df)
    assert t["regime"] == "UP"
    assert t["slope_pct"] > 0


def test_trend_down():
    df = _df(np.linspace(200, 100, 120))
    t = market_intel.trend_regime(df)
    assert t["regime"] == "DOWN"
    assert t["slope_pct"] < 0


def test_trend_range_flat():
    df = _df(np.full(120, 100.0))
    t = market_intel.trend_regime(df)
    assert t["regime"] == "RANGE"


def test_volatility_constant_is_normal():
    df = _df(np.full(120, 100.0))
    v = market_intel.volatility_regime(df)
    assert v["regime"] == "NORMAL"
    assert v["percentile"] == 50.0


def test_volatility_spike_is_high_or_extreme():
    close = np.linspace(100, 120, 120)
    high = close + 0.5
    low = close - 0.5
    high[-5:] += 5.0
    low[-5:] -= 5.0
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                       "volume": np.full(120, 100.0)})
    v = market_intel.volatility_regime(df)
    assert v["regime"] in ("HIGH", "EXTREME")
    assert v["percentile"] >= 70.0


def test_liquidity_shape():
    df = _df(np.linspace(100, 200, 120))
    liq = market_intel.liquidity(df)
    assert liq["vol_ma"] == 100.0
    assert isinstance(liq["zscore"], float)


def test_analyze_returns_all_keys():
    df = _df(np.linspace(100, 200, 120))
    m = market_intel.analyze(df)
    assert set(m) == {"volatility", "trend", "liquidity"}
    assert m["trend"]["regime"] == "UP"
    assert m["volatility"]["regime"] in ("LOW", "NORMAL", "HIGH", "EXTREME")


def test_analyze_short_series_does_not_crash():
    df = _df(np.linspace(100, 110, 20))
    m = market_intel.analyze(df)
    assert m["trend"]["regime"] in ("UP", "DOWN", "RANGE")
