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
    assert set(d["components"]) == {"v23", "strategy", "trend", "momentum_pct", "volatility"}
    assert "reason" in d


# -- TTP birincil oy ----------------------------------------------------------
def test_vote_ttp_source_labels_votes():
    verdict, conf, votes = decision._vote("BUY", "UP", 5.0, "NORMAL", source="ttp")
    assert verdict == "BUY"
    assert conf == 1.0
    assert {v["source"] for v in votes} == {"ttp", "trend", "momentum"}


def test_vote_ttp_disagreement_holds():
    verdict, conf, votes = decision._vote("BUY", "DOWN", -5.0, "NORMAL", source="ttp")
    assert verdict == "HOLD"
    assert conf < 0.6


def test_vote_ttp_one_side_agrees_passes():
    verdict, conf, votes = decision._vote("BUY", "DOWN", 5.0, "NORMAL", source="ttp")
    assert verdict == "BUY"
    assert 0.5 <= conf < 1.0


def test_decide_ttp_primary_uses_primary_signal(monkeypatch):
    captured = {}

    def fake_v23(df):
        captured["v23_called"] = True
        return {"signal": "SELL"}

    monkeypatch.setattr(decision, "TradeBotV23", lambda cfg: type(
        "Bot", (), {"generate_signal": fake_v23})())
    df = _df(np.linspace(100, 200, 120))
    d = decision.decide(df, settings={},
                        primary_signal={"signal": "BUY", "source": "ttp"})
    assert "v23_called" not in captured  # v23 hesaplanmadi
    assert d["components"]["strategy"] == "ttp"
    assert d["components"]["v23"] is None
    assert any(v["source"] == "ttp" for v in d["votes"])
    assert d["verdict"] in ("BUY", "SELL", "HOLD")


def test_decide_ttp_mode_uses_ttp_primary(monkeypatch):
    captured = {}

    def fake_v23(df):
        captured["v23_called"] = True
        return {"signal": "SELL"}

    class FakeTtp:
        def __init__(self, cfg):
            pass

        def analyze_full(self, df):
            return {"orders": pd.DataFrame({
                "signal": [0, 1], "sl": [np.nan, 99.0],
                "tp": [np.nan, 110.0], "strength": [0.0, 1.0],
                "in_position": [False, True], "exit": ["", ""],
                "exit_qty_pct": [0.0, 0.0], "exit_price": [np.nan, np.nan],
            })}

    monkeypatch.setattr(decision, "TradeBotV23", lambda cfg: type(
        "Bot", (), {"generate_signal": fake_v23})())
    monkeypatch.setattr(decision, "TtpTsl", FakeTtp)
    df = _df(np.linspace(100, 200, 120))
    d = decision.decide(df, settings={"active_strategy": "ttp"})
    assert "v23_called" not in captured  # v23 hesaplanmadi
    assert d["components"]["strategy"] == "ttp"
    assert d["components"]["v23"] is None
    assert any(v["source"] == "ttp" for v in d["votes"])
    assert d["price"] == 200.0  # son bar kapanisi


def test_decide_v23_mode_keeps_legacy_behavior(monkeypatch):
    captured = {}

    def fake_v23(df):
        captured["v23_called"] = True
        return {"signal": "BUY", "price": 150.0, "sl": 140.0, "tp": 170.0}

    monkeypatch.setattr(decision, "TradeBotV23", lambda cfg: type(
        "Bot", (), {"generate_signal": staticmethod(fake_v23)})())
    df = _df(np.linspace(100, 200, 120))
    d = decision.decide(df, settings={"active_strategy": "v23"})
    assert captured["v23_called"] is True
    assert d["components"]["strategy"] == "v23"
    assert d["components"]["v23"] == "BUY"
    assert d["price"] == 150.0
