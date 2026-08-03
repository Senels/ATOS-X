import sqlite3
from datetime import datetime, timedelta

import pandas as pd
import pytest

import app.strategy.auto_trader as at_mod
from app.backtest.engine import BacktestEngine
from app.core.database import Database


class FakeTelegram:
    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(message)


class FakeBinance:
    def __init__(self):
        self.open_calls = []
        self.close_calls = []
        self.tp_sl_calls = []
        self.cancel_calls = []
        self.klines = None
        self.no_position = False
        self.open_positions = []
        self.algo_orders = []
        self.raise_on_positions = False
        self.connect_failures = 0
        self.fail_tp_sl = False
        self.client = None
        self.klines_calls = []
        self.raise_on_klines = set()

    async def connect(self):
        if self.connect_failures:
            self.connect_failures -= 1
            return False
        self.client = True
        return True

    async def load_all_symbols(self):
        return ["BTCUSDT", "ETHUSDT"]

    async def get_all_tickers(self):
        return {"BTCUSDT": 65000.0, "ETHUSDT": 3000.0}

    async def get_klines(self, symbol, interval, limit):
        self.klines_calls.append(symbol)
        if symbol in self.raise_on_klines:
            raise Exception("kline error")
        return self.klines

    async def get_price(self, symbol="BTCUSDT"):
        return 65000.0

    async def place_market_order(self, symbol, side, quantity):
        self.open_calls.append((symbol, side, quantity))
        return {"symbol": symbol, "side": side, "quantity": quantity}

    async def close_position(self, symbol):
        if self.no_position:
            return None
        self.close_calls.append(symbol)
        return {"symbol": symbol}

    async def set_tp_sl(self, symbol, position_side, sl_price, tp_price):
        self.tp_sl_calls.append((symbol, position_side, sl_price, tp_price))
        if self.fail_tp_sl:
            return {"sl": None, "tp": None}
        return {"sl": "SL_1", "tp": "TP_1"}

    async def cancel_algo_order(self, symbol, algo_id):
        self.cancel_calls.append((symbol, algo_id))
        return {"symbol": symbol, "algoId": algo_id}

    async def get_open_positions(self):
        if self.raise_on_positions:
            raise Exception("network down")
        return self.open_positions

    async def get_open_algo_orders(self):
        return self.algo_orders


@pytest.fixture
def trader(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "at.db"))
    monkeypatch.setattr(at_mod, "Database", lambda *a, **k: db)
    fb = FakeBinance()
    tr = at_mod.AutoTrader(fb, paper=False)
    return tr, fb, db


def _signals_count(db) -> int:
    conn = sqlite3.connect(db.db_path)
    n = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    conn.close()
    return n


def test_sizing_matches_engine(trader):
    tr, fb, db = trader
    entry, sl = 100.0, 90.0
    sizing = tr.engine.position_size(entry, sl, tr.equity)
    ref = BacktestEngine(
        initial_equity=tr.engine.initial_equity,
        risk_per_trade=tr.engine.risk_per_trade,
        fee_rate=tr.engine.fee_rate,
        max_leverage=tr.engine.max_leverage,
    ).position_size(entry, sl, tr.equity)
    assert sizing["qty"] == pytest.approx(ref["qty"])
    assert sizing["entry_fee"] == pytest.approx(ref["entry_fee"])


async def test_open_close_persists(trader):
    tr, fb, db = trader
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)

    assert "BTCUSDT" in tr.active_positions
    assert len(fb.open_calls) == 1
    rows = db.get_trades(limit=10)
    assert len(rows) == 1
    assert rows[0][7] == "OPEN"
    assert _signals_count(db) == 1

    await tr.close_position("BTCUSDT", 64000.0, "stop_loss")
    assert "BTCUSDT" not in tr.active_positions
    assert len(fb.close_calls) == 1
    rows = db.get_trades(limit=10)
    assert rows[0][7] == "CLOSED"
    assert rows[0][6] is not None  # pnl yazildi
    assert len(tr.trade_history) == 1
    assert tr.trade_history[0]["reason"] == "stop_loss"


async def test_update_price_feeds_check_positions(trader):
    tr, fb, db = trader
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)

    tr.update_price("BTCUSDT", 62900.0)
    await tr.check_positions({})
    assert "BTCUSDT" not in tr.active_positions
    assert tr.trade_history[0]["reason"] == "stop_loss"


