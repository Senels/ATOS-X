import asyncio
from types import SimpleNamespace

import numpy as np
import pandas as pd

from app import main as main_mod
from app.notifications.telegram import TelegramNotifier, _process_updates, format_stop_summary


def _signal_df(n=120):
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0, 0.3, n))
    high = close + 0.4
    low = close - 0.4
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    vol = rng.uniform(50, 300, n)
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": vol})


class _FakeKlines:
    def __init__(self, df):
        self.df = df
        self.calls = []

    async def get_klines(self, symbol, interval, limit):
        self.calls.append((symbol, interval, limit))
        return self.df


class _FakeDB:
    def get_closed_trades_since(self, days=1):
        return []


class _FakeTrader:
    def __init__(self):
        self.equity = 10000.0
        self.max_position_pct = 75.0
        self.max_side_pct = 150.0
        self.max_drawdown_pct = 20.0
        self.drawdown_pct = 0.0
        self.risk_halted = False
        self.running = True
        self.loss_halted = False
        self.consecutive_losses = 0
        self.max_consecutive_losses = 5
        self.daily_loss_halted = False
        self.day_pnl = 0.0
        self.equity_halted = False
        self.min_equity = 0.0
        self.live_prices = {}
        self.db = _FakeDB()
        self.top_symbols = ["BTCUSDT", "ETHUSDT"]
        self.trade_history = []
        self.risk_events = [{"time": "2026-08-03T10:00:00", "type": "block_add",
                             "message": "Engel: side:LONG"}]
        self._conc_blocks = {"side:LONG"}
        self.active_positions = {
            "BTCUSDT": {"side": "BUY", "entry_price": 65000.0, "quantity": 0.5,
                        "sl_order_id": "SL_1", "tp_order_id": "TP_1"},
            "ETHUSDT": {"side": "SELL", "entry_price": 3000.0, "quantity": 2.0,
                        "sl_order_id": None, "tp_order_id": None},
        }

    async def stop(self):
        self.running = False

    async def start(self):
        self.running = True

    async def close_position(self, symbol, price, reason):
        self.closed = (symbol, price, reason)

    async def close_all(self, reason="manual_close_all"):
        self.closed_all = reason

    async def update_sl(self, symbol, new_sl):
        if symbol not in self.active_positions:
            return {"ok": False, "error": "position_not_found"}
        return {"ok": True, "symbol": symbol, "old_sl": 0.0, "new_sl": new_sl}

    async def update_tp(self, symbol, new_tp):
        if symbol not in self.active_positions:
            return {"ok": False, "error": "position_not_found"}
        return {"ok": True, "symbol": symbol, "old_tp": 0.0, "new_tp": new_tp}

    def _apply_risk_settings(self, s):
        self.applied_settings = s


def test_process_updates_filters_and_offsets():
    calls = []

    def handler(text):
        calls.append(text)
        if text.startswith("/"):
            return "reply:" + text
        return None

    updates = [
        {"update_id": 1, "message": {"text": "/durum"}},
        {"update_id": 2, "message": {"text": "plain message"}},
        {"update_id": 3, "message": {}},
    ]
    offset, replies = _process_updates(updates, handler)
    assert offset == 4
    assert replies == ["reply:/durum"]
    assert calls == ["/durum", "plain message"]


def test_process_updates_empty():
    offset, replies = _process_updates([], lambda t: "x")
    assert offset == 0
    assert replies == []


def test_command_status():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/durum")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "Equity" in reply
    assert "Engeller" in reply
    assert "side:LONG" in reply
    assert "Drawdown" in reply


def test_command_status_shows_halt():
    fake = _FakeTrader()
    fake.risk_halted = True
    fake.drawdown_pct = 25.5
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/durum")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "AKTIF" in reply
    assert "%25.5" in reply
    assert "Risk olayi" in reply
    assert "block_add" in reply


def test_command_blocks():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/blok")
    finally:
        main_mod.auto_trader = None
    assert reply == "ATOS X aktif engeller: side:LONG"


def test_command_blocks_empty():
    fake = _FakeTrader()
    fake._conc_blocks = set()
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/blok")
    finally:
        main_mod.auto_trader = None
    assert reply == "ATOS X aktif engeller: yok"


def test_command_positions_shows_protection():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/pozisyon")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "BTCUSDT" in reply and "korumali" in reply
    assert "ETHUSDT" in reply and "KORUMASIZ" in reply


def test_command_positions_shows_unrealized_pnl():
    fake = _FakeTrader()
    fake.live_prices = {"BTCUSDT": 70000.0}
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/pozisyon")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "PnL:" in reply
    assert "+" in reply


def test_command_help():
    reply = main_mod._telegram_command("/yardim")
    assert reply is not None
    assert "/durum" in reply and "/blok" in reply


