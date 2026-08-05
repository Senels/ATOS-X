import asyncio
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pandas as pd

from app import main as main_mod
from app.notifications.telegram import TelegramNotifier, _process_updates, format_stop_summary
from app.notifications.telegram import format_daily_summary


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
    def __init__(self):
        self.closed = 3
        self.op_counts = {"signals": 5, "backtest_runs": 1,
                          "risk_events": 2, "performance": 4}

    def get_closed_trades_since(self, days=1):
        return []

    def clear_closed_trades(self):
        return self.closed

    def clear_operational(self):
        return dict(self.op_counts)


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
        self.priority = ["BTCUSDT", "ETHUSDT"]
        self.trading_symbols = ["BTCUSDT", "ETHUSDT"]
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


def test_command_positions_shows_sl_tp_prices():
    fake = _FakeTrader()
    fake.active_positions["BTCUSDT"]["sl"] = 64000.0
    fake.active_positions["BTCUSDT"]["tp"] = 68000.0
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/pozisyon")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "SL: $64000" in reply and "TP: $68000" in reply


def test_command_positions_shows_age():
    fake = _FakeTrader()
    fake.active_positions["BTCUSDT"]["open_time"] = "2026-08-04T06:00:00"
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/pozisyon")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "h" in reply


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


def test_command_stop_requires_confirmation():
    fake = _FakeTrader()
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/durdur")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "onay" in reply
    assert "durdurulacak" in reply


def test_command_stop_confirmed(monkeypatch):
    fake = _FakeTrader()
    main_mod.auto_trader = fake
    monkeypatch.setattr(main_mod, "_run_later", lambda coro: True)
    try:
        reply = main_mod._telegram_command("/durdur onay")
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


def test_format_daily_summary_has_pf_and_best_symbol():
    trades = [
        ("id", "BTCUSDT", "BUY", 100.0, 108.0, 1.0, 120.0, "2026-08-04"),
        ("id", "BTCUSDT", "SELL", 200.0, 190.0, 1.0, 80.0, "2026-08-04"),
        ("id", "ETHUSDT", "BUY", 3000.0, 2950.0, 1.0, -50.0, "2026-08-04"),
        ("id", "ETHUSDT", "SELL", 4000.0, 3990.0, 1.0, -10.0, "2026-08-04"),
    ]
    msg = format_daily_summary(trades, 10500.0, {}, top_symbols=["BTCUSDT"],
                               marks=None)
    assert "Profit Factor: 3.33" in msg
    assert "En iyi sembol: BTCUSDT +200.00" in msg
    assert "Win Rate: 50.0%" in msg
    assert "Toplam PnL: <b>+140.00</b>" in msg
    assert "Semboller: BTCUSDT +200.00, ETHUSDT -60.00" in msg


def test_format_daily_summary_pf_inf_when_no_losses():
    trades = [
        ("id", "BTCUSDT", "BUY", 100.0, 108.0, 1.0, 120.0, "2026-08-04"),
    ]
    msg = format_daily_summary(trades, 10120.0, {}, marks=None)
    assert "Profit Factor: inf" in msg
    assert "En iyi sembol: BTCUSDT +120.00" in msg


def test_format_daily_summary_with_data_status():
    trades = [
        ("id", "BTCUSDT", "BUY", 100.0, 108.0, 1.0, 120.0, "2026-08-04"),
    ]
    ds = {"ok": True, "fresh": 80, "stale": 5, "missing": 2}
    msg = format_daily_summary(trades, 10120.0, {}, top_symbols=["BTCUSDT"],
                               marks=None, data_status=ds)
    assert "Veri: 80 guncel / 5 eski / 2 eksik" in msg


def test_format_daily_summary_without_data_status():
    trades = [("id", "BTCUSDT", "BUY", 100.0, 108.0, 1.0, 120.0, "2026-08-04")]
    msg = format_daily_summary(trades, 10120.0, {}, marks=None)
    assert "Veri:" not in msg


def test_format_daily_summary_with_protection_stats():
    trades = [("id", "BTCUSDT", "BUY", 100.0, 108.0, 1.0, 120.0, "2026-08-04")]
    msg = format_daily_summary(trades, 10120.0, {}, marks=None,
                               protection_stats={"trailing": 3, "breakeven": 2})
    assert "Koruma: Trailing: 3 | Breakeven: 2" in msg