async def test_falls_back_to_rest_prices(trader):
    tr, fb, db = trader
    await tr.open_position("BTCUSDT", "SELL", 65000.0, 66000.0, 64000.0)

    # websocket fiyati yok; REST ticker'dan gelir (>= SL -> stop_loss)
    await tr.check_positions({"BTCUSDT": 66100.0})
    assert "BTCUSDT" not in tr.active_positions
    assert tr.trade_history[0]["reason"] == "stop_loss"


async def test_opposite_signal_closes(trader):
    tr, fb, db = trader
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
    await tr.process_signals([{
        "symbol": "BTCUSDT", "signal": "SELL", "price": 64500.0,
        "sl": 65000.0, "tp": 64000.0, "reason": "signal",
    }])
    assert "BTCUSDT" not in tr.active_positions
    assert tr.trade_history[0]["reason"] == "signal_exit"


def test_rank_symbols_skips_missing_data(tmp_path, monkeypatch):
    from app.data import loader

    if not (loader.DEFAULT_DATA_DIR / "futures_4h_data" / "BTCUSDT_4h.csv").exists():
        pytest.skip("BTCUSDT_4h.csv yok; rank testi atlandi")
    db = Database(str(tmp_path / "at.db"))
    monkeypatch.setattr(at_mod, "Database", lambda *a, **k: db)
    tr = at_mod.AutoTrader(FakeBinance())
    tr.trading_symbols = ["BTCUSDT", "ETHUSDT", "NOPEUSDT"]
    ranked = tr.rank_symbols(limit=400)
    assert ranked
    assert set(ranked) <= {"BTCUSDT", "ETHUSDT"}


async def test_paper_mode_skips_exchange(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "at.db"))
    monkeypatch.setattr(at_mod, "Database", lambda *a, **k: db)
    fb = FakeBinance()
    tr = at_mod.AutoTrader(fb, paper=True)
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)

    assert "BTCUSDT" in tr.active_positions
    assert fb.open_calls == []  # emir borsaya gitmedi
    assert fb.tp_sl_calls == []  # exchange SL/TP de gitmedi
    rows = db.get_trades(limit=10)
    assert len(rows) == 1
    assert rows[0][7] == "OPEN"

    tr.update_price("BTCUSDT", 62900.0)
    await tr.check_positions({})
    assert "BTCUSDT" not in tr.active_positions
    assert fb.close_calls == []  # kapanis da simule edildi
    assert tr.trade_history[0]["reason"] == "stop_loss"


async def test_update_equity_throttles(trader):
    tr, fb, db = trader
    tr.perf_interval = 3600
    tr._last_perf = -3600.0
    await tr.update_equity()
    await tr.update_equity()
    await tr.update_equity()
    rows = db.get_performance_series(10)
    assert len(rows) == 1


async def test_update_equity_computes_win_rate(trader):
    tr, fb, db = trader
    tr.perf_interval = 0
    tr.trade_history = [
        {"pnl": 100.0}, {"pnl": -50.0}, {"pnl": 25.0},
    ]
    await tr.update_equity()
    rows = db.get_performance(limit=10)
    assert len(rows) == 1
    assert rows[0][5] == pytest.approx(66.67, abs=0.01)  # 2/3 kazancli


async def test_real_mode_places_exchange_sl_tp(trader):
    tr, fb, db = trader
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
    assert fb.tp_sl_calls == [("BTCUSDT", "LONG", 63000.0, 69000.0)]
    assert tr.active_positions["BTCUSDT"]["sl_order_id"] == "SL_1"
    assert tr.active_positions["BTCUSDT"]["tp_order_id"] == "TP_1"


async def test_close_cancels_algo_orders(trader):
    tr, fb, db = trader
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
    await tr.close_position("BTCUSDT", 64000.0, "signal_exit")
    assert fb.cancel_calls == [("BTCUSDT", "SL_1"), ("BTCUSDT", "TP_1")]


async def test_close_when_exchange_already_closed(trader):
    tr, fb, db = trader
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
    fb.no_position = True  # exchange-side SL/TP zaten kapatti
    await tr.close_position("BTCUSDT", 63000.0, "stop_loss")
    assert "BTCUSDT" not in tr.active_positions
    assert tr.trade_history[0]["reason"] == "stop_loss"
    assert tr.trade_history[0]["exit"] == 63000.0


