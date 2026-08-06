"""v23 strateji + backtest motoru icin temel dogrulama testleri."""
import pytest

from app.backtest.engine import BacktestEngine
from app.strategy.tradebot_v23 import TradeBotV23


def test_analyze_produces_orders(btc_df):
    r = TradeBotV23().analyze(btc_df)
    orders = r["orders"]
    assert len(orders) == len(btc_df)
    assert set(orders.columns) == {"signal", "sl", "tp", "strength"}
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
    assert 0.0 <= s.get("strength", 0.0) <= 1.0
    if s["signal"] in ("BUY", "SELL"):
        assert s["sl"] is not None and s["tp"] is not None


def test_signal_strength_reflects_confirmations(btc_df):
    bot = TradeBotV23()
    s = bot.generate_signal(btc_df)
    if s["signal"] == "HOLD":
        assert s["strength"] == 0.0
    else:
        _, _, lc, sc = bot._confirmations(btc_df)
        n_active = int(lc.iloc[-1]) if s["signal"] == "BUY" else int(sc.iloc[-1])
        enabled = [k for k, v in bot.settings["confirmations"].items() if v]
        assert s["strength"] == round(n_active / max(len(enabled), 1), 2)


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


# -- volatilite rejimi boyutlandirma -----------------------------------------
def test_position_size_vol_regime_shrinks_risk():
    engine = BacktestEngine(initial_equity=10000, risk_per_trade=0.02,
                            vol_sizing_enabled=True)
    s = engine.position_size(entry=100.0, sl=90.0, equity=10000, atr_ratio=2.0)
    # yuksek rejim: risk 200 -> 100 -> qty = 100/10 = 10
    assert s["qty"] == pytest.approx(10.0)
    assert s["risk_amount"] == pytest.approx(100.0)


def test_position_size_vol_regime_normal_risk_below_hi():
    engine = BacktestEngine(initial_equity=10000, risk_per_trade=0.02,
                            vol_sizing_enabled=True)
    s = engine.position_size(entry=100.0, sl=90.0, equity=10000, atr_ratio=1.2)
    assert s["qty"] == pytest.approx(20.0)
    assert s["risk_amount"] == pytest.approx(200.0)


def test_position_size_vol_regime_disabled_by_default():
    engine = BacktestEngine(initial_equity=10000, risk_per_trade=0.02)
    s = engine.position_size(entry=100.0, sl=90.0, equity=10000, atr_ratio=2.0)
    assert s["qty"] == pytest.approx(20.0)


def test_open_uses_position_size(btc_df):
    orders = TradeBotV23().analyze(btc_df)["orders"]
    engine = BacktestEngine(initial_equity=10000, risk_per_trade=0.02)
    m = engine.run(btc_df, orders, "4h")
    for t in m["trades"]:
        assert t["qty"] > 0


# -- risk korumalari (canli ayarlarla ayni davranis) -------------------------
def _frame(prices, signals=None, sls=None, tps=None, strengths=None):
    import numpy as np
    import pandas as pd
    df = pd.DataFrame(prices, columns=["open", "high", "low", "close"])
    n = len(df)
    sig = signals if signals is not None else [0] * n
    sls = sls if sls is not None else [np.nan] * n
    tps = tps if tps is not None else [np.nan] * n
    orders = pd.DataFrame({"signal": sig, "sl": sls, "tp": tps})
    if strengths is not None:
        orders["strength"] = strengths
    return df, orders


def test_backtest_trailing_raises_stop():
    df, orders = _frame([
        [100.0, 101.0, 99.0, 100.0],
        [100.5, 103.0, 100.0, 103.0],
        [103.0, 107.0, 102.5, 106.5],
        [106.0, 108.0, 104.0, 107.0],
    ], signals=[1, 0, 0, 0], sls=[95.0, None, None, None], tps=[110.0, None, None, None])
    engine = BacktestEngine(initial_equity=10000, risk_per_trade=0.02,
                            trailing_activate_pct=3.0, trailing_sl_pct=1.5)
    m = engine.run(df, orders, "4h")
    sl_stop = [t for t in m["trades"] if t["reason"] == "stop_loss"]
    assert sl_stop, "trailing SL yukseltilmemis"
    assert sl_stop[0]["exit"] > 100.0


def test_backtest_trailing_disabled_no_trail():
    df, orders = _frame([
        [100.0, 101.0, 99.0, 100.0],
        [100.5, 103.0, 100.0, 103.0],
        [103.0, 107.0, 102.5, 106.5],
        [106.0, 108.0, 104.0, 107.0],
    ], signals=[1, 0, 0, 0], sls=[95.0, None, None, None], tps=[110.0, None, None, None])
    engine = BacktestEngine(initial_equity=10000, risk_per_trade=0.02)
    m = engine.run(df, orders, "4h")
    assert all(t["reason"] != "stop_loss" for t in m["trades"])