def test_format_daily_summary_protection_pnl():
    trades = [("id", "BTCUSDT", "BUY", 100.0, 108.0, 1.0, 120.0, "2026-08-04")]
    msg = format_daily_summary(
        trades, 10120.0, {}, marks=None,
        protection_stats={"trailing": 3, "trailing_pnl": 45.5,
                          "breakeven": 2, "breakeven_pnl": -12.25})
    assert "Trailing: 3 (+45.50)" in msg
    assert "Breakeven: 2 (-12.25)" in msg


def test_format_daily_summary_drawdown_and_worst():
    trades = [
        ("id", "BTCUSDT", "BUY", 100.0, 108.0, 1.0, 120.0, "2026-08-04"),
        ("id", "ETHUSDT", "BUY", 3000.0, 2900.0, 2.0, -200.0, "2026-08-04"),
    ]
    msg = format_daily_summary(
        trades, 10120.0, {}, marks=None,
        drawdown_pct=5.3,
        worst_sym=("ETHUSDT", -200.0),
        risk_events=[{"time": "2026-08-04T10:00:00", "type": "block_add", "message": "x"}])
    assert "Drawdown: %5.3" in msg
    assert "En kotu sembol: ETHUSDT -200.00" in msg
    assert "Risk olayi: 1" in msg


def test_format_daily_summary_no_drawdown_when_zero():
    trades = [("id", "BTCUSDT", "BUY", 100.0, 108.0, 1.0, 120.0, "2026-08-04")]
    msg = format_daily_summary(trades, 10120.0, {}, marks=None, drawdown_pct=0.0)
    assert "Drawdown:" not in msg


def test_format_daily_summary_no_protection_line():
    trades = [("id", "BTCUSDT", "BUY", 100.0, 108.0, 1.0, 120.0, "2026-08-04")]
    msg = format_daily_summary(trades, 10120.0, {}, marks=None,
                               protection_stats={"trailing": 0, "breakeven": 0})
    assert "Koruma:" not in msg


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
        {"symbol": "BTCUSDT", "side": "BUY", "pnl": 120.0, "reason": "take_profit",
         "entry": 100.0, "exit": 108.0, "time": "2026-08-04T07:00:00"},
        {"symbol": "ETHUSDT", "side": "SELL", "pnl": -50.0, "reason": "stop_loss",
         "entry": 3000.0, "exit": 3050.0, "time": "2026-08-04T06:00:00"},
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
    assert "100 -> 108" in reply
    assert "Net: +70.00" in reply
    assert "Kazanma: %50" in reply


def test_command_history_no_prices_ok():
    fake = _FakeTrader()
    fake.trade_history = [{"symbol": "BTCUSDT", "side": "BUY", "pnl": 1.0, "reason": "x"}]
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/gecmis")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "BTCUSDT" in reply


def test_command_history_symbol_filter():
    fake = _FakeTrader()
    fake.trade_history = [
        {"symbol": "BTCUSDT", "side": "BUY", "pnl": 120.0, "reason": "tp",
         "entry": 100.0, "exit": 108.0, "time": "2026-08-04T07:00:00"},
        {"symbol": "ETHUSDT", "side": "SELL", "pnl": -50.0, "reason": "sl",
         "entry": 3000.0, "exit": 3050.0, "time": "2026-08-04T06:00:00"},
        {"symbol": "BTCUSDT", "side": "BUY", "pnl": 80.0, "reason": "tp",
         "entry": 200.0, "exit": 220.0, "time": "2026-08-04T05:00:00"},
    ]
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/gecmis 10 BTCUSDT")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "BTCUSDT" in reply
    assert "ETHUSDT" not in reply
    assert "+120.00" in reply
    assert "+80.00" in reply
    assert "Net: +200.00" in reply


def test_command_history_symbol_filter_empty():
    fake = _FakeTrader()
    fake.trade_history = [
        {"symbol": "BTCUSDT", "side": "BUY", "pnl": 120.0},
    ]
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/gecmis ETHUSDT")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "kapanis gecmisi yok" in reply


