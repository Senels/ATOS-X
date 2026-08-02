import sqlite3

import pytest

import app.strategy.auto_trader as at_mod
from app.backtest.engine import BacktestEngine
from app.core.database import Database


class FakeBinance:
    def __init__(self):
        self.open_calls = []
        self.close_calls = []
        self.klines = None

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
        self.close_calls.append(symbol)
        return {"symbol": symbol}


@pytest.fixture
def trader(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "at.db"))
    monkeypatch.setattr(at_mod, "Database", lambda *a, **k: db)
    fb = FakeBinance()
    tr = at_mod.AutoTrader(fb)
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
