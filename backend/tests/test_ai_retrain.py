"""Otomatik AI yeniden egitim testleri (TensorFlow gerektirmez).

Kapsar: tetikleyici mantigi (zaman/accuracy), model dizini mtime okuma,
`_maybe_retrain_ai` kapilari ve `_run_retrain` arka plan gorevi (cache
gecersizlesme + Telegram bildirimi + concurrency guard).
"""
import asyncio
import time

import pytest

from app.ai import retrain as retrain_mod
from app.ai.retrain import accuracy_trigger, retrain_due
from app.core.database import Database


# -- Saf mantik --------------------------------------------------------------
def test_retrain_due_logic():
    now = time.time()
    assert retrain_due(None, now, 24.0) is True          # hic egitim yok
    assert retrain_due(now - 3600, now, 24.0) is False   # taze
    assert retrain_due(now - 25 * 3600, now, 24.0) is True  # interval gecti
    assert retrain_due(now - 25 * 3600, now, 0.0) is True


def test_accuracy_trigger_logic():
    now = time.time()
    last_old = now - 7 * 3600
    last_fresh = now - 3600
    # Yetersiz ornek -> tetiklemez
    assert accuracy_trigger(10, 0.4, 30, 0.55, last_old, now) is False
    # Accuracy iyi -> tetiklemez
    assert accuracy_trigger(40, 0.6, 30, 0.55, last_old, now) is False
    # Dusuk accuracy + yeterli ornek + soguma gecti -> tetikler
    assert accuracy_trigger(40, 0.4, 30, 0.55, last_old, now) is True
    # Soguma gecmedi -> tetiklemez
    assert accuracy_trigger(40, 0.4, 30, 0.55, last_fresh, now) is False
    # Model yok -> aninda tetikler
    assert accuracy_trigger(40, 0.4, 30, 0.55, None, now) is True


def test_last_trained_at(tmp_path, monkeypatch):
    monkeypatch.setattr(retrain_mod.m, "_MODEL_ROOT", tmp_path)
    assert retrain_mod.last_trained_at("x") is None
    d = tmp_path / "x"
    d.mkdir()
    (d / "model.keras").write_bytes(b"")
    import os
    os.utime(d / "model.keras", (100.0, 100.0))
    assert retrain_mod.last_trained_at("x") == 100.0


# -- AutoTrader entegrasyonu -------------------------------------------------
class _FakeBinance:
    async def connect(self):
        return True

    async def get_all_tickers(self):
        return {}

    async def load_all_symbols(self):
        return []


class _FakeTelegram:
    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(message)


@pytest.fixture
def trader(tmp_path, monkeypatch):
    from app.strategy import auto_trader as at_mod
    from app.strategy.auto_trader import AutoTrader
    db = Database(str(tmp_path / "at.db"))
    monkeypatch.setattr(at_mod, "Database", lambda *a, **k: db)
    tg = _FakeTelegram()
    t = AutoTrader(_FakeBinance(), telegram=tg)
    t.db = db
    return t, db, tg, at_mod


def _settings(**over):
    s = {
        "ai_auto_retrain": True,
        "ai_model_path": "ai_direction",
        "ai_retrain_interval_hours": 24.0,
        "ai_retrain_min_acc": 0.55,
        "ai_retrain_min_samples": 30,
        "ai_retrain_symbols": 400,
        "ai_retrain_epochs": 30,
    }
    s.update(over)
    return s


def test_maybe_retrain_auto_retrain_off(trader, monkeypatch):
    t, db, tg, at_mod = trader
    monkeypatch.setattr(at_mod.strat_settings, "get_settings",
                        lambda: _settings(ai_auto_retrain=False))
    captured = []
    monkeypatch.setattr(asyncio, "create_task", lambda coro, **k: captured.append(coro))
    t._maybe_retrain_ai(now=time.time())
    assert captured == []


def test_maybe_retrain_time_based_trigger(trader, monkeypatch):
    t, db, tg, at_mod = trader
    monkeypatch.setattr(at_mod.strat_settings, "get_settings",
                        lambda: _settings())
    monkeypatch.setattr(retrain_mod, "last_trained_at", lambda name: None)
    captured = []
    monkeypatch.setattr(asyncio, "create_task", lambda coro, **k: captured.append(coro))
    t._maybe_retrain_ai(now=time.time())
    assert len(captured) == 1
    captured[0].close()
    assert t._retrain_running is True


def test_maybe_retrain_time_gate(trader, monkeypatch):
    t, db, tg, at_mod = trader
    monkeypatch.setattr(at_mod.strat_settings, "get_settings",
                        lambda: _settings())
    monkeypatch.setattr(retrain_mod, "last_trained_at", lambda name: None)
    captured = []
    monkeypatch.setattr(asyncio, "create_task", lambda coro, **k: captured.append(coro))
    now = time.time()
    t._maybe_retrain_ai(now=now)
    assert len(captured) == 1
    t._retrain_running = False          # arka plan gorevi tamamlanmis gibi
    t._maybe_retrain_ai(now=now + 600)   # 10 dk -> time gate
    assert len(captured) == 1
    t._maybe_retrain_ai(now=now + 901)   # 15 dk -> yeniden degerlendirir
    assert len(captured) == 2
    for coro in captured:
        coro.close()


def test_maybe_retrain_accuracy_trigger(trader, monkeypatch):
    t, db, tg, at_mod = trader
    t._ai_predictor_cache = False
    monkeypatch.setattr(at_mod.strat_settings, "get_settings",
                        lambda: _settings(ai_retrain_interval_hours=0.0))
    monkeypatch.setattr(retrain_mod, "last_trained_at",
                        lambda name: time.time() - 7 * 3600)
    t.db.ai_stats = lambda **k: {"resolved": 40, "accuracy": 0.4}
    captured = []
    monkeypatch.setattr(asyncio, "create_task", lambda coro, **k: captured.append(coro))
    t._maybe_retrain_ai(now=time.time())
    assert len(captured) == 1
    captured[0].close()


def test_run_retrain_success_invalidates_cache_and_notifies(trader, monkeypatch):
    t, db, tg, at_mod = trader
    t._ai_predictor_cache = False

    class FakeRunner:
        def __init__(self, model_name="ai_direction"):
            self.model_name = model_name

        async def train(self, symbols=400, epochs=30, horizon=24, atr_mult=1.0):
            return True, "val_acc: 0.630"

    monkeypatch.setattr(retrain_mod, "RetrainRunner", FakeRunner)
    asyncio.run(t._run_retrain(_settings(), "ai_direction"))
    assert t._ai_predictor_cache is None          # yeni model sonraki tahminde yuklenecek
    assert t._retrain_running is False
    joined = "\n".join(tg.sent)
    assert "basladi" in joined
    assert "yeniden egitildi" in joined
    assert "0.630" in joined


def test_run_retrain_failure_notifies(trader, monkeypatch):
    t, db, tg, at_mod = trader

    class FakeRunner:
        def __init__(self, model_name="ai_direction"):
            self.model_name = model_name

        async def train(self, symbols=400, epochs=30, horizon=24, atr_mult=1.0):
            return False, "zaman asimi"

    monkeypatch.setattr(retrain_mod, "RetrainRunner", FakeRunner)
    asyncio.run(t._run_retrain(_settings(), "ai_direction"))
    assert t._retrain_running is False
    assert any("BASARISIZ" in m for m in tg.sent)
