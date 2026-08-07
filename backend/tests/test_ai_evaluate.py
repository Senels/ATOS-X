"""AI degerlendirme modulu testleri (TensorFlow gerektirmez).

`evaluate_model`/`summarize`/`resolve_outcome`: sahte model+scaler ile
canli cozumleme semantiginin arsiv uzerindeki sonuclarini dogrular.
"""
import numpy as np
import pandas as pd
import pytest
from app.ai.evaluate import DIRECTIONS, evaluate_model, resolve_outcome, summarize
from app.ai.features import FEATURE_NAMES


class FakeScaler:
    def transform(self, X):
        return np.asarray(X)


class FakeModel:
    """`predict(X, batch_size, verbose)` -> sabit yon dagilimli prob matrisi."""

    def __init__(self, target_idx: int):
        self.target_idx = target_idx

    def predict(self, X, batch_size=1024, verbose=0):
        n = len(X)
        probs = np.zeros((n, 3), dtype=np.float32)
        probs[:, self.target_idx] = 1.0
        return probs


def make_df(n=100, drift=0.001):
    idx = pd.date_range("2026-01-01", periods=n, freq="4h")
    close = 100.0 * (1.0 + drift * np.arange(n))
    df = pd.DataFrame({
        "open": close * 0.999,
        "high": close * 1.002,
        "low": close * 0.998,
        "close": close,
        "volume": np.full(n, 1000.0),
    }, index=idx)
    return df


def run(pred_idx, drift, horizon=12):
    df = make_df(drift=drift)
    model, scaler = FakeModel(pred_idx), FakeScaler()
    out = evaluate_model(model, scaler, FEATURE_NAMES, {"TEST": df}, horizon=horizon)
    return out


def test_resolve_outcome_rules():
    assert resolve_outcome("BUY", 100.0, 101.0) == "hit"
    assert resolve_outcome("BUY", 100.0, 100.0) == "miss"
    assert resolve_outcome("SELL", 100.0, 99.0) == "hit"
    assert resolve_outcome("SELL", 100.0, 100.0) == "miss"
    assert resolve_outcome("HOLD", 100.0, 101.0) == "na"


def test_all_buy_rising_close_all_hit():
    out = run(DIRECTIONS.index("BUY"), drift=0.001)
    assert len(out) == 100 - 12
    assert (out["outcome"] == "hit").all()
    assert (out["direction"] == "BUY").all()


def test_all_sell_rising_close_all_miss():
    out = run(DIRECTIONS.index("SELL"), drift=0.001)
    assert len(out) == 88
    assert (out["outcome"] == "miss").all()


def test_all_hold_no_samples():
    out = run(DIRECTIONS.index("HOLD"), drift=0.001)
    assert out.empty
    s = summarize(out)
    assert s["samples"] == 0


def test_summarize_accuracy_direction_stats():
    out = run(DIRECTIONS.index("BUY"), drift=0.001)
    s = summarize(out, recent_bars=50)
    assert s["samples"] == 88
    assert s["accuracy"] == pytest.approx(1.0)
    assert s["hits"] == 88 and s["misses"] == 0
    assert s["by_direction"]["BUY"]["accuracy"] == pytest.approx(1.0)
    assert s["by_direction"]["BUY"]["avg_confidence"] == pytest.approx(1.0)
    assert s["recent_samples"] == 50
    assert s["recent_accuracy"] == pytest.approx(1.0)
    assert s["avg_conf_hit"] == pytest.approx(1.0)
    assert s["avg_conf_miss"] == pytest.approx(0.0)


def test_mixed_directions_with_drift_flip():
    n = 120
    idx = pd.date_range("2026-01-01", periods=n, freq="4h")
    close = np.concatenate([
        100.0 * (1 + 0.002 * np.arange(60)),
        100.0 * (1 + 0.002 * 59) * (1 - 0.002 * np.arange(60)),
    ])
    df = pd.DataFrame({
        "open": close * 0.999, "high": close * 1.002,
        "low": close * 0.998, "close": close,
        "volume": np.full(n, 1000.0),
    }, index=idx)
    probs = np.concatenate([
        np.tile([1.0, 0.0, 0.0], (60, 1)),   # SELL
        np.tile([0.0, 0.0, 1.0], (60, 1)),   # BUY
    ]).astype(np.float32)

    class Model:
        def predict(self, X, batch_size=1024, verbose=0):
            return probs

    out = evaluate_model(Model(), FakeScaler(), FEATURE_NAMES, {"TEST": df},
                         horizon=12)
    expected = [
        resolve_outcome("SELL" if i < 60 else "BUY",
                        float(close[i]), float(close[i + 12]))
        for i in range(len(close) - 12)
    ]
    assert list(out["outcome"]) == expected
    s = summarize(out)
    assert s["by_direction"]["SELL"]["accuracy"] == pytest.approx(
        sum(1 for e in expected[:60] if e == "hit") / 60.0)
    assert s["by_direction"]["BUY"]["accuracy"] == pytest.approx(
        sum(1 for e in expected[60:] if e == "hit") / 48.0)
