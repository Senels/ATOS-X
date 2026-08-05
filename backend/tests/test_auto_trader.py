import sqlite3
from datetime import datetime, timedelta

import pandas as pd
import pytest

import app.strategy.auto_trader as at_mod
from app.backtest.engine import BacktestEngine
from app.core.database import Database
from app.strategy import settings as strat_settings


class FakeTelegram:
    def __init__(self):
        self.sent = []
        self.stop_summaries = []
        self.signal_msgs = []

    async def send(self, message):
        self.sent.append(message)

    async def send_signal(self, symbol, side, price, reason, sl=None, tp=None, strength=0.0):
        self.signal_msgs.append((symbol, side, price, reason, sl, tp))

    async def send_stop_summary(self, closed):
        self.stop_summaries.append(closed)


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

    async def place_market_order(self, symbol, side, quantity, reduce_only=False):
        self.open_calls.append((symbol, side, quantity, reduce_only))
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
        return {"sl": "SL_1" if sl_price else None, "tp": "TP_1" if tp_price else None}

    async def cancel_algo_order(self, symbol, algo_id):
        self.cancel_calls.append((symbol, algo_id))
        return {"symbol": symbol, "algoId": algo_id}

    async def get_open_positions(self):
        if self.raise_on_positions:
            raise Exception("network down")
        return self.open_positions

    async def get_open_algo_orders(self):
        return self.algo_orders


def _kframe(closes):
    """Canli get_klines gibi tz-aware UTC indexli OHLCV frame."""
    closes = [float(c) for c in closes]
    highs = [c * 1.005 for c in closes]
    lows = [c * 0.995 for c in closes]
    opens = [closes[i - 1] if i else closes[i] for i in range(len(closes))]
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="4h", tz="UTC")
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": [1000.0] * len(closes),
    }, index=idx)


@pytest.fixture
def trader(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "at.db"))
    monkeypatch.setattr(at_mod, "Database", lambda *a, **k: db)
    fb = FakeBinance()
    tr = at_mod.AutoTrader(fb, paper=False, live_trading_enabled=True)
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


async def test_close_all_closes_positions(trader):
    tr, fb, db = trader
    tr.live_prices = {"BTCUSDT": 64000.0, "ETHUSDT": 3100.0}
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
    await tr.open_position("ETHUSDT", "SELL", 3000.0, 3100.0, 2800.0)
    closed = await tr.close_all()
    assert sorted(closed) == ["BTCUSDT", "ETHUSDT"]
    assert tr.active_positions == {}
    assert set(fb.close_calls) == {"BTCUSDT", "ETHUSDT"}
    assert {t["reason"] for t in tr.trade_history} == {"manual_close_all"}


async def test_close_all_skips_missing_price(trader):
    tr, fb, db = trader
    tr.live_prices = {}
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
    await tr.open_position("ETHUSDT", "SELL", 3000.0, 3100.0, 2800.0)
    closed = await tr.close_all()
    assert sorted(closed) == ["BTCUSDT", "ETHUSDT"]
    assert tr.active_positions == {}


async def test_update_sl_moves_stop(trader):
    tr, fb, db = trader
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
    fb.tp_sl_calls.clear()
    res = await tr.update_sl("BTCUSDT", 64000.0)
    assert res["ok"] is True
    assert res["new_sl"] == 64000.0
    assert tr.active_positions["BTCUSDT"]["sl"] == 64000.0
    assert fb.cancel_calls == [("BTCUSDT", "SL_1")]
    assert fb.tp_sl_calls == [("BTCUSDT", "LONG", 64000.0, 0.0)]


async def test_update_sl_paper_skips_exchange(trader):
    tr, fb, db = trader
    tr.paper = True
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
    fb.cancel_calls.clear()
    fb.tp_sl_calls.clear()
    res = await tr.update_sl("BTCUSDT", 64000.0)
    assert res["ok"] is True
    assert tr.active_positions["BTCUSDT"]["sl"] == 64000.0
    assert fb.cancel_calls == []
    assert fb.tp_sl_calls == []


async def test_update_sl_rejects_missing_position(trader):
    tr, fb, db = trader
    res = await tr.update_sl("BTCUSDT", 64000.0)
    assert res["ok"] is False
    assert res["error"] == "position_not_found"


async def test_update_sl_rejects_wrong_direction(trader):
    tr, fb, db = trader
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
    res = await tr.update_sl("BTCUSDT", 66000.0)
    assert res["ok"] is False
    assert res["error"] == "sl_above_entry"
    assert tr.active_positions["BTCUSDT"]["sl"] == 63000.0
    await tr.open_position("ETHUSDT", "SELL", 3000.0, 3100.0, 2800.0)
    res2 = await tr.update_sl("ETHUSDT", 2900.0)
    assert res2["ok"] is False
    assert res2["error"] == "sl_below_entry"
    assert tr.active_positions["ETHUSDT"]["sl"] == 3100.0