def test_command_unknown_returns_none():
    assert main_mod._telegram_command("merhaba") is None
    assert main_mod._telegram_command("/bilinmeyen") is None


def test_command_close_schedules_close(monkeypatch):
    fake = _FakeTrader()
    fake.live_prices = {"BTCUSDT": 66000.0}
    main_mod.auto_trader = fake
    monkeypatch.setattr(main_mod, "_run_later", lambda coro: True)
    try:
        reply = main_mod._telegram_command("/kapat BTCUSDT")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "kapatiliyor" in reply and "BTCUSDT" in reply


def test_command_close_no_position():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/kapat DOGEUSDT")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "pozisyon yok" in reply


def test_command_close_missing_price():
    fake = _FakeTrader()
    fake.live_prices = {}
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/kapat BTCUSDT")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "fiyati bulunamadi" in reply


def test_command_close_bad_usage():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/kapat")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "kullanim" in reply


def test_command_stop_schedules_stop(monkeypatch):
    fake = _FakeTrader()
    main_mod.auto_trader = fake
    monkeypatch.setattr(main_mod, "_run_later", lambda coro: True)
    try:
        reply = main_mod._telegram_command("/durdur")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "DURDURULDU" in reply


def test_command_stop_when_not_running():
    fake = _FakeTrader()
    fake.running = False
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/durdur")
    finally:
        main_mod.auto_trader = None
    assert "zaten durdurulmus" in reply


def test_command_resume_schedules_start(monkeypatch):
    fake = _FakeTrader()
    fake.running = False
    main_mod.auto_trader = fake
    monkeypatch.setattr(main_mod, "_run_later", lambda coro: True)
    try:
        reply = main_mod._telegram_command("/ac")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "yeniden baslatiliyor" in reply


def test_command_resume_when_running():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/ac")
    finally:
        main_mod.auto_trader = None
    assert "zaten calisiyor" in reply


def test_command_status_shows_trading_state():
    fake = _FakeTrader()
    fake.running = False
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/durum")
    finally:
        main_mod.auto_trader = None
    assert "DURDURULDU" in reply


def test_listener_disabled_returns_none():
    nt = TelegramNotifier.__new__(TelegramNotifier)
    nt.enabled = False
    assert nt.start_listener(lambda t: None) is None


def test_format_stop_summary():
    closed = [
        {"symbol": "BTCUSDT", "pnl": 120.0},
        {"symbol": "ETHUSDT", "pnl": -50.0},
    ]
    msg = format_stop_summary(closed)
    assert "Kapanan pozisyon: 2" in msg
    assert "Kar: 1 / Zarar: 1" in msg
    assert "+70.00" in msg
    assert "BTCUSDT" in msg and "ETHUSDT" in msg


def test_format_stop_summary_empty():
    msg = format_stop_summary([])
    assert "Kapanan pozisyon: 0" in msg
    assert "Kar: 0 / Zarar: 0" in msg


def test_command_report_schedules_daily_summary(monkeypatch):
    fake = _FakeTrader()
    main_mod.auto_trader = fake

    def fake_run_later(coro):
        coro.close()
        return True

    monkeypatch.setattr(main_mod, "_run_later", fake_run_later)
    try:
        reply = main_mod._telegram_command("/rapor")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "gunluk rapor gonderiliyor" in reply


def test_command_report_requires_trader():
    main_mod.auto_trader = None
    assert "motor calismiyor" in main_mod._telegram_command("/rapor")


def test_command_risk():
    fake = _FakeTrader()
    fake.day_pnl = -25.5
    fake.drawdown_pct = 3.5
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/risk")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "Equity" in reply
    assert "Maruziyet" in reply
    assert "Drawdown" in reply
    assert "Risk olayi" in reply
    assert "side:LONG" in reply


def test_command_risk_shows_halts():
    fake = _FakeTrader()
    fake.risk_halted = True
    fake.loss_halted = True
    fake.daily_loss_halted = True
    fake.equity_halted = True
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/risk")
    finally:
        main_mod.auto_trader = None
    assert reply.count("AKTIF") == 4


def test_command_history_empty():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/gecmis")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "kapanis gecmisi yok" in reply


def test_command_history_lists_trades():
    fake = _FakeTrader()
    fake.trade_history = [
        {"symbol": "BTCUSDT", "side": "BUY", "pnl": 120.0, "reason": "take_profit"},
        {"symbol": "ETHUSDT", "side": "SELL", "pnl": -50.0, "reason": "stop_loss"},
    ]
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/gecmis")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "son islemler" in reply
    assert "BTCUSDT" in reply and "ETHUSDT" in reply
    assert "+120.00" in reply and "-50.00" in reply