def test_command_stats_summary():
    fake = _FakeTrader()
    fake.trade_history = [
        {"symbol": "BTCUSDT", "side": "BUY", "pnl": 120.0},
        {"symbol": "BTCUSDT", "side": "SELL", "pnl": 80.0},
        {"symbol": "ETHUSDT", "side": "BUY", "pnl": -50.0},
        {"symbol": "ETHUSDT", "side": "SELL", "pnl": -10.0},
    ]
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/istatistik")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "istatistik (4 islem)" in reply
    assert "Net PnL: +140.00" in reply
    assert "Kazanma: %50" in reply
    assert "PF: 3.33" in reply
    assert "Ort kar: +100.00" in reply
    assert "En iyi sembol: BTCUSDT +200.00" in reply


def test_command_stats_shows_protection_counts():
    fake = _FakeTrader()
    fake.trade_history = [
        {"symbol": "BTCUSDT", "side": "BUY", "pnl": 40.0, "trailing": True},
        {"symbol": "ETHUSDT", "side": "BUY", "pnl": 5.0, "breakeven": True},
        {"symbol": "SOLUSDT", "side": "SELL", "pnl": -10.0},
    ]
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/istatistik")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "Trailing: 1 (+40.00)" in reply
    assert "Breakeven: 1 (+5.00)" in reply


def test_command_stats_no_protection_line():
    fake = _FakeTrader()
    fake.trade_history = [
        {"symbol": "BTCUSDT", "side": "BUY", "pnl": 10.0},
        {"symbol": "ETHUSDT", "side": "SELL", "pnl": -5.0},
    ]
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/istatistik")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "Trailing" not in reply
    assert "Breakeven" not in reply


def test_command_stats_empty():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/istatistik")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "islem gecmisi yok" in reply


def _fresh_csv_df():
    idx = pd.DatetimeIndex([datetime.utcnow() - pd.Timedelta(hours=1)]).tz_localize("UTC")
    return pd.DataFrame({"open": [100.0], "high": [101.0], "low": [99.0],
                         "close": [100.0], "volume": [1.0]}, index=idx)


def test_command_data_summary(monkeypatch):
    fake = _FakeTrader()
    main_mod.auto_trader = fake

    def fake_load(symbol, interval="4h", data_dir=None, limit=None):
        if symbol == "ETHUSDT":
            raise FileNotFoundError("missing")
        return _fresh_csv_df()

    monkeypatch.setattr(main_mod.loader, "load_csv", fake_load)
    try:
        reply = main_mod._telegram_command("/veri")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "Guncel: 1" in reply
    assert "Esk" in reply
    assert "Eksik: 1" in reply
    assert "ETHUSDT" in reply


def test_command_data_all_fresh(monkeypatch):
    fake = _FakeTrader()
    main_mod.auto_trader = fake
    monkeypatch.setattr(main_mod.loader, "load_csv",
                        lambda symbol, interval="4h", data_dir=None, limit=None: _fresh_csv_df())
    try:
        reply = main_mod._telegram_command("/veri")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "Guncel: 2" in reply
    assert "Eksik: 0" in reply


def test_command_temizle_clears_closed_history():
    fake = _FakeTrader()
    fake.trade_history = [{"symbol": "BTCUSDT", "side": "BUY", "pnl": 1.0}]
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/temizle")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "temizlendi" in reply
    assert "3" in reply
    assert "hepsi" in reply
    assert fake.trade_history == []


def test_command_temizle_hard_wipes_operational():
    fake = _FakeTrader()
    fake.trade_history = [{"symbol": "BTCUSDT", "side": "BUY", "pnl": 1.0}]
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/temizle hepsi onay")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "temizlendi (hepsi)" in reply
    assert "Sinyal: 5" in reply and "Performans: 4" in reply
    assert fake.trade_history == []


def test_command_temizle_hard_requires_confirmation():
    fake = _FakeTrader()
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/temizle hepsi")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "Onay icin" in reply


def test_command_temizle_requires_trader():
    main_mod.auto_trader = None
    reply = main_mod._telegram_command("/temizle")
    assert "motor calismiyor" in reply


def test_command_backfill_schedules(monkeypatch):
    fake = _FakeTrader()
    fake.binance = SimpleNamespace(client=object())
    main_mod.auto_trader = fake
    captured = []

    def fake_run_later(coro):
        captured.append(coro)
        return True

    monkeypatch.setattr(main_mod, "_run_later", fake_run_later)
    try:
        reply = main_mod._telegram_command("/backfill BTCUSDT,ETHUSDT 7")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "backfill basladi" in reply
    assert "BTCUSDT" in reply and "ETHUSDT" in reply
    assert "7 gun" in reply
    assert len(captured) == 1