async def test_update_sl_resets_trailing_flags(trader):
    tr, fb, db = trader
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
    tr.active_positions["BTCUSDT"]["trailing"] = True
    tr.active_positions["BTCUSDT"]["breakeven"] = True
    await tr.update_sl("BTCUSDT", 64000.0)
    pos = tr.active_positions["BTCUSDT"]
    assert pos["trailing"] is False
    assert pos["breakeven"] is False
    assert db.get_open_trade_protection("BTCUSDT") == (False, False)


async def test_update_tp_moves_take_profit(trader):
    tr, fb, db = trader
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
    fb.tp_sl_calls.clear()
    res = await tr.update_tp("BTCUSDT", 72000.0)
    assert res["ok"] is True
    assert res["new_tp"] == 72000.0
    assert tr.active_positions["BTCUSDT"]["tp"] == 72000.0
    assert fb.cancel_calls == [("BTCUSDT", "TP_1")]
    assert fb.tp_sl_calls == [("BTCUSDT", "LONG", 0.0, 72000.0)]


async def test_update_tp_paper_skips_exchange(trader):
    tr, fb, db = trader
    tr.paper = True
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
    fb.cancel_calls.clear()
    fb.tp_sl_calls.clear()
    res = await tr.update_tp("BTCUSDT", 72000.0)
    assert res["ok"] is True
    assert tr.active_positions["BTCUSDT"]["tp"] == 72000.0
    assert fb.cancel_calls == []
    assert fb.tp_sl_calls == []


async def test_update_tp_rejects_wrong_direction(trader):
    tr, fb, db = trader
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
    res = await tr.update_tp("BTCUSDT", 60000.0)
    assert res["ok"] is False
    assert res["error"] == "tp_below_entry"
    assert tr.active_positions["BTCUSDT"]["tp"] == 69000.0
    await tr.open_position("ETHUSDT", "SELL", 3000.0, 3100.0, 2800.0)
    res2 = await tr.update_tp("ETHUSDT", 3200.0)
    assert res2["ok"] is False
    assert res2["error"] == "tp_above_entry"
    assert tr.active_positions["ETHUSDT"]["tp"] == 2800.0


async def test_update_tp_rejects_missing_position(trader):
    tr, fb, db = trader
    res = await tr.update_tp("BTCUSDT", 72000.0)
    assert res["ok"] is False
    assert res["error"] == "position_not_found"


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


async def test_reconcile_restores_protection_flags(trader):
    tr, fb, db = trader
    db.save_trade("BTCUSDT", "BUY", 65000.0, 0.5)
    db.update_trade_protection("BTCUSDT", trailing=True, breakeven=True)
    fb.open_positions = [
        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "65000.0"},
    ]
    fb.algo_orders = [
        {"symbol": "BTCUSDT", "orderType": "STOP_MARKET", "algoId": 111, "triggerPrice": "63000"},
    ]
    await tr.reconcile_positions()
    pos = tr.active_positions["BTCUSDT"]
    assert pos["restored"] is True
    assert pos["trailing"] is True
    assert pos["breakeven"] is True


async def test_reconcile_restores_ttp_state(trader):
    tr, fb, db = trader
    db.save_trade("BTCUSDT", "BUY", 65000.0, 0.5,
                  entry_ts="2026-08-05 12:00:00", ttp_tp_hit=1)
    fb.open_positions = [
        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "65000.0"},
    ]
    fb.algo_orders = [
        {"symbol": "BTCUSDT", "orderType": "STOP_MARKET", "algoId": 111, "triggerPrice": "63000"},
    ]
    await tr.reconcile_positions()
    pos = tr.active_positions["BTCUSDT"]
    assert pos["restored"] is True
    assert pos["entry_ts"] == "2026-08-05 12:00:00"
    assert pos["ttp_tp_hit"] is True


_TTP_P = {
    "fast_ma_len": 3, "slow_ma_len": 5, "atr_len": 3,
    "sl_method": "perc", "sl_long_perc": 0.06, "sl_short_perc": 0.05,
    "tp_method": "perc", "tp_long_perc": 0.09, "tp_short_perc": 0.08,
    "sl_trail_mode": "ON", "be_enabled": True, "tp_qty_pct": 0.5,
    "tp_trail_enabled": False, "dist_method": "perc",
    "dist_perc": 0.0284, "dist_atr_mul": 3.4,
}