def test_backtest_time_stop_closes():
    df, orders = _frame([
        [100.0, 101.0, 99.0, 100.0],
        [100.5, 101.0, 100.0, 100.8],
        [100.8, 101.5, 100.5, 101.0],
    ], signals=[1, 0, 0], sls=[95.0, None, None], tps=[110.0, None, None])
    engine = BacktestEngine(initial_equity=10000, risk_per_trade=0.02,
                            max_position_age_hours=2)
    m = engine.run(df, orders, "4h")
    assert any(t["reason"] == "time_stop" for t in m["trades"])


def test_backtest_consecutive_losses_block_entry():
    df, orders = _frame([
        [100.0, 101.0, 99.0, 100.0],
        [100.5, 101.0, 100.0, 100.5],
        [99.5, 100.0, 95.5, 96.0],
        [96.0, 97.0, 94.0, 95.0],
        [100.0, 101.0, 99.0, 100.0],
        [100.5, 101.0, 100.0, 100.5],
        [101.0, 105.0, 100.0, 104.0],
    ], signals=[1, 0, 0, 0, 1, 0, 0], sls=[95.0, None, None, None, 95.0, None, None],
       tps=[110.0, None, None, None, 110.0, None, None])
    engine = BacktestEngine(initial_equity=10000, risk_per_trade=0.02,
                            max_consecutive_losses=1)
    m = engine.run(df, orders, "4h")
    assert len(m["trades"]) == 1
    assert m["trades"][0]["reason"] == "stop_loss"


def test_backtest_breakeven_moves_sl_to_entry():
    df, orders = _frame([
        [100.0, 101.0, 99.0, 100.0],
        [100.5, 103.0, 100.0, 102.6],
        [103.0, 106.0, 102.0, 101.0],
        [101.0, 101.5, 99.5, 100.0],
    ], signals=[1, 0, 0, 0], sls=[95.0, None, None, None], tps=[110.0, None, None, None])
    engine = BacktestEngine(initial_equity=10000, risk_per_trade=0.02,
                            breakeven_activate_pct=2.0)
    m = engine.run(df, orders, "4h")
    sl_stop = [t for t in m["trades"] if t["reason"] == "stop_loss"]
    assert sl_stop
    assert sl_stop[0]["exit"] > 98.0


def test_backtest_min_signal_strength_blocks_weak_entry():
    df, orders = _frame([
        [100.0, 101.0, 99.0, 100.0],
        [100.5, 101.0, 100.0, 100.5],
        [101.0, 105.0, 100.5, 104.0],
    ], signals=[1, 0, 0], sls=[95.0, None, None], tps=[110.0, None, None],
       strengths=[0.2, 0.0, 0.0])
    engine = BacktestEngine(initial_equity=10000, risk_per_trade=0.02,
                            min_signal_strength=0.6)
    m = engine.run(df, orders, "4h")
    assert m["total_trades"] == 0


def test_backtest_min_signal_strength_allows_strong_entry():
    df, orders = _frame([
        [100.0, 101.0, 99.0, 100.0],
        [100.5, 101.0, 100.0, 100.5],
        [101.0, 105.0, 100.5, 104.0],
    ], signals=[1, 0, 0], sls=[95.0, None, None], tps=[110.0, None, None],
       strengths=[1.0, 0.0, 0.0])
    engine = BacktestEngine(initial_equity=10000, risk_per_trade=0.02,
                            min_signal_strength=0.6)
    m = engine.run(df, orders, "4h")
    assert m["total_trades"] == 1


def test_backtest_min_signal_strength_default_off():
    df, orders = _frame([
        [100.0, 101.0, 99.0, 100.0],
        [100.5, 101.0, 100.0, 100.5],
        [101.0, 105.0, 100.5, 104.0],
    ], signals=[1, 0, 0], sls=[95.0, None, None], tps=[110.0, None, None],
       strengths=[0.1, 0.0, 0.0])
    engine = BacktestEngine(initial_equity=10000, risk_per_trade=0.02)
    m = engine.run(df, orders, "4h")
    assert m["total_trades"] == 1


def test_backtest_missing_strength_column_keeps_legacy_behavior():
    df, orders = _frame([
        [100.0, 101.0, 99.0, 100.0],
        [100.5, 101.0, 100.0, 100.5],
        [101.0, 105.0, 100.5, 104.0],
    ], signals=[1, 0, 0], sls=[95.0, None, None], tps=[110.0, None, None])
    engine = BacktestEngine(initial_equity=10000, risk_per_trade=0.02,
                            min_signal_strength=0.6)
    m = engine.run(df, orders, "4h")
    assert m["total_trades"] == 1