def test_command_backfill_stale_symbols(monkeypatch):
    fake = _FakeTrader()
    fake.binance = SimpleNamespace(client=object())
    main_mod.auto_trader = fake
    monkeypatch.setattr(main_mod.loader, "load_csv",
                        lambda symbol, interval="4h", data_dir=None, limit=None: (_ for _ in ()).throw(FileNotFoundError("missing")))
    captured = []
    monkeypatch.setattr(main_mod, "_run_later",
                        lambda coro: (captured.append(coro), True)[1])
    try:
        reply = main_mod._telegram_command("/backfill")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "backfill basladi" in reply
    assert "eski/eksik" in reply
    assert "BTCUSDT" in reply
    assert len(captured) == 1


def test_command_backfill_all_fresh(monkeypatch):
    fake = _FakeTrader()
    fake.binance = SimpleNamespace(client=object())
    main_mod.auto_trader = fake
    monkeypatch.setattr(main_mod.loader, "load_csv",
                        lambda symbol, interval="4h", data_dir=None, limit=None: _fresh_csv_df())
    captured = []
    monkeypatch.setattr(main_mod, "_run_later",
                        lambda coro: (captured.append(coro), True)[1])
    try:
        reply = main_mod._telegram_command("/backfill")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "backfill gereken sembol yok" in reply
    assert captured == []


def test_command_history_bad_n():
    fake = _FakeTrader()
    fake.trade_history = [{"symbol": "BTCUSDT", "side": "BUY", "pnl": 1.0, "reason": "x"}]
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/gecmis abc")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "kapanis gecmisi yok" in reply


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


def test_command_close_all_requires_confirmation():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/kapatall")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "kapatilacak" in reply
    assert "onay" in reply


def test_command_close_all_confirmed(monkeypatch):
    main_mod.auto_trader = _FakeTrader()

    def fake_run_later(coro):
        coro.close()
        return True

    monkeypatch.setattr(main_mod, "_run_later", fake_run_later)
    try:
        reply = main_mod._telegram_command("/kapatall onay")
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


def test_command_sl_all_updates_all_positions():
    fake = _FakeTrader()
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/sl all 64000")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "SL guncellendi" in reply
    assert "64000" in reply
    assert fake.active_positions["BTCUSDT"]["sl"] == 64000
    assert fake.active_positions["ETHUSDT"]["sl"] == 64000


def test_command_sl_all_empty():
    fake = _FakeTrader()
    fake.active_positions = {}
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/sl all 64000")
    finally:
        main_mod.auto_trader = None
    assert "acik pozisyon yok" in reply


def test_command_sl_all_invalid_price():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/sl all abc")
    finally:
        main_mod.auto_trader = None
    assert "gecersiz SL fiyati" in reply


def test_command_tp_all_updates_all_positions():
    fake = _FakeTrader()
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/tp all 70000")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "TP guncellendi" in reply
    assert "70000" in reply
    assert fake.active_positions["BTCUSDT"]["tp"] == 70000
    assert fake.active_positions["ETHUSDT"]["tp"] == 70000


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


def test_command_koruma_set_backfill_interval(monkeypatch):
    fake = _FakeTrader()
    main_mod.auto_trader = fake
    applied = {}
    monkeypatch.setattr(main_mod.strat_settings, "update_settings",
                        lambda patch: applied.update(patch) or main_mod.strat_settings.get_settings())
    monkeypatch.setattr(main_mod.strat_settings, "persist", lambda: {})
    try:
        reply = main_mod._telegram_command("/koruma data_backfill_hours 6")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert applied["data_backfill_hours"] == 6.0


def test_command_koruma_view_shows_backfill(monkeypatch):
    fake = _FakeTrader()
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/koruma")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "Otomatik backfill" in reply


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

    async def fake_send_signal(symbol, signal, price, reason="", sl=None, tp=None, strength=None):
        sent.append((symbol, signal, price, reason, sl, tp, strength))

    monkeypatch.setattr(main_mod.telegram, "send_signal", fake_send_signal)
    asyncio.run(main_mod._send_symbol_signal("BTCUSDT"))
    assert sent == [("BTCUSDT", "BUY", 65000.0, "test", 63000.0, 69000.0, None)]


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