async def test_ttp_open_places_sl_only_order(trader):
    tr, fb, db = trader
    prev = strat_settings.get_settings()
    strat_settings.update_settings({"active_strategy": "ttp"})
    try:
        await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
        assert ("BTCUSDT", "LONG", 63000.0, 0.0) in fb.tp_sl_calls
        pos = tr.active_positions["BTCUSDT"]
        assert pos["sl_order_id"] == "SL_1"
        assert pos["tp_order_id"] is None
    finally:
        strat_settings.update_settings(prev)


async def test_open_position_alerts_when_sl_order_fails(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    fb.fail_tp_sl = True
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
    pos = tr.active_positions["BTCUSDT"]
    assert pos["sl_order_id"] is None
    assert any("BTCUSDT" in m and "koruma" in m for m in tg.sent)


async def test_open_position_alerts_when_sl_order_raises(trader, monkeypatch):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg

    async def _boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(fb, "set_tp_sl", _boom)
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
    pos = tr.active_positions["BTCUSDT"]
    assert pos["sl_order_id"] is None
    assert any("BTCUSDT" in m and "koruma" in m for m in tg.sent)


async def test_ttp_manage_moves_exchange_sl(trader):
    tr, fb, db = trader
    prev = strat_settings.get_settings()
    strat_settings.update_settings({"active_strategy": "ttp", "ttp": _TTP_P})
    try:
        df = _kframe([100.0] * 30 + [130.0, 138.0, 138.0, 138.0])
        fb.klines = df
        await tr.open_position("BTCUSDT", "BUY", 130.0, 130.0 * 0.94, 130.0 * 1.09,
                               entry_ts=str(df.index[30]))
        await tr.check_positions({})
        pos = tr.active_positions["BTCUSDT"]
        assert pos["sl"] > 130.0 * 0.94
        assert ("BTCUSDT", "LONG", pos["sl"], 0.0) in fb.tp_sl_calls
        assert ("BTCUSDT", "SL_1") in fb.cancel_calls
    finally:
        strat_settings.update_settings(prev)


async def test_ttp_partial_close_submits_reduce_order(trader):
    tr, fb, db = trader
    prev = strat_settings.get_settings()
    p = dict(_TTP_P, sl_trail_mode="TP", tp_qty_pct=0.5, be_enabled=True)
    strat_settings.update_settings({"active_strategy": "ttp", "ttp": p})
    try:
        df = _kframe([100.0] * 20 + [195.0, 215.0, 215.0, 215.0, 215.0] + [120.0] * 10)
        fb.klines = df
        await tr.open_position("BTCUSDT", "BUY", 195.0, 195.0 * 0.94, 195.0 * 1.09,
                               entry_ts=str(df.index[20]))
        qty0 = tr.active_positions["BTCUSDT"]["quantity"]
        await tr.check_positions({})
        pos = tr.active_positions.get("BTCUSDT")
        assert pos is not None
        assert pos["ttp_tp_hit"] is True
        reduces = [c for c in fb.open_calls if c[3] is True]
        assert reduces and reduces[0][1] == "SELL" and reduces[0][2] == pytest.approx(qty0 * 0.5)
        assert pos["quantity"] == pytest.approx(qty0 * 0.5)
    finally:
        strat_settings.update_settings(prev)


async def test_reconcile_does_not_require_tp_for_ttp(trader):
    tr, fb, db = trader
    prev = strat_settings.get_settings()
    strat_settings.update_settings({"active_strategy": "ttp"})
    try:
        tg = FakeTelegram()
        tr.telegram = tg
        db.save_trade("BTCUSDT", "BUY", 65000.0, 0.5)
        fb.open_positions = [
            {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "65000.0"},
        ]
        fb.algo_orders = [
            {"symbol": "BTCUSDT", "orderType": "STOP_MARKET", "algoId": 111, "triggerPrice": "63000"},
        ]
        await tr.reconcile_positions()
        assert "BTCUSDT" in tr.active_positions
        assert tg.sent == []
        assert tr.active_positions["BTCUSDT"]["sl_order_id"] == 111
    finally:
        strat_settings.update_settings(prev)


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


async def test_reconcile_repairs_tracked_without_order_ids(trader):
    tr, fb, db = trader
    tr.active_positions["BTCUSDT"] = {
        "side": "BUY", "entry_price": 65000, "quantity": 0.5,
        "sl": 63000, "tp": 69000,
        "sl_order_id": None, "tp_order_id": None,
        "entry_fee": 0.0, "open_time": "x",
    }
    fb.open_positions = [
        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "65000"},
    ]
    fb.algo_orders = []
    await tr.reconcile_positions()
    assert "BTCUSDT" in tr.active_positions
    assert ("BTCUSDT", "LONG", 63000.0, 69000.0) in fb.tp_sl_calls
    assert tr.active_positions["BTCUSDT"]["sl_order_id"] == "SL_1"
    assert tr.active_positions["BTCUSDT"]["tp_order_id"] == "TP_1"


