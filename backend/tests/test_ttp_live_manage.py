"""TTPTSL canli pozisyon yonetimi (manage) entegrasyon testleri.

`check_positions` ttp modunda `_ttp_manage_positions`'i calistirir; bu testler
gercek akisi (process_signals -> acilis -> manage -> kismi/tam kapanis) paper
modda, FakeBinance uzerinden dogrular.
"""
import pandas as pd
import pytest

import app.strategy.auto_trader as at_mod
from app.core.database import Database
from app.strategy import settings as ss

_P = {
    "fast_ma_len": 3, "slow_ma_len": 5, "atr_len": 3,
    "sl_method": "perc", "sl_long_perc": 0.06, "sl_short_perc": 0.05,
    "tp_method": "perc", "tp_long_perc": 0.09, "tp_short_perc": 0.08,
    "sl_trail_mode": "TP", "be_enabled": True, "tp_qty_pct": 0.5,
    "tp_trail_enabled": False, "dist_method": "perc",
    "dist_perc": 0.0284, "dist_atr_mul": 3.4,
}


def _klines_frame(closes):
    closes = [float(c) for c in closes]
    highs = [c * 1.005 for c in closes]
    lows = [c * 0.995 for c in closes]
    opens = [closes[i - 1] if i else closes[i] for i in range(len(closes))]
    idx = pd.date_range("2025-01-01", periods=len(closes), freq="4h", tz="UTC")
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": [1000.0] * len(closes),
    }, index=idx)


class FakeBinanceKlines:
    def __init__(self, df):
        self.df = df
        self.client = None
        self.testnet = False

    async def connect(self):
        self.client = True
        return True

    async def load_all_symbols(self):
        return ["BTCUSDT"]

    async def get_all_tickers(self):
        return {"BTCUSDT": 0.0}

    async def get_klines(self, symbol, interval, limit):
        return self.df

    async def get_price(self, symbol="BTCUSDT"):
        return 0.0

    async def place_market_order(self, symbol, side, quantity):
        return {"symbol": symbol, "side": side, "quantity": quantity, "paper": True}

    async def close_position(self, symbol):
        return {"symbol": symbol}


def _make_trader(tmp_path, monkeypatch, df, ttp_patch):
    db = Database(str(tmp_path / "at.db"))
    monkeypatch.setattr(at_mod, "Database", lambda *a, **k: db)
    prev = ss.get_settings()
    ss.update_settings({"active_strategy": "ttp", "ttp": ttp_patch})
    tr = at_mod.AutoTrader(FakeBinanceKlines(df), paper=True)
    tr.trading_symbols = ["BTCUSDT"]
    return tr, prev


async def _open_signal(tr, df, bar, price, sl, tp):
    await tr.process_signals([{
        "symbol": "BTCUSDT", "signal": "BUY", "price": price,
        "sl": sl, "tp": tp, "reason": "test", "strength": 1.0,
        "entry_ts": str(df.index[bar]),
    }])


async def test_ttp_live_partial_then_full_close(tmp_path, monkeypatch):
    df = _klines_frame([100.0] * 20 + [195.0, 215.0, 215.0, 215.0, 215.0] + [120.0] * 10)
    tr, prev = _make_trader(tmp_path, monkeypatch, df, _P)
    try:
        await _open_signal(tr, df, 20, 195.0, 195.0 * 0.94, 195.0 * 1.09)
        qty0 = tr.active_positions["BTCUSDT"]["quantity"]
        assert qty0 > 0

        await tr.check_positions({})
        pos = tr.active_positions.get("BTCUSDT")
        assert pos is not None
        assert pos["ttp_tp_hit"] is True
        assert pos["quantity"] == pytest.approx(qty0 * 0.5)
        assert len(tr.trade_history) == 1
        assert tr.trade_history[0]["reason"] == "take_profit"

        await tr.check_positions({})
        assert "BTCUSDT" not in tr.active_positions
        assert len(tr.trade_history) == 2
        assert tr.trade_history[-1]["reason"] == "stop_loss"
    finally:
        ss.update_settings(prev)


async def test_ttp_live_sl_exit(tmp_path, monkeypatch):
    df = _klines_frame([100.0] * 30 + [130.0, 120.0])
    tr, prev = _make_trader(tmp_path, monkeypatch, df, _P)
    try:
        await _open_signal(tr, df, 30, 130.0, 130.0 * 0.94, 130.0 * 1.09)
        await tr.check_positions({})
        assert "BTCUSDT" not in tr.active_positions
        assert len(tr.trade_history) == 1
        assert tr.trade_history[0]["reason"] == "stop_loss"
        assert tr.trade_history[0]["exit"] == pytest.approx(130.0 * 0.94)
    finally:
        ss.update_settings(prev)


async def test_ttp_live_no_exit_refreshes_trailing_sl(tmp_path, monkeypatch):
    p = dict(_P, sl_trail_mode="ON")
    df = _klines_frame([100.0] * 30 + [130.0, 138.0, 138.0, 138.0])
    tr, prev = _make_trader(tmp_path, monkeypatch, df, p)
    try:
        await _open_signal(tr, df, 30, 130.0, 130.0 * 0.94, 130.0 * 1.09)
        await tr.check_positions({})
        pos = tr.active_positions.get("BTCUSDT")
        assert pos is not None
        assert len(tr.trade_history) == 0
        # Mode ON: SL yukselen high'i izler (138 * 1.005 * 0.94)
        assert pos["sl"] == pytest.approx(138.0 * 1.005 * 0.94)
        assert pos["sl"] > 130.0 * 0.94
    finally:
        ss.update_settings(prev)