def test_send_batch_signals_summary(monkeypatch):
    async def fake_signal(symbol, interval="4h"):
        table = {
            "BTCUSDT": {"signal": "BUY", "price": 65000.0, "reason": "trend up"},
            "ETHUSDT": {"signal": "SELL", "price": 3000.0, "reason": "dump"},
        }
        return table.get(symbol, {})

    monkeypatch.setattr(main_mod, "_signal_for_symbol", fake_signal)
    messages = []

    async def fake_send(message):
        messages.append(message)

    monkeypatch.setattr(main_mod.telegram, "send", fake_send)
    asyncio.run(main_mod._send_batch_signals(["BTCUSDT", "ETHUSDT", "SOLUSDT"]))
    assert len(messages) == 1
    msg = messages[0]
    assert "ATOS X tarama (4h):" in msg
    assert "BTCUSDT" in msg and "BUY" in msg
    assert "ETHUSDT" in msg and "SELL" in msg
    assert "SOLUSDT" not in msg


def test_command_scan_schedules(monkeypatch):
    fake = _FakeTrader()
    main_mod.auto_trader = fake
    captured = []

    def fake_run_later(coro):
        captured.append(coro)
        coro.close()
        return True

    monkeypatch.setattr(main_mod, "_run_later", fake_run_later)
    try:
        reply = main_mod._telegram_command("/sinyalall 3")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "2 sembol taranacak" in reply
    assert len(captured) == 1


def test_command_scan_bad_n(monkeypatch):
    fake = _FakeTrader()
    main_mod.auto_trader = fake
    captured = []

    def fake_run_later(coro):
        captured.append(coro)
        coro.close()
        return True

    monkeypatch.setattr(main_mod, "_run_later", fake_run_later)
    try:
        reply = main_mod._telegram_command("/sinyalall abc")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "2 sembol taranacak (4h)" in reply
    assert len(captured) == 1


def test_command_scan_interval(monkeypatch):
    fake = _FakeTrader()
    main_mod.auto_trader = fake
    captured = []

    def fake_run_later(coro):
        captured.append(coro)
        coro.close()
        return True

    monkeypatch.setattr(main_mod, "_run_later", fake_run_later)
    try:
        reply = main_mod._telegram_command("/sinyalall 3 1h")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "2 sembol taranacak (1h)" in reply
    assert len(captured) == 1


def test_command_watchlist_schedules(monkeypatch):
    fake = _FakeTrader()
    fake.priority = ["BTCUSDT", "ETHUSDT"]
    fake.trading_symbols = ["BTCUSDT", "ETHUSDT"]
    main_mod.auto_trader = fake
    captured = []

    def fake_run_later(coro):
        captured.append(coro)
        coro.close()
        return True

    monkeypatch.setattr(main_mod, "_run_later", fake_run_later)
    try:
        reply = main_mod._telegram_command("/izleme")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "2 sembol hesaplaniyor" in reply
    assert len(captured) == 1


def test_command_watchlist_empty_list():
    fake = _FakeTrader()
    fake.priority = []
    fake.trading_symbols = []
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/izleme")
    finally:
        main_mod.auto_trader = None
    assert "tarama listesi bos" in reply


def test_command_watchlist_requires_trader():
    main_mod.auto_trader = None
    reply = main_mod._telegram_command("/izleme")
    assert "motor calismiyor" in reply


def test_command_performance_summary():
    fake = _FakeTrader()
    fake.trade_history = [
        {"symbol": "BTCUSDT", "side": "BUY", "pnl": 120.0,
         "time": "2026-08-01T10:00:00"},
        {"symbol": "ETHUSDT", "side": "SELL", "pnl": -50.0,
         "time": "2026-08-01T12:00:00"},
        {"symbol": "SOLUSDT", "side": "BUY", "pnl": 80.0,
         "time": "2026-08-02T10:00:00"},
    ]
    fake.equity = 10150.0
    fake.db = type("FakeDB", (), {
        "get_performance_series": lambda self, n: [
            ("2026-08-01", 10000.0, 0),
            ("2026-08-02", 10150.0, 1),
        ]
    })()
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/performans")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "performans (3 islem)" in reply
    assert "Equity: $10150" in reply
    assert "Peak: $10150" in reply
    assert "Kazanma: %67" in reply
    assert "2026-08" in reply


def test_command_performance_empty():
    fake = _FakeTrader()
    fake.trade_history = []
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/performans")
    finally:
        main_mod.auto_trader = None
    assert "islem gecmisi yok" in reply


