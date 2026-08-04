import numpy as np
import pandas as pd

from app.strategy import decision


def _df(close, spread=1.0, volume=100.0):
    n = len(close)
    high = close + spread
    low = close - spread
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": np.full(n, volume)})


def test_vote_full_buy_consensus():
    verdict, conf, votes = decision._vote("BUY", "UP", 5.0, "NORMAL")
    assert verdict == "BUY"
    assert conf == 1.0
    assert {v["source"] for v in votes} == {"v23", "trend", "momentum"}


def test_vote_full_sell_consensus():
    verdict, conf, votes = decision._vote("SELL", "DOWN", -5.0, "NORMAL")
    assert verdict == "SELL"
    assert conf == 1.0


def test_vote_disagreement_holds():
    verdict, conf, votes = decision._vote("BUY", "DOWN", -5.0, "NORMAL")
    assert verdict == "HOLD"
    assert conf < 0.6


def test_vote_v23_alone_triggers_with_lower_confidence():
    verdict, conf, votes = decision._vote("BUY", "RANGE", 0.0, "NORMAL")
    assert verdict == "BUY"
    assert 0.5 <= conf < 1.0


def test_vote_extreme_volatility_vetoes():
    verdict, conf, votes = decision._vote("BUY", "UP", 5.0, "EXTREME")
    assert verdict == "HOLD"
    assert conf == 0.0
    assert any(v["source"] == "volatility" for v in votes)


def test_vote_high_volatility_penalizes():
    verdict, conf, votes = decision._vote("BUY", "UP", 5.0, "HIGH")
    assert verdict == "BUY"
    assert conf < 1.0


def test_vote_hold_no_trigger():
    verdict, conf, votes = decision._vote("HOLD", "UP", 5.0, "NORMAL")
    assert verdict == "HOLD"


def test_decide_returns_structure():
    df = _df(np.linspace(100, 200, 120))
    d = decision.decide(df, settings={})
    assert d["verdict"] in ("BUY", "SELL", "HOLD")
    assert 0.0 <= d["confidence"] <= 1.0
    assert isinstance(d["votes"], list)
    assert set(d["components"]) == {"v23", "trend", "momentum_pct", "volatility"}
    assert "reason" in d
