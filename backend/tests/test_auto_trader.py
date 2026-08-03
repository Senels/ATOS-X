import sqlite3

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
        self.client = None

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