def test_command_performance_requires_trader():
    main_mod.auto_trader = None
    reply = main_mod._telegram_command("/performans")
    assert "motor calismiyor" in reply


def test_command_last_trade():
    fake = _FakeTrader()
    fake.trade_history = [
        {"symbol": "BTCUSDT", "side": "BUY", "pnl": 120.0,
         "entry": 65000.0, "exit": 66000.0, "reason": "take_profit",
         "trailing": True, "time": "2026-08-04T10:00:00"},
    ]
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/son")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "BTCUSDT" in reply
    assert "BUY" in reply
    assert "+120.00" in reply
    assert "take_profit" in reply
    assert "Trailing" in reply
    assert "65000" in reply
    assert "66000" in reply


def test_command_last_trade_empty():
    fake = _FakeTrader()
    fake.trade_history = []
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/son")
    finally:
        main_mod.auto_trader = None
    assert "kapanan islem yok" in reply


def test_command_last_trade_requires_trader():
    main_mod.auto_trader = None
    reply = main_mod._telegram_command("/son")
    assert "motor calismiyor" in reply


# -- /alarm ---------------------------------------------------------------
def test_command_alarm_adds_alert():
    fake = _FakeTrader()
    fake.live_prices = {"BTCUSDT": 60000.0}
    main_mod.auto_trader = fake
    main_mod._PRICE_ALERTS.clear()
    try:
        reply = main_mod._telegram_command("/alarm BTCUSDT 65000")
    finally:
        main_mod.auto_trader = None
        main_mod._PRICE_ALERTS.clear()
    assert reply is not None
    assert "alarm eklendi" in reply
    assert "$65000" in reply


def test_command_alarm_below_side():
    fake = _FakeTrader()
    fake.live_prices = {"ETHUSDT": 3000.0}
    main_mod.auto_trader = fake
    main_mod._PRICE_ALERTS.clear()
    try:
        reply = main_mod._telegram_command("/alarm ETHUSDT 2900 alt")
    finally:
        main_mod.auto_trader = None
        main_mod._PRICE_ALERTS.clear()
    assert reply is not None
    assert "altina inince" in reply


def test_command_alarm_list_and_clear():
    main_mod.auto_trader = _FakeTrader()
    main_mod._PRICE_ALERTS.clear()
    try:
        main_mod._telegram_command("/alarm BTCUSDT 65000")
        main_mod._telegram_command("/alarm BTCUSDT 55000 alt")
        reply = main_mod._telegram_command("/alarm")
        cleared = main_mod._telegram_command("/alarm temizle")
    finally:
        main_mod.auto_trader = None
        main_mod._PRICE_ALERTS.clear()
    assert reply is not None
    assert "aktif alarmlar" in reply
    assert "2" in reply
    assert "BTCUSDT" in reply
    assert cleared is not None
    assert "2 alarm silindi" in cleared


def test_command_alarm_invalid_price():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/alarm BTCUSDT abc")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "gecersiz fiyat" in reply


def test_command_alarm_already_above():
    fake = _FakeTrader()
    fake.live_prices = {"BTCUSDT": 70000.0}
    main_mod.auto_trader = fake
    main_mod._PRICE_ALERTS.clear()
    try:
        reply = main_mod._telegram_command("/alarm BTCUSDT 65000")
    finally:
        main_mod.auto_trader = None
        main_mod._PRICE_ALERTS.clear()
    assert reply is not None
    assert "zaten" in reply


# -- /kapatall sembol listesi ---------------------------------------------
def test_command_close_all_symbols_requires_confirmation():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/kapatall BTCUSDT")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "1 pozisyon kapatilacak" in reply
    assert "BTCUSDT" in reply
    assert "onay" in reply


def test_command_close_all_symbols_confirmed(monkeypatch):
    main_mod.auto_trader = _FakeTrader()

    def fake_run_later(coro):
        coro.close()
        return True

    monkeypatch.setattr(main_mod, "_run_later", fake_run_later)
    try:
        reply = main_mod._telegram_command("/kapatall BTCUSDT,ETHUSDT onay")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "2 pozisyon kapatiliyor" in reply


def test_command_close_all_symbols_no_match():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/kapatall SOLUSDT onay")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "belirtilen sembollerde acik pozisyon yok" in reply