async def test_reconcile_restores_exchange_positions(trader):
    tr, fb, db = trader
    fb.open_positions = [
        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "65000.0"},
    ]
    fb.algo_orders = [
        {"symbol": "BTCUSDT", "orderType": "STOP_MARKET", "algoId": 111, "triggerPrice": "63000"},
        {"symbol": "BTCUSDT", "orderType": "TAKE_PROFIT_MARKET", "algoId": 222, "triggerPrice": "69000"},
    ]
    await tr.reconcile_positions()
    pos = tr.active_positions["BTCUSDT"]
    assert pos["side"] == "BUY"
    assert pos["quantity"] == 0.5
    assert pos["sl"] == 63000
    assert pos["tp"] == 69000
    assert pos["sl_order_id"] == 111
    assert pos["tp_order_id"] == 222
    assert pos["restored"] is True


async def test_reconcile_restores_db_open_time(trader):
    tr, fb, db = trader
    db.save_trade("BTCUSDT", "BUY", 65000.0, 0.5)
    db_entry = db.get_open_trade_entry_time("BTCUSDT")
    assert db_entry is not None
    fb.open_positions = [
        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "65000.0"},
    ]
    fb.algo_orders = [
        {"symbol": "BTCUSDT", "orderType": "STOP_MARKET", "algoId": 111, "triggerPrice": "63000"},
    ]
    await tr.reconcile_positions()
    pos = tr.active_positions["BTCUSDT"]
    assert pos["open_time"] == datetime.fromisoformat(db_entry).isoformat()


async def test_db_open_trade_entry_time_none_when_closed(trader):
    tr, fb, db = trader
    assert db.get_open_trade_entry_time("BTCUSDT") is None
    db.save_trade("BTCUSDT", "BUY", 65000.0, 0.5)
    db.close_trade_by_symbol("BTCUSDT", 66000.0, 100.0)
    assert db.get_open_trade_entry_time("BTCUSDT") is None


async def test_reconcile_skips_unprotected(trader):
    tr, fb, db = trader
    fb.open_positions = [
        {"symbol": "ETHUSDT", "positionAmt": "-2.0", "entryPrice": "3000"},
    ]
    fb.algo_orders = []
    await tr.reconcile_positions()
    assert "ETHUSDT" not in tr.active_positions