async def test_reconcile_skips_tracked_without_prices(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    tr.active_positions["BTCUSDT"] = {
        "side": "BUY", "entry_price": 65000, "quantity": 0.5,
        "sl": 0, "tp": 0,
        "sl_order_id": None, "tp_order_id": None,
        "entry_fee": 0.0, "open_time": "x",
    }
    fb.open_positions = [
        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "65000"},
    ]
    fb.algo_orders = []
    await tr.reconcile_positions()
    assert fb.tp_sl_calls == []
    assert tg.sent == []


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
    assert any("Risk - max pos %75" in m and "%150" in m for m in tg.sent)
    assert any("Engel yok" in m for m in tg.sent)


async def test_notify_startup_state_lists_blocks(trader):
    tr, fb, db = trader
    tg = FakeTelegram()
    tr.telegram = tg
    tr._conc_blocks.add("side:LONG")
    await tr._notify_startup_state()
    assert any("Engeller: side:LONG" in m for m in tg.sent)


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


async def test_trailing_persists_to_db(trader):
    tr, fb, db = trader
    tr.trailing_activate_pct = 3.0
    tr.trailing_sl_pct = 1.5
    tr.breakeven_activate_pct = 0
    await tr.open_position("BTCUSDT", "BUY", 100.0, 95.0, 110.0)
    await tr.check_positions({"BTCUSDT": 105.0})
    assert db.get_open_trade_protection("BTCUSDT") == (True, False)


async def test_breakeven_persists_to_db(trader):
    tr, fb, db = trader
    tr.breakeven_activate_pct = 2.0
    tr.trailing_activate_pct = 0
    tr.trailing_sl_pct = 0
    await tr.open_position("BTCUSDT", "BUY", 100.0, 95.0, 110.0)
    await tr.check_positions({"BTCUSDT": 104.0})
    assert db.get_open_trade_protection("BTCUSDT") == (False, True)


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
    tr.breakeven_activate_pct = 0
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


async def test_stop_sends_stop_summary(trader):
    tr, fb, db = trader
    tr.telegram = FakeTelegram()
    await tr.open_position("BTCUSDT", "BUY", 100.0, 95.0, 110.0)
    assert tr.active_positions
    await tr.stop()
    assert tr.active_positions == {}
    assert len(tr.trade_history) == 1
    assert len(tr.telegram.stop_summaries) == 1
    assert tr.telegram.stop_summaries[0][0]["symbol"] == "BTCUSDT"
    assert tr.telegram.stop_summaries[0][0]["reason"] == "system_stop"


async def test_stop_summary_empty_when_no_positions(trader):
    tr, fb, db = trader
    tr.telegram = FakeTelegram()
    await tr.stop()
    assert len(tr.telegram.stop_summaries) == 1
    assert tr.telegram.stop_summaries[0] == []



async def test_risk_events_ring_buffer(trader):
    tr, fb, db = trader
    tr.risk_events_max = 3
    for i in range(5):
        tr._log_risk_event(f"type{i}", f"msg{i}")
    assert len(tr.risk_events) == 3
    assert tr.risk_events[0]["type"] == "type2"


async def test_closed_trade_records_trailing_flag(trader):
    tr, fb, db = trader
    await tr.open_position("BTCUSDT", "BUY", 100.0, 95.0, 110.0)
    tr.active_positions["BTCUSDT"]["trailing"] = True
    await tr.close_position("BTCUSDT", 99.0, "stop_loss")
    assert tr.trade_history
    last = tr.trade_history[-1]
    assert last["trailing"] is True


async def test_closed_trade_defaults_trailing_false(trader):
    tr, fb, db = trader
    await tr.open_position("BTCUSDT", "BUY", 100.0, 95.0, 110.0)
    await tr.close_position("BTCUSDT", 99.0, "stop_loss")
    assert tr.trade_history
    assert tr.trade_history[-1]["trailing"] is False


async def test_trailing_min_move_blocks_small_update(trader):
    tr, fb, db = trader
    tr.trailing_activate_pct = 3.0
    tr.trailing_sl_pct = 1.5
    tr.trailing_min_move_pct = 10.0
    tr.breakeven_activate_pct = 0
    await tr.open_position("BTCUSDT", "BUY", 100.0, 95.0, 110.0)
    await tr.check_positions({"BTCUSDT": 103.0})
    pos = tr.active_positions["BTCUSDT"]
    assert pos["sl"] == 95.0
    assert "trailing" not in pos
    await tr.check_positions({"BTCUSDT": 108.0})
    pos = tr.active_positions["BTCUSDT"]
    assert pos["sl"] == pytest.approx(108.0 * 0.985, abs=0.001)
    assert pos["trailing"] is True


