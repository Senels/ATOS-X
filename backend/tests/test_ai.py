"""AI katmani testleri: ozellikler, etiketleme ve canli kapi.

TensorFlow testleri `pytest.importorskip("tensorflow")` ile kosullu; CI'da
TF olmadigi icin skip edilir. Kapi (gate) testleri stub predictor ile
calisir ve TF gerektirmez.
"""
import numpy as np
import pandas as pd
import pytest

from app.ai.features import FEATURE_NAMES, build_features, last_feature_vector
from app.ai.labeling import class_balance, class_name, make_labels


def _df(n=120, seed=0, start=0.0, drift=0.0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    close = 100.0 * np.cumprod(1.0 + drift + rng.normal(0.0, 0.01, n))
    high = close * (1.0 + np.abs(rng.normal(0.0, 0.004, n)))
    low = close * (1.0 - np.abs(rng.normal(0.0, 0.004, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({
        "open": open_, "high": high, "low": low,
        "close": close, "volume": rng.uniform(1e4, 1e6, n),
    }, index=idx)


def test_build_features_columns_and_no_nan_last():
    df = _df()
    feats = build_features(df)
    assert list(feats.columns) == FEATURE_NAMES
    assert len(feats) == len(df)
    last = feats.iloc[-1]
    assert last.notna().all()


def test_build_features_insufficient_data_empty():
    feats = build_features(_df(n=30))
    assert feats.empty
    assert build_features(None).empty


def test_last_feature_vector_matches_feature_count():
    vec = last_feature_vector(_df())
    assert len(vec) == len(FEATURE_NAMES)
    assert all(isinstance(x, float) for x in vec)


def test_make_labels_uptrend_buys():
    df = _trend_df(90, step=0.005)
    labels = make_labels(df, horizon=6, atr_mult=0.5)
    assert labels.iloc[-20:].max() == 1.0  # yukselen yonde BUY uretir


def test_make_labels_downtrend_sells():
    df = _trend_df(90, step=-0.005)
    labels = make_labels(df, horizon=6, atr_mult=0.5)
    assert labels.iloc[-20:].min() == -1.0  # dusen yonde SELL uretir


def _trend_df(n, step):
    close = 100.0 * np.cumprod(np.ones(n) * (1.0 + step))
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({
        "open": open_, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": [1000.0] * n,
    }, index=idx)


def test_class_mapping():
    assert class_name(-1.0) == "SELL"
    assert class_name(0.0) == "HOLD"
    assert class_name(1.0) == "BUY"


def test_class_balance_counts():
    bal = class_balance(np.array([-1.0, 0.0, 1.0, 1.0, 1.0]))
    assert bal == {"SELL": 1, "HOLD": 1, "BUY": 3}


# -- AI kapi (gate) ---------------------------------------------------------
@pytest.fixture
def trader(tmp_path, monkeypatch):
    from app.strategy import auto_trader as at_mod
    from app.strategy.auto_trader import AutoTrader, Database
    db = Database(str(tmp_path / "at.db"))
    monkeypatch.setattr(at_mod, "Database", lambda *a, **k: db)
    return AutoTrader(_FakeBinance())


class _FakeBinance:
    async def connect(self):
        return True

    async def get_all_tickers(self):
        return {}

    async def load_all_symbols(self):
        return []


def _stub_predictor(direction="BUY", confidence=0.9):
    class P:
        def __init__(self, d, c):
            self.d = d
            self.c = c

        def predict(self, df):
            return {"direction": self.d, "confidence": self.c,
                    "probabilities": [0.1, 0.1, 0.8], "loaded": True}

    return P(direction, confidence)


def test_ai_gate_disabled_when_off(trader):
    allow, info = trader._ai_gate({"signal": "BUY"}, _df(), {"use_ai_model": False})
    assert allow is True
    assert info is None


def test_ai_gate_no_model_passes(trader, monkeypatch):
    monkeypatch.setattr(trader, "_ai_predictor", lambda: None)
    allow, info = trader._ai_gate(
        {"signal": "BUY"}, _df(),
        {"use_ai_model": True, "ai_min_confidence": 0.55})
    assert allow is True
    assert info is None


def test_ai_gate_rejects_mismatch(trader, monkeypatch):
    monkeypatch.setattr(trader, "_ai_predictor", lambda: _stub_predictor("SELL", 0.9))
    allow, info = trader._ai_gate(
        {"signal": "BUY"}, _df(),
        {"use_ai_model": True, "ai_min_confidence": 0.55})
    assert allow is False
    assert info["direction"] == "SELL"


def test_ai_gate_rejects_low_confidence(trader, monkeypatch):
    monkeypatch.setattr(trader, "_ai_predictor", lambda: _stub_predictor("BUY", 0.3))
    allow, info = trader._ai_gate(
        {"signal": "BUY"}, _df(),
        {"use_ai_model": True, "ai_min_confidence": 0.55})
    assert allow is False
    assert info["confidence"] == 0.3


def test_ai_gate_agree_passes(trader, monkeypatch):
    monkeypatch.setattr(trader, "_ai_predictor", lambda: _stub_predictor("BUY", 0.9))
    allow, info = trader._ai_gate(
        {"signal": "BUY"}, _df(),
        {"use_ai_model": True, "ai_min_confidence": 0.55})
    assert allow is True
    assert info["direction"] == "BUY"
    assert info["confidence"] == 0.9


# -- TensorFlow kosullu testler ---------------------------------------------
def test_train_predict_roundtrip(tmp_path, monkeypatch):
    pytest.importorskip("tensorflow")
    import app.ai.model as m
    monkeypatch.setattr(m, "_MODEL_ROOT", tmp_path)
    df = _df(n=400, seed=3)
    res = m.train_from_dataframe([df], horizon=6, atr_mult=1.0, epochs=2,
                                 model_name="t")
    assert res["samples"] > 0
    assert (tmp_path / "t" / "model.keras").exists()
    predictor = m.load_predictor("t")
    assert predictor is not None
    pred = predictor.predict(_df(n=150, seed=9))
    assert pred["direction"] in ("BUY", "SELL", "HOLD")
    assert 0.0 <= pred["confidence"] <= 1.0


def test_train_requires_tf_when_missing(monkeypatch):
    import app.ai.model as m
    monkeypatch.setattr(m, "_HAVE_TF", False)
    with pytest.raises(RuntimeError):
        m.build_model(5)