async def test_reconcile_alerts_on_unprotected(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    fb.open_positions = [
        {"symbol": "ETHUSDT", "positionAmt": "-2.0", "entryPrice": "3000"},
    ]
    fb.algo_orders = []
    await tr.reconcile_positions()
    assert any("korumasiz" in m and "ETHUSDT" in m for m in tg.sent)


async def test_reconcile_repairs_when_tracked_loses_algo(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    tr.active_positions["BTCUSDT"] = {
        "side": "BUY", "entry_price": 65000, "quantity": 0.5,
        "sl": 63000, "tp": 69000,
        "sl_order_id": 111, "tp_order_id": 222,
        "entry_fee": 0.0, "open_time": "x",
    }
    fb.open_positions = [
        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "65000"},
    ]
    fb.algo_orders = [
        {"symbol": "BTCUSDT", "orderType": "TAKE_PROFIT_MARKET", "algoId": 222, "triggerPrice": "69000"},
    ]
    await tr.reconcile_positions()
    assert "BTCUSDT" in tr.active_positions
    assert ("BTCUSDT", "LONG", 63000.0, 0.0) in fb.tp_sl_calls
    assert tr.active_positions["BTCUSDT"]["sl_order_id"] == "SL_1"
    assert tg.sent == []


async def test_reconcile_alerts_when_repair_fails(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    fb.fail_tp_sl = True
    tr.active_positions["BTCUSDT"] = {
        "side": "BUY", "entry_price": 65000, "quantity": 0.5,
        "sl": 63000, "tp": 69000,
        "sl_order_id": 111, "tp_order_id": 222,
        "entry_fee": 0.0, "open_time": "x",
    }
    fb.open_positions = [
        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "65000"},
    ]
    fb.algo_orders = []
    await tr.reconcile_positions()
    assert "BTCUSDT" in tr.active_positions
    assert any("SL" in m and "TP" in m and "BTCUSDT" in m for m in tg.sent)


async def test_concentration_alerts_single_symbol(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    tr.equity = 10000
    tr.max_position_pct = 50.0
    tr.max_side_pct = 999.0
    tr.active_positions["BTCUSDT"] = {
        "side": "BUY", "entry_price": 60000, "quantity": 1.0,
        "sl": 0, "tp": 0, "sl_order_id": None, "tp_order_id": None,
        "entry_fee": 0.0, "open_time": "x",
    }
    await tr._check_concentration()
    assert any("BTCUSDT" in m and "konsantrasyon" in m for m in tg.sent)
    await tr._check_concentration()
    assert len(tg.sent) == 1


async def test_concentration_realerts_after_clear(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    tr.equity = 10000
    tr.max_position_pct = 50.0
    tr.max_side_pct = 999.0
    pos = {
        "side": "BUY", "entry_price": 60000, "quantity": 1.0,
        "sl": 0, "tp": 0, "sl_order_id": None, "tp_order_id": None,
        "entry_fee": 0.0, "open_time": "x",
    }
    tr.active_positions["BTCUSDT"] = dict(pos)
    await tr._check_concentration()
    n = len(tg.sent)
    del tr.active_positions["BTCUSDT"]
    await tr._check_concentration()
    assert len(tg.sent) == n
    tr.active_positions["BTCUSDT"] = dict(pos)
    await tr._check_concentration()
    assert len(tg.sent) == n + 1


async def test_concentration_alerts_on_side(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    tr.equity = 10000
    tr.max_position_pct = 999.0
    tr.max_side_pct = 100.0
    for sym, qty in [("BTCUSDT", 0.9), ("ETHUSDT", 10.0)]:
        tr.active_positions[sym] = {
            "side": "BUY", "entry_price": 60000 if sym == "BTCUSDT" else 3000,
            "quantity": qty,
            "sl": 0, "tp": 0, "sl_order_id": None, "tp_order_id": None,
            "entry_fee": 0.0, "open_time": "x",
        }
    await tr._check_concentration()
    assert any("LONG" in m and "konsantrasyon" in m for m in tg.sent)
    assert not any("BTCUSDT" in m for m in tg.sent)


async def test_side_block_prevents_open(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    tr.equity = 10000
    tr.max_side_pct = 50.0
    tr.max_position_pct = 999.0
    tr.max_positions = 10
    await tr.open_position("BTCUSDT", "BUY", 100.0, 99.5, 101.0)
    await tr.process_signals([{
        "symbol": "ETHUSDT", "signal": "BUY", "price": 100.0,
        "sl": 99.5, "tp": 101.0, "reason": "r",
    }])
    await tr._check_concentration()
    assert "ETHUSDT" not in tr.active_positions
    assert any("engellendi" in m and "LONG" in m for m in tg.sent)


async def test_side_block_no_spam(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    tr.equity = 10000
    tr.max_side_pct = 50.0
    tr.max_position_pct = 999.0
    tr.max_positions = 10
    await tr.open_position("BTCUSDT", "BUY", 100.0, 99.5, 101.0)
    sig = {"symbol": "ETHUSDT", "signal": "BUY", "price": 100.0,
           "sl": 99.5, "tp": 101.0, "reason": "r"}
    await tr.process_signals([sig])
    await tr._check_concentration()
    n = len(tg.sent)
    await tr.process_signals([sig])
    await tr._check_concentration()
    assert len(tg.sent) == n


async def test_symbol_block_prevents_open(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    tr.equity = 10000
    tr.max_position_pct = 50.0
    tr.max_side_pct = 999.0
    await tr.process_signals([{
        "symbol": "BTCUSDT", "signal": "BUY", "price": 100.0,
        "sl": 99.5, "tp": 101.0, "reason": "r",
    }])
    await tr._check_concentration()
    assert "BTCUSDT" not in tr.active_positions
    assert any("engellendi" in m and "BTCUSDT" in m for m in tg.sent)


async def test_side_block_clears_when_under(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    tr.equity = 10000
    tr.max_side_pct = 50.0
    tr.max_position_pct = 999.0
    tr.max_positions = 10
    await tr.open_position("BTCUSDT", "BUY", 100.0, 99.5, 101.0)
    sig = {"symbol": "ETHUSDT", "signal": "BUY", "price": 100.0,
           "sl": 99.5, "tp": 101.0, "reason": "r"}
    await tr.process_signals([sig])
    await tr._check_concentration()
    assert "ETHUSDT" not in tr.active_positions
    assert "side:LONG" in tr._conc_blocks
    tr.active_positions.clear()
    await tr._check_concentration()
    assert "side:LONG" not in tr._conc_blocks
    assert any("kaldirildi" in m and "side:LONG" in m for m in tg.sent)


async def test_apply_risk_settings_live(trader):
    tr, fb, db = trader
    tr._apply_risk_settings({
        "max_open_positions": 5,
        "risk_per_trade": 0.05,
        "max_leverage": 20.0,
    })
    assert tr.max_positions == 5
    assert tr.engine.risk_per_trade == 0.05
    assert tr.engine.max_leverage == 20.0


async def test_side_block_summary_sent(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    tr.equity = 10000
    tr.max_side_pct = 50.0
    tr.max_position_pct = 999.0
    tr.max_positions = 10
    tr.block_summary_interval = 0
    await tr.open_position("BTCUSDT", "BUY", 100.0, 99.5, 101.0)
    await tr.process_signals([{
        "symbol": "ETHUSDT", "signal": "BUY", "price": 100.0,
        "sl": 99.5, "tp": 101.0, "reason": "r",
    }])
    await tr._check_concentration()
    assert any("engeli aktif" in m for m in tg.sent)


async def test_side_block_summary_throttled(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    tr.equity = 10000
    tr.max_side_pct = 50.0
    tr.max_position_pct = 999.0
    tr.max_positions = 10
    tr.block_summary_interval = 3600
    await tr.open_position("BTCUSDT", "BUY", 100.0, 99.5, 101.0)
    await tr.process_signals([{
        "symbol": "ETHUSDT", "signal": "BUY", "price": 100.0,
        "sl": 99.5, "tp": 101.0, "reason": "r",
    }])
    await tr._check_concentration()
    n = len(tg.sent)
    await tr._check_concentration()
    assert len(tg.sent) == n


async def test_side_block_summary_resets_when_clear(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    tr.equity = 10000
    tr.max_side_pct = 50.0
    tr.max_position_pct = 999.0
    tr.max_positions = 10
    tr.block_summary_interval = 3600
    await tr.open_position("BTCUSDT", "BUY", 100.0, 99.5, 101.0)
    await tr.process_signals([{
        "symbol": "ETHUSDT", "signal": "BUY", "price": 100.0,
        "sl": 99.5, "tp": 101.0, "reason": "r",
    }])
    await tr._check_concentration()
    assert tr._last_block_summary != 0.0
    tr.active_positions.clear()
    await tr._check_concentration()
    assert tr._last_block_summary == 0.0


async def test_sync_block_state_sends_on_add(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    tr._conc_blocks.add("side:LONG")
    await tr._sync_block_state()
    assert any("degisti" in m and "side:LONG" in m for m in tg.sent)
    assert any("engellendi" in m and "side:LONG" in m for m in tg.sent)


async def test_sync_block_state_sends_on_remove(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    tr._conc_blocks.add("side:LONG")
    await tr._sync_block_state()
    n = len(tg.sent)
    tr._conc_blocks.discard("side:LONG")
    await tr._sync_block_state()
    assert len(tg.sent) == n + 1
    assert any("kaldirildi" in m and "side:LONG" in m for m in tg.sent)


async def test_sync_block_state_silent_when_unchanged(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    tr._conc_blocks.add("side:LONG")
    await tr._sync_block_state()
    n = len(tg.sent)
    await tr._sync_block_state()
    assert len(tg.sent) == n


async def test_notify_startup_state_sends_settings(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    tr.max_position_pct = 75.0
    tr.max_side_pct = 150.0
    await tr._notify_startup_state()
    assert any("Risk ayarlari" in m and "%75" in m and "%150" in m for m in tg.sent)
    assert any("engel yok" in m for m in tg.sent)


async def test_notify_startup_state_lists_blocks(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    tr._conc_blocks.add("side:LONG")
    await tr._notify_startup_state()
    assert any("engel aktif" in m and "side:LONG" in m for m in tg.sent)


async def test_drawdown_halts_new_entries(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    tr.equity = 9000.0
    tr.peak_equity = 12000.0
    tr.max_drawdown_pct = 20.0
    await tr._check_drawdown()
    assert tr.risk_halted is True
    assert tr.drawdown_pct == round((12000.0 - 9000.0) / 12000.0 * 100, 2)
    assert any("Drawdown" in m and "durduruldu" in m for m in tg.sent)
    tr.max_positions = 10
    await tr.process_signals([{
        "symbol": "BTCUSDT", "signal": "BUY", "price": 100.0,
        "sl": 99.5, "tp": 101.0, "reason": "r",
    }])
    assert "BTCUSDT" not in tr.active_positions


async def test_drawdown_no_halt_below_threshold(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    tr.equity = 11000.0
    tr.peak_equity = 12000.0
    tr.max_drawdown_pct = 20.0
    await tr._check_drawdown()
    assert tr.risk_halted is False
    assert tg.sent == []


async def test_drawdown_resumes_after_recovery(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    tr.equity = 9000.0
    tr.peak_equity = 12000.0
    tr.max_drawdown_pct = 20.0
    await tr._check_drawdown()
    assert tr.risk_halted is True
    tr.equity = 11500.0  # %4.2 < %10 (yarisi) -> serbest
    await tr._check_drawdown()
    assert tr.risk_halted is False
    assert any("serbest" in m for m in tg.sent)


async def test_drawdown_updates_peak(trader):
    tr, fb, db = trader
    tr.equity = 15000.0
    tr.peak_equity = 12000.0
    tr.max_drawdown_pct = 20.0
    await tr._check_drawdown()
    assert tr.peak_equity == 15000.0
    assert tr.drawdown_pct == 0.0


async def test_drawdown_disabled_when_threshold_zero(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    tr.equity = 100.0
    tr.peak_equity = 12000.0
    tr.max_drawdown_pct = 0.0
    await tr._check_drawdown()
    assert tr.risk_halted is False
    assert tg.sent == []


async def test_time_stop_closes_expired_position(trader):
    tr, fb, db = trader
    tr.max_position_age_hours = 8
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
    tr.active_positions["BTCUSDT"]["open_time"] = (
        datetime.utcnow() - timedelta(hours=10)
    ).isoformat()
    await tr.check_positions({"BTCUSDT": 64000.0})
    assert "BTCUSDT" not in tr.active_positions
    assert any(t["reason"] == "time_stop" for t in tr.trade_history)


async def test_time_stop_disabled_when_zero(trader):
    tr, fb, db = trader
    tr.max_position_age_hours = 0
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
    tr.active_positions["BTCUSDT"]["open_time"] = (
        datetime.utcnow() - timedelta(hours=100)
    ).isoformat()
    await tr.check_positions({"BTCUSDT": 64000.0})
    assert "BTCUSDT" in tr.active_positions


async def test_time_stop_fresh_position_stays(trader):
    tr, fb, db = trader
    tr.max_position_age_hours = 8
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
    await tr.check_positions({"BTCUSDT": 64000.0})
    assert "BTCUSDT" in tr.active_positions


async def test_trailing_moves_sl_up_on_profit(trader):
    tr, fb, db = trader
    tr.trailing_activate_pct = 3.0
    tr.trailing_sl_pct = 1.5
    await tr.open_position("BTCUSDT", "BUY", 100.0, 95.0, 110.0)
    await tr.check_positions({"BTCUSDT": 105.0})
    pos = tr.active_positions["BTCUSDT"]
    assert pos["trailing"] is True
    assert pos["sl"] == pytest.approx(105.0 * 0.985, abs=0.001)
    assert pos["sl"] > 95.0
    assert fb.cancel_calls and fb.tp_sl_calls


async def test_trailing_ignores_loss(trader):
    tr, fb, db = trader
    tr.trailing_activate_pct = 3.0
    tr.trailing_sl_pct = 1.5
    await tr.open_position("BTCUSDT", "BUY", 100.0, 95.0, 110.0)
    await tr.check_positions({"BTCUSDT": 98.0})
    pos = tr.active_positions["BTCUSDT"]
    assert "trailing" not in pos
    assert pos["sl"] == 95.0


async def test_trailing_does_not_retreat_sl(trader):
    tr, fb, db = trader
    tr.trailing_activate_pct = 3.0
    tr.trailing_sl_pct = 1.5
    await tr.open_position("BTCUSDT", "BUY", 100.0, 95.0, 110.0)
    await tr.check_positions({"BTCUSDT": 105.0})
    assert tr.active_positions["BTCUSDT"]["sl"] == pytest.approx(105.0 * 0.985)
    await tr.check_positions({"BTCUSDT": 103.8})
    pos = tr.active_positions["BTCUSDT"]
    assert pos["sl"] == pytest.approx(105.0 * 0.985, abs=0.001)


async def test_trailing_disabled_when_activate_zero(trader):
    tr, fb, db = trader
    tr.trailing_activate_pct = 0
    tr.trailing_sl_pct = 1.5
    await tr.open_position("BTCUSDT", "BUY", 100.0, 95.0, 110.0)
    await tr.check_positions({"BTCUSDT": 105.0})
    pos = tr.active_positions["BTCUSDT"]
    assert "trailing" not in pos
    assert pos["sl"] == 95.0


async def test_trailing_moves_sl_down_for_short(trader):
    tr, fb, db = trader
    tr.trailing_activate_pct = 3.0
    tr.trailing_sl_pct = 1.5
    await tr.open_position("ETHUSDT", "SELL", 100.0, 105.0, 90.0)
    await tr.check_positions({"ETHUSDT": 95.0})
    pos = tr.active_positions["ETHUSDT"]
    assert pos["trailing"] is True
    assert pos["sl"] == pytest.approx(95.0 * 1.015, abs=0.001)
    assert pos["sl"] < 105.0


async def test_risk_event_log_drawdown_halt(trader):
    tr, fb, db = trader
    tr.equity = 9000.0
    tr.peak_equity = 12000.0
    await tr._check_drawdown()
    assert tr.risk_halted is True
    assert tr.risk_events[-1]["type"] == "drawdown_halt"


async def test_risk_event_log_block_change(trader):
    tr, fb, db = trader
    tr._conc_blocks.add("side:LONG")
    await tr._sync_block_state()
    assert tr.risk_events[-1]["type"] == "block_add"
    tr._conc_blocks.discard("side:LONG")
    await tr._sync_block_state()
    assert tr.risk_events[-1]["type"] == "block_remove"


async def test_risk_event_log_trailing(trader):
    tr, fb, db = trader
    tr.trailing_activate_pct = 3.0
    tr.trailing_sl_pct = 1.5
    await tr.open_position("BTCUSDT", "BUY", 100.0, 95.0, 110.0)
    await tr.check_positions({"BTCUSDT": 105.0})
    assert any(e["type"] == "trailing_activate" for e in tr.risk_events)


async def test_risk_event_log_system_stop(trader):
    tr, fb, db = trader
    await tr.stop()
    assert tr.risk_events[-1]["type"] == "system_stop"


async def test_risk_events_ring_buffer(trader):
    tr, fb, db = trader
    tr.risk_events_max = 3
    for i in range(5):
        tr._log_risk_event(f"type{i}", f"msg{i}")
    assert len(tr.risk_events) == 3
    assert tr.risk_events[0]["type"] == "type2"


async def test_fetch_klines_batch(trader):
    tr, fb, db = trader
    n = 200
    df = pd.DataFrame({
        "open": [100.0] * n, "high": [101.0] * n, "low": [99.0] * n,
        "close": [100.0] * n, "volume": [1000.0] * n,
    })
    fb.klines = df
    fb.raise_on_klines = {"NOPEUSDT"}
    m = await tr._fetch_klines_batch(["BTCUSDT", "ETHUSDT", "NOPEUSDT"])
    assert set(m) == {"BTCUSDT", "ETHUSDT", "NOPEUSDT"}
    assert m["NOPEUSDT"] is None
    assert m["BTCUSDT"] is df
    assert sorted(fb.klines_calls) == ["BTCUSDT", "ETHUSDT", "NOPEUSDT"]


async def test_reconcile_silent_when_tracked_protected(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    tr.active_positions["BTCUSDT"] = {
        "side": "BUY", "entry_price": 65000, "quantity": 0.5,
        "sl": 63000, "tp": 69000,
        "sl_order_id": 111, "tp_order_id": 222,
        "entry_fee": 0.0, "open_time": "x",
    }
    fb.open_positions = [
        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "65000"},
    ]
    fb.algo_orders = [
        {"symbol": "BTCUSDT", "orderType": "STOP_MARKET", "algoId": 111, "triggerPrice": "63000"},
        {"symbol": "BTCUSDT", "orderType": "TAKE_PROFIT_MARKET", "algoId": 222, "triggerPrice": "69000"},
    ]
    await tr.reconcile_positions()
    assert "BTCUSDT" in tr.active_positions
    assert tg.sent == []


async def test_reconcile_paper_skips_exchange(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "at.db"))
    monkeypatch.setattr(at_mod, "Database", lambda *a, **k: db)
    fb = FakeBinance()
    fb.open_positions = [
        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "65000"},
    ]
    tr = at_mod.AutoTrader(fb, paper=True)
    await tr.reconcile_positions()
    assert "BTCUSDT" not in tr.active_positions


async def test_check_positions_skips_missing_levels(trader):
    tr, fb, db = trader
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
    pos = tr.active_positions["BTCUSDT"]
    pos["tp"] = 0.0  # tp yok -> take_profit tetiklenmemeli
    tr.update_price("BTCUSDT", 70000.0)
    await tr.check_positions({})
    assert "BTCUSDT" in tr.active_positions
    pos["sl"] = 0.0
    pos["tp"] = 69000.0
    tr.update_price("BTCUSDT", 70000.0)
    await tr.check_positions({})
    assert "BTCUSDT" not in tr.active_positions


async def test_reconcile_drift_records_closed(trader):
    tr, fb, db = trader
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
    tr.update_price("BTCUSDT", 64000.0)  # sl/tp arasi -> exchange_closed
    fb.open_positions = [{"symbol": "ETHUSDT", "positionAmt": "1.0", "entryPrice": "3000"}]
    fb.algo_orders = []
    await tr.reconcile_positions()
    assert "BTCUSDT" not in tr.active_positions
    assert len(tr.trade_history) == 1
    assert tr.trade_history[0]["reason"] == "exchange_closed"


async def test_reconcile_drift_estimates_take_profit(trader):
    tr, fb, db = trader
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
    tr.update_price("BTCUSDT", 70000.0)  # tp ustunde -> take_profit tahmini
    await tr.reconcile_positions()
    assert tr.trade_history[0]["reason"] == "take_profit"
    assert tr.trade_history[0]["exit"] == 69000.0


async def test_reconcile_aborts_on_network_error(trader):
    tr, fb, db = trader
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
    fb.raise_on_positions = True
    await tr.reconcile_positions()
    assert "BTCUSDT" in tr.active_positions  # yanlis kapanis kaydi yok
    assert tr.trade_history == []


async def test_reconcile_empty_is_noop(trader):
    tr, fb, db = trader
    await tr.reconcile_positions()
    assert tr.active_positions == {}


async def test_ensure_connected_retries(trader):
    tr, fb, db = trader
    tr.running = True
    fb.connect_failures = 2
    assert await tr._ensure_connected(max_attempts=5, delay=0) is True
    assert fb.client is True


async def test_ensure_connected_gives_up(trader):
    tr, fb, db = trader
    tr.running = True
    fb.connect_failures = 100
    assert await tr._ensure_connected(max_attempts=3, delay=0) is False
    assert fb.client is None


async def test_ensure_connected_alerts_on_recovery():
    fb = FakeBinance()
    tg = FakeTelegram()
    tr = at_mod.AutoTrader(fb, telegram=tg, paper=False)
    tr.running = True
    fb.connect_failures = 2
    assert await tr._ensure_connected(max_attempts=5, delay=0) is True
    assert any("yeniden kuruldu" in m for m in tg.sent)


async def test_ensure_connected_alerts_on_give_up():
    fb = FakeBinance()
    tg = FakeTelegram()
    tr = at_mod.AutoTrader(fb, telegram=tg, paper=False)
    tr.running = True
    fb.connect_failures = 100
    assert await tr._ensure_connected(max_attempts=2, delay=0) is False
    assert any("kurulamadi" in m for m in tg.sent)

