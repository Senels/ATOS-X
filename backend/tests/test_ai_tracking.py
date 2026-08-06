"""AI tahmin izleme (feedback dongusu) testleri.

Kapsar: predictions DB tablosu, bar-bazli outcome cozumleme, AI istatistikleri,
/api/v1/signals AI alanlari ve /koruma AI anahtarlari. TensorFlow gerektirmez.
"""
import sqlite3
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import main as main_mod
from app.core.database import Database


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


# -- DB: predictions tablosu -------------------------------------------------
def test_save_list_and_resolve_predictions(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.save_prediction("BTCUSDT", "BUY", 100.0, ai_direction="BUY",
                       ai_confidence=0.8, strength=0.6,
                       bar_ts="2024-01-01 00:00:00+00:00", executed=True)
    db.save_prediction("ETHUSDT", "SELL", 50.0, ai_direction="SELL", ai_confidence=0.7)
    db.save_prediction("SOLUSDT", "BUY", 10.0, ai_direction="HOLD", ai_confidence=0.5)

    pending = db.list_pending_predictions()
    assert len(pending) == 3
    assert pending[0]["symbol"] == "BTCUSDT"
    assert pending[0]["ai_direction"] == "BUY"
    assert pending[0]["bar_ts"] == "2024-01-01 00:00:00+00:00"

    db.resolve_prediction(pending[0]["id"], "hit")
    db.resolve_prediction(pending[1]["id"], "miss")
    db.resolve_prediction(pending[2]["id"], "na")

    stats = db.ai_stats()
    assert stats["total"] == 3
    assert stats["resolved"] == 2
    assert stats["pending"] == 0
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["accuracy"] == 0.5
    assert stats["executed"] == 1
    assert stats["avg_confidence"] == round((0.8 + 0.7 + 0.5) / 3, 4)
    assert stats["by_direction"]["BUY"]["total"] == 1
    assert stats["by_direction"]["BUY"]["hits"] == 1
    assert stats["by_direction"]["SELL"]["accuracy"] == 0.0


def test_resolve_stale_predictions(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.save_prediction("BTCUSDT", "BUY", 100.0, ai_direction="BUY")
    conn = sqlite3.connect(db.db_path)
    conn.execute("UPDATE predictions SET created_at = datetime('now', '-10 days')")
    conn.commit()
    conn.close()
    db.resolve_stale_predictions(days=7)
    stats = db.ai_stats()
    assert stats["pending"] == 0
    assert stats["total"] == 1
    assert stats["resolved"] == 0


def test_save_prediction_dedup_same_bar(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.save_prediction("BTCUSDT", "BUY", 100.0, ai_direction="BUY",
                       bar_ts="2024-01-01 00:00:00+00:00", executed=False)
    db.save_prediction("BTCUSDT", "BUY", 100.0, ai_direction="BUY",
                       bar_ts="2024-01-01 00:00:00+00:00", executed=False)
    pending = db.list_pending_predictions()
    assert len(pending) == 1


def test_save_prediction_dedup_upgrades_executed(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.save_prediction("BTCUSDT", "BUY", 100.0, ai_direction="BUY",
                       bar_ts="2024-01-01 00:00:00+00:00", executed=False)
    db.save_prediction("BTCUSDT", "BUY", 100.0, ai_direction="BUY",
                       bar_ts="2024-01-01 00:00:00+00:00", executed=True)
    pending = db.list_pending_predictions()
    assert len(pending) == 1
    assert pending[0]["executed"] == 1


# -- Outcome cozumleme -------------------------------------------------------
class _FakeBinance:
    async def connect(self):
        return True

    async def get_all_tickers(self):
        return {}

    async def load_all_symbols(self):
        return []


@pytest.fixture
def trader(tmp_path, monkeypatch):
    from app.strategy import auto_trader as at_mod
    from app.strategy.auto_trader import AutoTrader
    db = Database(str(tmp_path / "at.db"))
    monkeypatch.setattr(at_mod, "Database", lambda *a, **k: db)
    return AutoTrader(_FakeBinance()), db


def test_record_prediction_writes_db(trader):
    t, db = trader
    sig = {"signal": "BUY", "price": 100.0, "strength": 0.8, "bar_ts": "x"}
    t._record_prediction("BTCUSDT", sig, {"confidence": 0.9},
                         {"direction": "BUY", "confidence": 0.8}, executed=True)
    t._record_prediction("ETHUSDT", {"signal": "SELL", "price": 50.0},
                         None, None)
    pending = db.list_pending_predictions()
    assert len(pending) == 2
    assert pending[0]["ai_direction"] == "BUY"
    assert pending[0]["ai_confidence"] == 0.8
    assert pending[1]["ai_direction"] is None  # AI kapali/onceki model yok


def test_gate_and_record_records_even_when_council_blocks(trader, monkeypatch):
    t, db = trader
    sig = {"signal": "BUY", "price": 100.0, "strength": 0.9, "bar_ts": "bar-1"}
    klines = _df(n=60, seed=2)
    monkeypatch.setattr(t, "_council_gate",
                        lambda *a, **k: (False, {"verdict": "HOLD", "confidence": 0.06}))
    monkeypatch.setattr(t, "_strength_gate", lambda *a, **k: (True, None))
    monkeypatch.setattr(t, "_ai_gate", lambda *a, **k: (True, {"direction": "BUY", "confidence": 0.8}))
    allow_ai, ai_info, allow, decision, allow_str, str_info = \
        t._gate_and_record("BTCUSDT", sig, klines, {})
    assert allow is False and allow_str is True and allow_ai is True
    pending = db.list_pending_predictions()
    assert len(pending) == 1
    assert pending[0]["ai_direction"] == "BUY"
    assert pending[0]["executed"] == 0  # council engelledi -> executed 0


def test_gate_and_record_executed_when_all_gates_pass(trader, monkeypatch):
    t, db = trader
    sig = {"signal": "SELL", "price": 50.0, "strength": 0.8, "bar_ts": "bar-2"}
    klines = _df(n=60, seed=3)
    monkeypatch.setattr(t, "_council_gate",
                        lambda *a, **k: (True, {"verdict": "SELL", "confidence": 0.7}))
    monkeypatch.setattr(t, "_strength_gate", lambda *a, **k: (True, None))
    monkeypatch.setattr(t, "_ai_gate", lambda *a, **k: (True, {"direction": "SELL", "confidence": 0.9}))
    allow_ai, ai_info, allow, decision, allow_str, str_info = \
        t._gate_and_record("BTCUSDT", sig, klines, {})
    assert allow and allow_str and allow_ai
    pending = db.list_pending_predictions()
    assert len(pending) == 1
    assert pending[0]["executed"] == 1


def test_gate_and_record_dedup_same_bar(trader, monkeypatch):
    t, db = trader
    sig = {"signal": "BUY", "price": 100.0, "strength": 0.9, "bar_ts": "bar-3"}
    klines = _df(n=60, seed=4)
    monkeypatch.setattr(t, "_council_gate", lambda *a, **k: (False, {"verdict": "HOLD"}))
    monkeypatch.setattr(t, "_strength_gate", lambda *a, **k: (True, None))
    monkeypatch.setattr(t, "_ai_gate", lambda *a, **k: (True, {"direction": "BUY"}))
    t._gate_and_record("BTCUSDT", sig, klines, {})
    t._gate_and_record("BTCUSDT", sig, klines, {})
    assert len(db.list_pending_predictions()) == 1


def test_resolve_outcome_buy_hit_sell_miss(trader):
    t, db = trader
    df = _df(n=60, seed=1, drift=0.005)
    bar_ts = str(df.index[30])
    db.save_prediction("BTCUSDT", "BUY", float(df["close"].iloc[30]),
                       ai_direction="BUY", bar_ts=bar_ts)
    db.save_prediction("ETHUSDT", "SELL", float(df["close"].iloc[30]),
                       ai_direction="SELL", bar_ts=bar_ts)
    t._resolve_pending_predictions({"BTCUSDT": df, "ETHUSDT": df})
    stats = db.ai_stats()
    assert stats["resolved"] == 2
    assert stats["hits"] == 1  # BUY yukseldi -> hit; SELL yukseldi -> miss
    assert stats["misses"] == 1


def test_resolve_outcome_insufficient_bars_stays_pending(trader):
    t, db = trader
    df = _df(n=40, seed=3)
    bar_ts = str(df.index[35])
    db.save_prediction("BTCUSDT", "BUY", 100.0, ai_direction="BUY", bar_ts=bar_ts)
    t._resolve_pending_predictions({"BTCUSDT": df})
    assert len(db.list_pending_predictions()) == 1


def test_resolve_outcome_missing_bar_marked_na(trader):
    t, db = trader
    df = _df(n=60, seed=4)
    db.save_prediction("BTCUSDT", "BUY", 100.0, ai_direction="BUY",
                       bar_ts="2020-01-01 00:00:00+00:00")
    t._resolve_pending_predictions({"BTCUSDT": df})
    stats = db.ai_stats()
    assert stats["resolved"] == 0
    assert stats["total"] == 1  # 'na' olarak kapatildi


# -- /api/v1/signals AI alanlari --------------------------------------------
class _StubPredictor:
    def __init__(self, direction="BUY", confidence=0.9):
        self.direction = direction
        self.confidence = confidence

    def predict(self, df):
        return {"direction": self.direction, "confidence": self.confidence,
                "probabilities": [0.1, 0.1, 0.8], "loaded": True}


def test_live_signals_ai_fields():
    fake = SimpleNamespace(
        priority=["BTCUSDT"], trading_symbols=["BTCUSDT"],
        _ai_predictor=lambda: _StubPredictor("BUY", 0.9),
    )
    main_mod.auto_trader = fake

    async def _fake_get_klines(symbol, interval, limit):
        return _df(n=400, seed=7)

    try:
        client = TestClient(main_mod.app)
        main_mod.app.state.binance = SimpleNamespace(get_klines=_fake_get_klines)
        resp = client.get("/api/v1/signals?limit=5")
        client.close()
    finally:
        main_mod.auto_trader = None
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] >= 1
    s = body["signals"][0]
    assert s["symbol"] == "BTCUSDT"
    assert s["ai_direction"] == "BUY"
    assert s["ai_confidence"] == 0.9


def test_live_signals_ai_fields_null_without_predictor():
    fake = SimpleNamespace(
        priority=["BTCUSDT"], trading_symbols=["BTCUSDT"],
        _ai_predictor=lambda: None,
    )
    main_mod.auto_trader = fake

    async def _fake_get_klines(symbol, interval, limit):
        return _df(n=400, seed=7)

    try:
        client = TestClient(main_mod.app)
        main_mod.app.state.binance = SimpleNamespace(get_klines=_fake_get_klines)
        resp = client.get("/api/v1/signals?limit=5")
        client.close()
    finally:
        main_mod.auto_trader = None
    assert resp.status_code == 200
    s = resp.json()["signals"][0]
    assert "ai_direction" in s
    assert s["ai_direction"] is None


# -- /koruma AI anahtarlari --------------------------------------------------
def test_koruma_editor_contains_ai_keys():
    assert "use_ai_model" in main_mod._EDITABLE_RISK_KEYS
    assert "ai_min_confidence" in main_mod._EDITABLE_RISK_KEYS
    assert "ai_model_path" in main_mod._EDITABLE_RISK_KEYS
    assert "use_ai_model" in main_mod._BOOL_RISK_KEYS
    assert "ai_model_path" in main_mod._STR_RISK_KEYS


def test_koruma_sets_ai_boolean_and_str(monkeypatch):
    updates = {}
    monkeypatch.setattr(main_mod.strat_settings, "update_settings",
                        lambda d: updates.update(d))
    monkeypatch.setattr(main_mod.strat_settings, "persist", lambda: None)
    main_mod.auto_trader = None

    main_mod._telegram_command("/koruma use_ai_model 0")
    assert updates.get("use_ai_model") is False

    main_mod._telegram_command("/koruma ai_min_confidence 0.6")
    assert updates.get("ai_min_confidence") == 0.6

    main_mod._telegram_command("/koruma ai_model_path ai_direction_v2")
    assert updates.get("ai_model_path") == "ai_direction_v2"


def test_koruma_format_includes_ai_line():
    s = main_mod.strat_settings.get_settings()
    s["use_ai_model"] = True
    s["ai_min_confidence"] = 0.55
    s["ai_model_path"] = "ai_direction"
    out = main_mod._format_koruma()
    assert "AI tahmini: acik" in out
    assert "Min guven: %55" in out
    assert "ai_direction" in out


# -- /ai Telegram komutu -----------------------------------------------------
def test_command_ai_stats(monkeypatch):
    fake = SimpleNamespace(
        db=SimpleNamespace(ai_stats=lambda: {
            "total": 10, "resolved": 8, "pending": 2, "hits": 6, "misses": 2,
            "accuracy": 0.75, "executed": 3, "avg_confidence": 0.61,
            "by_direction": {
                "BUY": {"total": 5, "hits": 4, "accuracy": 0.8, "avg_confidence": 0.6},
            },
        }),
    )
    monkeypatch.setattr(main_mod, "auto_trader", fake)
    reply = main_mod._telegram_command("/ai")
    assert "AI tahmin istatistikleri" in reply
    assert "%75" in reply
    assert "BUY: %80" in reply


def test_command_ai_stats_no_resolved(monkeypatch):
    fake = SimpleNamespace(
        db=SimpleNamespace(ai_stats=lambda: {
            "total": 5, "resolved": 0, "pending": 5, "hits": 0, "misses": 0,
            "accuracy": 0.0, "executed": 0, "avg_confidence": 0.0,
            "by_direction": {},
        }),
    )
    monkeypatch.setattr(main_mod, "auto_trader", fake)
    reply = main_mod._telegram_command("/ai")
    assert "Henuz cozumlenmis tahmin yok" in reply


# -- Dashboard AI Feedback karti --------------------------------------------
def test_dashboard_has_ai_feedback_card():
    client = TestClient(main_mod.app)
    resp = client.get("/dashboard/html")
    assert resp.status_code == 200
    assert "AI Feedback" in resp.text
    assert "loadAIStats" in resp.text
    assert 'id="aiStatsBody"' in resp.text
    assert "/api/v1/ai/stats" in resp.text
    client.close()