def test_command_history_bad_n():
    fake = _FakeTrader()
    fake.trade_history = [{"symbol": "BTCUSDT", "side": "BUY", "pnl": 1.0, "reason": "x"}]
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/gecmis abc")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "kullanim" in reply


def test_command_signal_bad_usage():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/sinyal")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "kullanim" in reply


def test_command_signal_schedules(monkeypatch):
    main_mod.auto_trader = _FakeTrader()

    def fake_run_later(coro):
        coro.close()
        return True

    monkeypatch.setattr(main_mod, "_run_later", fake_run_later)
    try:
        reply = main_mod._telegram_command("/sinyal BTCUSDT")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "BTCUSDT" in reply and "hesaplaniyor" in reply


def test_command_close_all_schedules(monkeypatch):
    main_mod.auto_trader = _FakeTrader()

    def fake_run_later(coro):
        coro.close()
        return True

    monkeypatch.setattr(main_mod, "_run_later", fake_run_later)
    try:
        reply = main_mod._telegram_command("/kapatall")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "2 pozisyon kapatiliyor" in reply


def test_command_close_all_no_positions():
    fake = _FakeTrader()
    fake.active_positions = {}
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/kapatall")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "pozisyon yok" in reply


def test_command_sl_bad_usage():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/sl")
        reply2 = main_mod._telegram_command("/sl BTCUSDT")
    finally:
        main_mod.auto_trader = None
    assert reply is not None and "kullanim" in reply
    assert reply2 is not None and "kullanim" in reply2


def test_command_sl_invalid_price():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/sl BTCUSDT abc")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "gecersiz SL fiyati" in reply


def test_command_sl_no_position():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/sl XRPUSDT 64000")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "acik pozisyon yok" in reply


def test_command_sl_schedules(monkeypatch):
    main_mod.auto_trader = _FakeTrader()

    def fake_run_later(coro):
        coro.close()
        return True

    monkeypatch.setattr(main_mod, "_run_later", fake_run_later)
    try:
        reply = main_mod._telegram_command("/sl BTCUSDT 64000")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "guncelleniyor" in reply and "64000" in reply


def test_set_sl_success(monkeypatch):
    main_mod.auto_trader = _FakeTrader()
    messages = []

    async def fake_send(message):
        messages.append(message)

    monkeypatch.setattr(main_mod.telegram, "send", fake_send)
    asyncio.run(main_mod._set_sl("BTCUSDT", 64000.0))
    assert messages and "SL guncellendi" in messages[0]


def test_set_sl_rejected(monkeypatch):
    main_mod.auto_trader = _FakeTrader()
    messages = []

    async def fake_send(message):
        messages.append(message)

    monkeypatch.setattr(main_mod.telegram, "send", fake_send)
    asyncio.run(main_mod._set_sl("XRPUSDT", 64000.0))
    assert messages and "acik pozisyon yok" in messages[0]


def test_command_tp_bad_usage():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/tp BTCUSDT")
    finally:
        main_mod.auto_trader = None
    assert reply is not None and "kullanim" in reply


def test_command_tp_no_position():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/tp XRPUSDT 70000")
    finally:
        main_mod.auto_trader = None
    assert reply is not None and "acik pozisyon yok" in reply


def test_command_tp_schedules(monkeypatch):
    main_mod.auto_trader = _FakeTrader()

    def fake_run_later(coro):
        coro.close()
        return True

    monkeypatch.setattr(main_mod, "_run_later", fake_run_later)
    try:
        reply = main_mod._telegram_command("/tp BTCUSDT 69000")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "guncelleniyor" in reply and "69000" in reply


def test_set_tp_success(monkeypatch):
    main_mod.auto_trader = _FakeTrader()
    messages = []

    async def fake_send(message):
        messages.append(message)

    monkeypatch.setattr(main_mod.telegram, "send", fake_send)
    asyncio.run(main_mod._set_tp("BTCUSDT", 69000.0))
    assert messages and "TP guncellendi" in messages[0]


def test_command_koruma_view(monkeypatch):
    fake = _FakeTrader()
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/koruma")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "risk ayarlari" in reply
    assert "max_drawdown_pct" in reply


def test_command_koruma_set(monkeypatch):
    fake = _FakeTrader()
    main_mod.auto_trader = fake
    applied = {}
    monkeypatch.setattr(main_mod.strat_settings, "update_settings",
                        lambda patch: applied.update(patch) or main_mod.strat_settings.get_settings())
    persisted = []
    monkeypatch.setattr(main_mod.strat_settings, "persist", lambda: persisted.append(True) or {})
    try:
        reply = main_mod._telegram_command("/koruma max_drawdown_pct 15")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "max_drawdown_pct = 15.0" in reply
    assert applied["max_drawdown_pct"] == 15.0
    assert persisted == [True]
    assert fake.applied_settings is not None