# -- /risk pozisyon dagilimi ----------------------------------------------
def test_command_risk_position_breakdown():
    fake = _FakeTrader()
    fake.live_prices = {"BTCUSDT": 66000.0, "ETHUSDT": 2950.0}
    fake.active_positions["BTCUSDT"]["sl"] = 64000.0
    fake.active_positions["ETHUSDT"]["sl"] = 3050.0
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/risk")
    finally:
        main_mod.auto_trader = None
    assert "Pozisyon risk dagilimi" in reply
    assert "BTCUSDT BUY notional $33000" in reply
    assert "ETHUSDT SELL notional $5900" in reply
    assert "SL mesafe %3.0" in reply
    assert "risk $1000.00" in reply
    assert "Toplam notional: $38900.00" in reply
    assert "%389 equity" in reply


# -- /islem ---------------------------------------------------------------
def test_command_islem_shows_today_trades():
    class _TodayDB:
        def get_closed_trades_since(self, days=1):
            return [
                ("row_id1", "BTCUSDT", "BUY", 100.0, 108.0, "2026-08-05T07:00:00", 120.0),
                ("row_id2", "ETHUSDT", "SELL", 3000.0, 3050.0, "2026-08-05T06:00:00", -50.0),
            ]

    fake = _FakeTrader()
    fake.db = _TodayDB()
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/islem")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "bugun (2 islem)" in reply
    assert "BTCUSDT" in reply and "ETHUSDT" in reply
    assert "+120.00" in reply and "-50.00" in reply
    assert "Toplam: +70.00" in reply


def test_command_islem_empty():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/islem")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "kapanan islem yok" in reply


# -- /bakiye --------------------------------------------------------------
def test_command_bakiye_shows_summary():
    fake = _FakeTrader()
    fake.live_prices = {"BTCUSDT": 66000.0}
    fake.day_pnl = 42.5
    fake.drawdown_pct = 2.1
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/bakiye")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "Equity: $10000.00" in reply
    assert "Pozisyon: 2 (L:1 S:1)" in reply
    assert "BTCUSDT BUY" in reply and "ETHUSDT SELL" in reply
    assert "Gunluk PnL: +42.50" in reply
    assert "Drawdown: %2.1" in reply


def test_command_bakiye_requires_trader():
    main_mod.auto_trader = None
    reply = main_mod._telegram_command("/bakiye")
    assert "motor calismiyor" in reply


# -- /ayarla --------------------------------------------------------------
def test_command_ayarla_shows_settings(monkeypatch):
    settings = {
        "leading_indicator": "Supertrend",
        "risk_per_trade": 0.02,
        "max_leverage": 3,
        "max_open_positions": 5,
        "max_position_age_hours": 48,
        "rr_ratio": 2.0,
        "atr_mult": 1.5,
        "trailing_activate_pct": 1.0,
        "trailing_sl_pct": 0.5,
        "breakeven_activate_pct": 1.0,
        "max_daily_loss_pct": 5.0,
        "max_drawdown_pct": 20.0,
        "max_position_pct": 75.0,
        "max_side_pct": 150.0,
        "council_min_confidence": 0.6,
        "use_decision_council": True,
        "use_score_ranking": True,
        "data_backfill_hours": 6.0,
        "data_freshness_hours": 12.0,
        "min_equity": 500.0,
        "max_consecutive_losses": 3,
    }
    monkeypatch.setattr(main_mod.strat_settings, "get_settings", lambda: settings)
    reply = main_mod._telegram_command("/ayarla")
    assert reply is not None
    assert "risk ayarlari" in reply
    assert "Max pozisyon: 5" in reply
    assert "Risk/trade: %2.0" in reply
    assert "Equity taban: $500" in reply
    assert "Decision Council: acik" in reply
    assert "Skor siralamasi: acik" in reply
    assert "Tazelik: 12 saat" in reply


# -- /yedek ---------------------------------------------------------------
def test_command_yedek_takes_backup(monkeypatch):
    class _BkDB:
        def backup(self):
            return {"ok": True, "path": "atos_backup_20260805.db", "kept": 3, "deleted": []}

    main_mod.app.state.db = _BkDB()
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/yedek")
    finally:
        main_mod.auto_trader = None
        main_mod.app.state.db = None
    assert reply is not None
    assert "DB yedeklendi" in reply
    assert "atos_backup_20260805.db" in reply


def test_command_yedek_no_db(monkeypatch):
    main_mod.app.state.db = None
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/yedek")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "motor calismiyor" in reply
