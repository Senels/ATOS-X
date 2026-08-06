"""Backtest AI kapisi simulasyonu testleri (TensorFlow gerektirmez).

Kapsar: `ai_blocked_mask` kurallari (yon uyumsuzlugu, HOLD, guven esigi),
motorun `ai_blocks` filtrelemesi, `signal_accuracy` isabet sayimi ve
`simulate` karsilastirmasi.
"""
import numpy as np
import pandas as pd
import pytest

from app.ai.backtest_sim import ai_blocked_mask, signal_accuracy, simulate
from app.ai.features import FEATURE_NAMES


class FakeScaler:
    def transform(self, X):
        return np.asarray(X)


class _FakeModel:
    """Sabit yon dagilimli prob matrisi; rows_per_dir her yonun kac bar surdugunu belirtir."""

    def __init__(self, rows_per_dir):
        self.rows_per_dir = rows_per_dir

    def predict(self, X, batch_size=1024, verbose=0):
        n = len(X)
        probs = np.zeros((n, 3), dtype=np.float32)
        i = 0
        for di, cnt in enumerate(self.rows_per_dir):
            probs[i:i + cnt, di] = 1.0
            i += cnt
        return probs


class _FakePredictor:
    def __init__(self, rows_per_dir, conf_scale=1.0):
        self.features = FEATURE_NAMES
        self.scaler = FakeScaler()
        self._model = _FakeModel(rows_per_dir)
        self.conf_scale = conf_scale

    @property
    def model(self):
        return self._model


def _df(n=100, drift=0.001):
    idx = pd.date_range("2026-01-01", periods=n, freq="4h")
    close = 100.0 * (1.0 + drift * np.arange(n))
    return pd.DataFrame({
        "open": close * 0.999, "high": close * 1.002,
        "low": close * 0.998, "close": close,
        "volume": np.full(n, 1000.0),
    }, index=idx)


# -- ai_blocked_mask ---------------------------------------------------------
def test_mask_blocks_mismatched_direction_and_hold():
    df = _df(n=60)
    sig = np.zeros(60, dtype=int)
    sig[5] = 1     # BUY vs SELL tahmini (0-19) -> engel
    sig[25] = -1   # SELL vs HOLD tahmini (20-39) -> engel
    sig[45] = 1    # BUY vs BUY tahmini (40-59) -> gecer
    sig[46] = 0    # sinyal yok -> gecer
    pred = _FakePredictor(rows_per_dir=[20, 20, 20])  # SELL, HOLD, BUY
    mask = ai_blocked_mask(pred, df, sig, threshold=0.5)
    assert bool(mask[5])
    assert bool(mask[25])
    assert not mask[45]
    assert not mask[46]


def test_mask_threshold_blocks_low_confidence():
    df = _df(n=60)
    sig = np.zeros(60, dtype=int)
    sig[0] = 1  # BUY
    pred = _FakePredictor(rows_per_dir=[0, 0, 60])  # hepsi BUY, conf 1.0
    assert not ai_blocked_mask(pred, df, sig, threshold=0.99)[0]
    assert bool(ai_blocked_mask(pred, df, sig, threshold=1.01)[0])


# -- motor ai_blocks ---------------------------------------------------------
def _orders(df, sig_pos, side=1):
    sig = np.zeros(len(df), dtype=int)
    sig[sig_pos] = side
    return pd.DataFrame({
        "signal": sig,
        "sl": np.full(len(df), 90.0),
        "tp": np.full(len(df), 130.0),
        "strength": np.full(len(df), np.inf),
    })


def test_engine_ai_blocks_skips_entry():
    from app.backtest.engine import BacktestEngine
    df = _df(n=60, drift=0.002)
    orders = _orders(df, sig_pos=5, side=1)
    eng = BacktestEngine(initial_equity=1000.0, risk_per_trade=0.01, fee_rate=0.0)
    base = eng.run(df, orders, "4h")
    assert base["total_trades"] == 1
    blocks = np.zeros(len(df), dtype=bool)
    blocks[5] = True  # sinyal barini engelle
    ai_res = eng.run(df, orders, "4h", ai_blocks=blocks)
    assert ai_res["total_trades"] == 0


# -- signal_accuracy ---------------------------------------------------------
def test_signal_accuracy_counts_hits_and_misses():
    df = _df(n=60, drift=0.001)  # yukselen piyasa -> BUY sinyalleri hit
    sig = np.zeros(60, dtype=int)
    sig[0] = 1   # BUY, gecen
    sig[10] = -1  # SELL, engellenen (dusmeyen piyasa -> miss)
    mask = np.zeros(60, dtype=bool)
    mask[10] = True
    st = signal_accuracy(df, sig, mask, horizon=12)
    assert st["signals"] == 2
    assert st["passed"] == 1 and st["passed_hits"] == 1
    assert st["blocked"] == 1 and st["blocked_misses"] == 1
    assert st["passed_accuracy"] == 1.0 and st["blocked_accuracy"] == 0.0


# -- simulate ----------------------------------------------------------------
def test_simulate_returns_comparison():
    from app.backtest.engine import BacktestEngine
    df = _df(n=60, drift=0.002)
    orders = _orders(df, sig_pos=5, side=1)
    pred = _FakePredictor(rows_per_dir=[0, 0, 60])  # hepsi BUY
    res = simulate(pred, df, orders, "4h", threshold=0.5,
                   engine_cls=BacktestEngine,
                   engine_kwargs={"initial_equity": 1000.0,
                                  "risk_per_trade": 0.01, "fee_rate": 0.0})
    assert res["baseline"]["total_trades"] == 1
    assert res["with_ai"]["total_trades"] == 1   # BUY tahmini, gecer
    assert res["signal_stats"]["passed"] == 1

    pred2 = _FakePredictor(rows_per_dir=[60, 0, 0])  # hepsi SELL -> engel
    res2 = simulate(pred2, df, orders, "4h", threshold=0.5,
                    engine_cls=BacktestEngine,
                    engine_kwargs={"initial_equity": 1000.0,
                                   "risk_per_trade": 0.01, "fee_rate": 0.0})
    assert res2["with_ai"]["total_trades"] == 0
    assert res2["signal_stats"]["blocked"] == 1


def test_summarize_scan_aggregates():
    from app.ai.backtest_sim import summarize_scan
    rows = [{
        "signal_stats": {"signals": 10, "blocked": 2, "passed": 8,
                         "blocked_hits": 0, "blocked_misses": 2,
                         "passed_hits": 5, "passed_misses": 3},
        "base_trades": 10, "ai_trades": 8,
        "base_wins": 6, "ai_wins": 6,
        "base_net": 50.0, "ai_net": 60.0,
    }]
    agg = summarize_scan(rows)
    assert agg["base_trades"] == 10 and agg["ai_trades"] == 8
    assert agg["base_win_rate"] == 60.0
    assert agg["ai_win_rate"] == 75.0
    assert agg["base_net"] == 50.0 and agg["ai_net"] == 60.0
    assert agg["blocked_accuracy"] == 0.0
    assert agg["passed_accuracy"] == pytest.approx(5 / 8)
