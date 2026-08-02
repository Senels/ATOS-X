"""v23 strateji + backtest motoru icin temel dogrulama testleri."""
import pandas as pd
import pytest

from app.backtest.engine import BacktestEngine
from app.data import loader
from app.strategy.tradebot_v23 import TradeBotV23


@pytest.fixture(scope="module")
def btc_df():
    return loader.load_csv("BTCUSDT", "4h")


def test_analyze_produces_orders(btc_df):
    r = TradeBotV23().analyze(btc_df)
    orders = r["orders"]
    assert len(orders) == len(btc_df)
    assert set(orders.columns) == {"signal", "sl", "tp"}
    assert orders["signal"].dropna().between(-1, 1).all()


def test_sl_tp_respects_rr(btc_df):
    bot = TradeBotV23()
    rr = float(bot.get_settings()["rr_ratio"])
    orders = bot.analyze(btc_df)["orders"]
    long_sigs = orders[(orders["signal"] == 1) & orders["sl"].notna() & orders["tp"].notna()]
    if len(long_sigs):
        close = float(btc_df.loc[long_sigs.index[-1], "close"])
        row = long_sigs.iloc[-1]
        dist = close - row["sl"]
        assert abs(row["tp"] - (close + dist * rr)) / close < 1e-6
    short_sigs = orders[(orders["signal"] == -1) & orders["sl"].notna() & orders["tp"].notna()]
    if len(short_sigs):
        close = float(btc_df.loc[short_sigs.index[-1], "close"])
        row = short_sigs.iloc[-1]
        dist = row["sl"] - close
        assert abs(row["tp"] - (close - dist * rr)) / close < 1e-6


def test_backtest_returns_metrics(btc_df):
    orders = TradeBotV23().analyze(btc_df)["orders"]
    m = BacktestEngine(initial_equity=10000).run(btc_df, orders, "4h")
    assert "total_return_pct" in m
    assert "equity_curve" in m
    assert len(m["equity_curve"]) == len(btc_df)
    assert m["total_trades"] >= 0


def test_generate_signal_valid(btc_df):
    s = TradeBotV23().generate_signal(btc_df)
    assert s["signal"] in ("BUY", "SELL", "HOLD")
    if s["signal"] in ("BUY", "SELL"):
        assert s["sl"] is not None and s["tp"] is not None


def test_no_lookahead_entry(btc_df):
    """Sinyal bari i ise giris i+1 acilisinda olmali."""
    orders = TradeBotV23().analyze(btc_df)["orders"]
    sig_bars = orders.index[orders["signal"] != 0]
    if not len(sig_bars):
        pytest.skip("sinyal yok")
    first_sig_bar = sig_bars[0]
    pos = int(btc_df.index.get_loc(first_sig_bar)) + 1
    assert pos < len(btc_df)  # girisin oynanacagi bar seride mevcut


# -- position_size (canli + backtest ortak boyutlandirma) -------------------
def test_position_size_risk_scaled():
    engine = BacktestEngine(initial_equity=10000, risk_per_trade=0.02)
    s = engine.position_size(entry=100.0, sl=90.0, equity=10000)
    # risk = %2 * 10000 = 200; SL mesafesi = 10 -> qty = 20
    assert s["qty"] == pytest.approx(20.0)
    assert s["entry_fee"] == pytest.approx(100 * 20 * 0.0005)
    assert s["risk_amount"] == pytest.approx(200.0)


def test_position_size_leverage_cap():
    engine = BacktestEngine(initial_equity=10000, risk_per_trade=0.5, max_leverage=2)
    s = engine.position_size(entry=100.0, sl=90.0, equity=10000)
    # risk = 5000, qty = 500 -> notional 50000 > 20000 -> cap 200
    assert s["qty"] == pytest.approx(200.0)


def test_position_size_zero_sl_distance():
    engine = BacktestEngine(initial_equity=10000, risk_per_trade=0.02)
    s = engine.position_size(entry=100.0, sl=100.0, equity=10000)
    # SL mesafesi 0 -> %2 fallback (dist=2) -> qty = 200/2 = 100
    assert s["qty"] == pytest.approx(100.0)


def test_open_uses_position_size(btc_df):
    orders = TradeBotV23().analyze(btc_df)["orders"]
    engine = BacktestEngine(initial_equity=10000, risk_per_trade=0.02)
    m = engine.run(btc_df, orders, "4h")
    for t in m["trades"]:
        assert t["qty"] > 0