async def test_trailing_min_move_zero_updates_every_time(trader):
    tr, fb, db = trader
    tr.trailing_activate_pct = 3.0
    tr.trailing_sl_pct = 1.5
    tr.trailing_min_move_pct = 0
    await tr.open_position("BTCUSDT", "BUY", 100.0, 95.0, 110.0)
    await tr.check_positions({"BTCUSDT": 104.0})
    await tr.check_positions({"BTCUSDT": 105.0})
    pos = tr.active_positions["BTCUSDT"]
    assert pos["sl"] == pytest.approx(105.0 * 0.985, abs=0.001)


async def test_trailing_move_event_logged(trader):
    tr, fb, db = trader
    tr.trailing_activate_pct = 3.0
    tr.trailing_sl_pct = 1.5
    await tr.open_position("BTCUSDT", "BUY", 100.0, 95.0, 110.0)
    await tr.check_positions({"BTCUSDT": 105.0})
    await tr.check_positions({"BTCUSDT": 108.0})
    types = [e["type"] for e in tr.risk_events]
    assert "trailing_activate" in types
    assert "trailing_move" in types


async def test_risk_events_persist_to_db(trader):
    tr, fb, db = trader
    tr._log_risk_event("test_persist", "kalici kayit")
    rows = db.get_risk_events(10)
    assert any(r["type"] == "test_persist" for r in rows)


async def test_risk_events_loaded_from_db_on_init(trader):
    tr, fb, db = trader
    tr._log_risk_event("boot_marker", "boot")
    tr2 = at_mod.AutoTrader(fb, paper=False, live_trading_enabled=True)
    assert any(e["type"] == "boot_marker" for e in tr2.risk_events)


async def test_notify_startup_state_summary(trader):
    tr, fb, db = trader
    tr.telegram = FakeTelegram()
    tr._log_risk_event("drawdown_halt", "test")
    await tr._notify_startup_state()
    assert tr.telegram.sent
    msg = tr.telegram.sent[0]
    assert "Motor baslatildi" in msg
    assert "Max pozisyon yasi" in msg
    assert "Trailing" in msg
    assert "Son risk olayi" in msg


async def test_loss_streak_halts_entries(trader):
    tr, fb, db = trader
    tr.max_consecutive_losses = 2
    for _ in range(2):
        await tr.open_position("BTCUSDT", "BUY", 100.0, 95.0, 110.0)
        await tr.close_position("BTCUSDT", 90.0, "stop_loss")
    assert tr.loss_halted is True
    assert tr.consecutive_losses == 2
    assert any(e["type"] == "loss_streak_halt" for e in tr.risk_events)
    await tr.open_position("BTCUSDT", "BUY", 100.0, 95.0, 110.0)
    await tr.close_position("BTCUSDT", 110.0, "take_profit")
    assert tr.loss_halted is False
    assert tr.consecutive_losses == 0
    assert any(e["type"] == "loss_streak_clear" for e in tr.risk_events)


async def test_loss_streak_disabled_when_zero(trader):
    tr, fb, db = trader
    tr.max_consecutive_losses = 0
    for _ in range(3):
        await tr.open_position("BTCUSDT", "BUY", 100.0, 95.0, 110.0)
        await tr.close_position("BTCUSDT", 90.0, "stop_loss")
    assert tr.loss_halted is False


async def test_trade_history_and_loss_streak_restored_on_init(trader):
    tr, fb, db = trader
    for _ in range(5):
        await tr.open_position("BTCUSDT", "BUY", 100.0, 95.0, 110.0)
        await tr.close_position("BTCUSDT", 90.0, "stop_loss")
    assert tr.loss_halted is True
    tr2 = at_mod.AutoTrader(fb, paper=False, live_trading_enabled=True)
    assert len(tr2.trade_history) == 5
    assert tr2.trade_history[0]["reason"] == "stop_loss"
    assert tr2.consecutive_losses == 5
    assert tr2.loss_halted is True


async def test_loss_halt_blocks_new_entries(trader):
    tr, fb, db = trader
    tr.loss_halted = True
    signal = {"symbol": "ETHUSDT", "signal": "BUY", "price": 100.0,
              "sl": 95.0, "tp": 110.0, "reason": ""}
    await tr.process_signals([signal])
    assert "ETHUSDT" not in tr.active_positions