def test_command_koruma_set_int(monkeypatch):
    fake = _FakeTrader()
    main_mod.auto_trader = fake
    applied = {}
    monkeypatch.setattr(main_mod.strat_settings, "update_settings",
                        lambda patch: applied.update(patch) or main_mod.strat_settings.get_settings())
    monkeypatch.setattr(main_mod.strat_settings, "persist", lambda: {})
    try:
        reply = main_mod._telegram_command("/koruma max_positions 5")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "max_open_positions = 5" in reply
    assert applied["max_open_positions"] == 5


def test_command_koruma_set_bool_toggle(monkeypatch):
    fake = _FakeTrader()
    main_mod.auto_trader = fake
    applied = {}
    monkeypatch.setattr(main_mod.strat_settings, "update_settings",
                        lambda patch: applied.update(patch) or main_mod.strat_settings.get_settings())
    monkeypatch.setattr(main_mod.strat_settings, "persist", lambda: {})
    try:
        reply = main_mod._telegram_command("/koruma use_decision_council 1")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert applied["use_decision_council"] is True


def test_command_koruma_set_confidence(monkeypatch):
    fake = _FakeTrader()
    main_mod.auto_trader = fake
    applied = {}
    monkeypatch.setattr(main_mod.strat_settings, "update_settings",
                        lambda patch: applied.update(patch) or main_mod.strat_settings.get_settings())
    monkeypatch.setattr(main_mod.strat_settings, "persist", lambda: {})
    try:
        reply = main_mod._telegram_command("/koruma council_min_confidence 0.7")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert applied["council_min_confidence"] == 0.7


def test_command_koruma_view_shows_council(monkeypatch):
    fake = _FakeTrader()
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/koruma")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "Decision Council" in reply


def test_command_koruma_set_score_ranking(monkeypatch):
    fake = _FakeTrader()
    main_mod.auto_trader = fake
    applied = {}
    monkeypatch.setattr(main_mod.strat_settings, "update_settings",
                        lambda patch: applied.update(patch) or main_mod.strat_settings.get_settings())
    monkeypatch.setattr(main_mod.strat_settings, "persist", lambda: {})
    try:
        reply = main_mod._telegram_command("/koruma use_score_ranking 1")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert applied["use_score_ranking"] is True


def test_command_koruma_view_shows_score_ranking(monkeypatch):
    fake = _FakeTrader()
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/koruma")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "Skor siralamasi" in reply


def test_command_koruma_unknown_key():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/koruma foo 5")
    finally:
        main_mod.auto_trader = None
    assert reply is not None and "bilinmeyen anahtar" in reply


def test_command_koruma_invalid_value():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/koruma max_drawdown_pct abc")
    finally:
        main_mod.auto_trader = None
    assert reply is not None and "gecersiz deger" in reply


def test_command_koruma_bad_usage():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/koruma max_drawdown_pct")
    finally:
        main_mod.auto_trader = None
    assert reply is not None and "kullanim" in reply


def test_signal_for_symbol_uses_binance():
    fake = _FakeKlines(_signal_df())
    main_mod.app.state.binance = fake
    try:
        sig = asyncio.run(main_mod._signal_for_symbol("BTCUSDT"))
    finally:
        main_mod.app.state.binance = None
    assert fake.calls == [("BTCUSDT", "4h", 400)]
    assert sig["signal"] in ("BUY", "SELL", "HOLD")
    assert sig["price"] is not None


def test_signal_for_symbol_no_client():
    main_mod.app.state.binance = None
    sig = asyncio.run(main_mod._signal_for_symbol("BTCUSDT"))
    assert sig == {}


def test_send_symbol_signal_buy(monkeypatch):
    async def fake_signal(symbol, interval="4h"):
        return {"signal": "BUY", "price": 65000.0, "reason": "test",
                "sl": 63000.0, "tp": 69000.0}

    monkeypatch.setattr(main_mod, "_signal_for_symbol", fake_signal)
    sent = []

    async def fake_send_signal(symbol, signal, price, reason=""):
        sent.append((symbol, signal, price, reason))

    monkeypatch.setattr(main_mod.telegram, "send_signal", fake_send_signal)
    asyncio.run(main_mod._send_symbol_signal("BTCUSDT"))
    assert sent == [("BTCUSDT", "BUY", 65000.0, "test")]


def test_send_symbol_signal_failure(monkeypatch):
    async def fake_signal(symbol, interval="4h"):
        return {}

    monkeypatch.setattr(main_mod, "_signal_for_symbol", fake_signal)
    messages = []

    async def fake_send(message):
        messages.append(message)

    monkeypatch.setattr(main_mod.telegram, "send", fake_send)
    asyncio.run(main_mod._send_symbol_signal("BTCUSDT"))
    assert messages and "alinamadi" in messages[0]