async def test_daily_loss_halts_entries(trader):
    tr, fb, db = trader
    tr.max_daily_loss_pct = 5.0
    tr.day_start_date = tr.day_start_date
    tr.day_pnl = -600.0
    await tr._update_daily_pnl(0.0)
    assert tr.daily_loss_halted is True
    assert any(e["type"] == "daily_loss_halt" for e in tr.risk_events)
    signal = {"symbol": "ETHUSDT", "signal": "BUY", "price": 100.0,
              "sl": 95.0, "tp": 110.0, "reason": ""}
    await tr.process_signals([signal])
    assert "ETHUSDT" not in tr.active_positions


async def test_daily_loss_reset_on_new_day(trader):
    tr, fb, db = trader
    tr.max_daily_loss_pct = 5.0
    tr.day_start_date = "2000-01-01"
    tr.day_pnl = -500.0
    tr.daily_loss_halted = True
    tr._rollover_day()
    assert tr.day_pnl == 0.0
    assert tr.daily_loss_halted is False
    assert any(e["type"] == "daily_loss_clear" for e in tr.risk_events)


async def test_daily_loss_disabled_when_zero(trader):
    tr, fb, db = trader
    tr.max_daily_loss_pct = 0
    tr.day_pnl = -999999.0
    await tr._update_daily_pnl(0.0)
    assert tr.daily_loss_halted is False


async def test_equity_floor_halts_entries(trader):
    tr, fb, db = trader
    tr.min_equity = 8000.0
    tr.equity = 7000.0
    await tr._check_equity_floor()
    assert tr.equity_halted is True
    assert any(e["type"] == "equity_floor" for e in tr.risk_events)
    signal = {"symbol": "ETHUSDT", "signal": "BUY", "price": 100.0,
              "sl": 95.0, "tp": 110.0, "reason": ""}
    await tr.process_signals([signal])
    assert "ETHUSDT" not in tr.active_positions


async def test_equity_floor_recovers_above_threshold(trader):
    tr, fb, db = trader
    tr.min_equity = 8000.0
    tr.equity = 7000.0
    await tr._check_equity_floor()
    assert tr.equity_halted is True
    tr.equity = 8500.0
    await tr._check_equity_floor()
    assert tr.equity_halted is False
    assert any(e["type"] == "equity_clear" for e in tr.risk_events)


async def test_equity_floor_disabled_when_zero(trader):
    tr, fb, db = trader
    tr.min_equity = 0
    tr.equity = 1.0
    await tr._check_equity_floor()
    assert tr.equity_halted is False


async def test_breakeven_moves_sl_to_entry(trader):
    tr, fb, db = trader
    tr.breakeven_activate_pct = 2.0
    await tr.open_position("BTCUSDT", "BUY", 100.0, 95.0, 110.0)
    await tr.check_positions({"BTCUSDT": 102.5})
    pos = tr.active_positions["BTCUSDT"]
    assert pos["breakeven"] is True
    assert pos["sl"] == 100.0
    assert any(e["type"] == "breakeven_move" for e in tr.risk_events)


async def test_breakeven_ignores_small_profit(trader):
    tr, fb, db = trader
    tr.breakeven_activate_pct = 2.0
    await tr.open_position("BTCUSDT", "BUY", 100.0, 95.0, 110.0)
    await tr.check_positions({"BTCUSDT": 101.0})
    pos = tr.active_positions["BTCUSDT"]
    assert "breakeven" not in pos
    assert pos["sl"] == 95.0


async def test_breakeven_disabled_when_zero(trader):
    tr, fb, db = trader
    tr.breakeven_activate_pct = 0
    tr.trailing_activate_pct = 0
    await tr.open_position("BTCUSDT", "BUY", 100.0, 95.0, 110.0)
    await tr.check_positions({"BTCUSDT": 105.0})
    pos = tr.active_positions["BTCUSDT"]
    assert "breakeven" not in pos
    assert pos["sl"] == 95.0


async def test_breakeven_for_short(trader):
    tr, fb, db = trader
    tr.breakeven_activate_pct = 2.0
    tr.trailing_activate_pct = 0
    await tr.open_position("ETHUSDT", "SELL", 100.0, 105.0, 90.0)
    await tr.check_positions({"ETHUSDT": 97.0})
    pos = tr.active_positions["ETHUSDT"]
    assert pos["breakeven"] is True
    assert pos["sl"] == 100.0


async def test_breakeven_does_not_override_trailing(trader):
    tr, fb, db = trader
    tr.breakeven_activate_pct = 2.0
    tr.trailing_activate_pct = 3.0
    tr.trailing_sl_pct = 1.5
    await tr.open_position("BTCUSDT", "BUY", 100.0, 95.0, 110.0)
    await tr.check_positions({"BTCUSDT": 105.0})
    pos = tr.active_positions["BTCUSDT"]
    assert pos["trailing"] is True
    assert pos["sl"] > 100.0


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


async def test_council_gate_disabled_passes(trader):
    tr, fb, db = trader
    klines = pd.DataFrame({"open": [100.0], "high": [101.0], "low": [99.0],
                           "close": [100.0], "volume": [100.0]})
    allow, decision = tr._council_gate("BUY", klines, {"use_decision_council": False})
    assert allow is True
    assert decision is None


async def test_council_gate_mismatch_rejects(trader, monkeypatch):
    tr, fb, db = trader
    klines = pd.DataFrame({"open": [100.0], "high": [101.0], "low": [99.0],
                           "close": [100.0], "volume": [100.0]})
    monkeypatch.setattr(at_mod, "decide_council", lambda df, settings=None: {
        "verdict": "HOLD", "confidence": 1.0})
    allow, decision = tr._council_gate("BUY", klines, {"use_decision_council": True})
    assert allow is False
    assert decision["verdict"] == "HOLD"


async def test_council_gate_low_confidence_rejects(trader, monkeypatch):
    tr, fb, db = trader
    klines = pd.DataFrame({"open": [100.0], "high": [101.0], "low": [99.0],
                           "close": [100.0], "volume": [100.0]})
    monkeypatch.setattr(at_mod, "decide_council", lambda df, settings=None: {
        "verdict": "BUY", "confidence": 0.5})
    allow, decision = tr._council_gate("BUY", klines,
                                       {"use_decision_council": True, "council_min_confidence": 0.6})
    assert allow is False


async def test_council_gate_agree_passes(trader, monkeypatch):
    tr, fb, db = trader
    klines = pd.DataFrame({"open": [100.0], "high": [101.0], "low": [99.0],
                           "close": [100.0], "volume": [100.0]})
    monkeypatch.setattr(at_mod, "decide_council", lambda df, settings=None: {
        "verdict": "BUY", "confidence": 0.8})
    allow, decision = tr._council_gate("BUY", klines,
                                       {"use_decision_council": True, "council_min_confidence": 0.6})
    assert allow is True
    assert decision["confidence"] == 0.8


async def test_council_gate_includes_min_confidence_setting(trader, monkeypatch):
    tr, fb, db = trader
    klines = pd.DataFrame({"open": [100.0], "high": [101.0], "low": [99.0],
                           "close": [100.0], "volume": [100.0]})
    monkeypatch.setattr(at_mod, "decide_council", lambda df, settings=None: {
        "verdict": "BUY", "confidence": 0.7})
    allow, decision = tr._council_gate("BUY", klines,
                                       {"use_decision_council": True, "council_min_confidence": 0.8})
    assert allow is False


async def test_strength_gate_disabled_passes(trader):
    tr, fb, db = trader
    allow, info = tr._strength_gate({"strength": 0.1}, {"min_signal_strength": 0.0})
    assert allow is True
    assert info is None


async def test_strength_gate_default_off_passes(trader):
    tr, fb, db = trader
    allow, info = tr._strength_gate({"strength": 0.1}, {})
    assert allow is True


async def test_strength_gate_below_threshold_rejects(trader):
    tr, fb, db = trader
    allow, info = tr._strength_gate({"strength": 0.4}, {"min_signal_strength": 0.6})
    assert allow is False
    assert info["strength"] == 0.4
    assert info["threshold"] == 0.6


async def test_strength_gate_meets_threshold_passes(trader):
    tr, fb, db = trader
    allow, info = tr._strength_gate({"strength": 0.8}, {"min_signal_strength": 0.6})
    assert allow is True
    assert info is None


async def test_strength_gate_missing_strength_rejects_when_enabled(trader):
    tr, fb, db = trader
    allow, info = tr._strength_gate({"signal": "BUY"}, {"min_signal_strength": 0.5})
    assert allow is False
    assert info["strength"] == 0.0


async def test_rank_by_score_reorders_by_score(trader, monkeypatch):
    tr, fb, db = trader
    klines = pd.DataFrame({"open": [100.0] * 30, "high": [101.0] * 30,
                           "low": [99.0] * 30, "close": [100.0] * 30,
                           "volume": [100.0] * 30})

    async def fetch(candidates):
        return {s: klines for s in candidates}

    scores = iter([3.0, 1.0, 2.0])
    monkeypatch.setattr(tr, "_fetch_klines_batch", fetch)
    monkeypatch.setattr(at_mod, "score_symbol", lambda df: {"score": next(scores)})
    klines.name = None
    scored = await tr._rank_by_score(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    assert scored == ["BTCUSDT", "SOLUSDT", "ETHUSDT"]


async def test_rank_by_score_keeps_unscored_at_end(trader, monkeypatch):
    tr, fb, db = trader

    async def fetch(candidates):
        return {}

    monkeypatch.setattr(tr, "_fetch_klines_batch", fetch)
    monkeypatch.setattr(at_mod, "score_symbol", lambda df: {"score": 1.0})
    scored = await tr._rank_by_score(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    assert scored == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


async def test_rank_by_score_drops_low_data(trader, monkeypatch):
    tr, fb, db = trader
    short = pd.DataFrame({"open": [100.0] * 10, "high": [101.0] * 10,
                          "low": [99.0] * 10, "close": [100.0] * 10,
                          "volume": [100.0] * 10})

    async def fetch(candidates):
        return {"BTCUSDT": short, "ETHUSDT": short}

    monkeypatch.setattr(tr, "_fetch_klines_batch", fetch)
    called = []
    monkeypatch.setattr(at_mod, "score_symbol",
                        lambda df: called.append(1) or {"score": 1.0})
    scored = await tr._rank_by_score(["BTCUSDT", "ETHUSDT"])
    assert called == []
    assert scored == ["BTCUSDT", "ETHUSDT"]


async def test_refresh_ranking_uses_score_when_enabled(trader, monkeypatch):
    tr, fb, db = trader
    monkeypatch.setattr(tr, "rank_symbols", lambda limit=500: ["BTCUSDT", "ETHUSDT"])
    monkeypatch.setattr(strat_settings, "get_settings",
                        lambda: {"use_score_ranking": True})
    klines = pd.DataFrame({"open": [100.0] * 30, "high": [101.0] * 30,
                           "low": [99.0] * 30, "close": [100.0] * 30,
                           "volume": [100.0] * 30})

    async def fetch(candidates):
        return {s: klines for s in candidates}

    scores = iter([1.0, 5.0])
    monkeypatch.setattr(tr, "_fetch_klines_batch", fetch)
    monkeypatch.setattr(at_mod, "score_symbol", lambda df: {"score": next(scores)})
    klines.name = None
    await tr._refresh_ranking()
    assert tr.priority == ["ETHUSDT", "BTCUSDT"]
    assert tr.top_symbols == ["ETHUSDT", "BTCUSDT"]


async def test_refresh_ranking_disabled_keeps_backtest_order(trader, monkeypatch):
    tr, fb, db = trader
    monkeypatch.setattr(tr, "rank_symbols", lambda limit=500: ["BTCUSDT", "ETHUSDT"])
    monkeypatch.setattr(strat_settings, "get_settings",
                        lambda: {"use_score_ranking": False})
    await tr._refresh_ranking()
    assert tr.priority == ["BTCUSDT", "ETHUSDT"]


async def test_ensure_data_freshness_backfills_stale(trader, monkeypatch):
    tr, fb, db = trader
    tr.priority = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    now = datetime.utcnow()

    def fake_load(symbol, interval="4h", data_dir=None, limit=None):
        if symbol == "BTCUSDT":
            idx = pd.DatetimeIndex([now - timedelta(hours=48)])
        elif symbol == "SOLUSDT":
            idx = pd.DatetimeIndex([now - timedelta(hours=1)])
        else:
            raise FileNotFoundError("missing")
        df = pd.DataFrame({"open": [100.0], "high": [101.0], "low": [99.0],
                           "close": [100.0], "volume": [1.0]}, index=idx)
        df.index = df.index.tz_localize("UTC")
        return df

    monkeypatch.setattr(at_mod.loader, "load_csv", fake_load)
    captured = {}

    async def fake_backfill(client, symbols, interval="4h", days=30,
                            data_dir=None, skip_stablecoins=True):
        captured["symbols"] = list(symbols)
        return {"written": list(symbols), "failed": [], "interval": interval,
                "days": days, "path": "/tmp"}

    monkeypatch.setattr(at_mod, "backfill_klines", fake_backfill)
    await tr._ensure_data_freshness()
    assert set(captured["symbols"]) == {"BTCUSDT", "ETHUSDT"}


async def test_ensure_data_freshness_all_fresh(trader, monkeypatch):
    tr, fb, db = trader
    tr.priority = ["BTCUSDT"]
    now = datetime.utcnow()
    idx = pd.DatetimeIndex([now - timedelta(hours=1)]).tz_localize("UTC")
    df = pd.DataFrame({"open": [100.0], "high": [101.0], "low": [99.0],
                       "close": [100.0], "volume": [1.0]}, index=idx)
    monkeypatch.setattr(at_mod.loader, "load_csv",
                        lambda symbol, interval="4h", data_dir=None, limit=None: df)
    called = []

    async def fake_backfill(*a, **k):
        called.append(1)

    monkeypatch.setattr(at_mod, "backfill_klines", fake_backfill)
    await tr._ensure_data_freshness()
    assert called == []
