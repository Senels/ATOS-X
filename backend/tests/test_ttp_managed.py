"""TTPTSL tam durum makinesi (analyze_full/manage) + engine uyumu testleri."""
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from app.backtest.engine import BacktestEngine
from app.strategy.ttp import TtpTsl

_OT_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "optimize_ttp.py"


def _load_ot():
    spec = importlib.util.spec_from_file_location("optimize_ttp_mod", _OT_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _frame(closes):
    closes = [float(c) for c in closes]
    highs = [c * 1.005 for c in closes]
    lows = [c * 0.995 for c in closes]
    opens = [closes[i - 1] if i else closes[i] for i in range(len(closes))]
    idx = pd.date_range("2025-01-01", periods=len(closes), freq="4h")
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": [1000.0] * len(closes),
    }, index=idx)


def _bot(**ttp_patch):
    patch = {"active_strategy": "ttp", "ttp": {
        "fast_ma_len": 3, "slow_ma_len": 5, "atr_len": 3,
        "sl_method": "perc", "sl_long_perc": 0.06, "sl_short_perc": 0.05,
        "tp_method": "perc", "tp_long_perc": 0.09, "tp_short_perc": 0.08,
        "sl_trail_mode": "TP", "be_enabled": True, "tp_qty_pct": 0.5,
        "tp_trail_enabled": False, "dist_method": "perc",
        "dist_perc": 0.0284, "dist_atr_mul": 3.4,
    }}
    patch["ttp"].update(ttp_patch)
    return TtpTsl(patch)


def _replay(orders, df):
    """analyze_full cikis direktiflerini run_backtest muhasebesiyle geri sarar."""
    net = gross_p = gross_l = 0.0
    trades = wins = 0
    active = False
    direction = 0
    entry = 0.0
    qty = 1.0
    for i in range(len(df)):
        sig = int(orders["signal"].iloc[i])
        ex = orders["exit"].iloc[i]
        if not active:
            if sig in (1, -1):
                active = True
                direction = sig
                entry = float(df["close"].iloc[i])
                qty = 1.0
            continue
        if ex:
            ep = float(orders["exit_price"].iloc[i])
            eq = float(orders["exit_qty_pct"].iloc[i])
            ret = (ep - entry) / entry if direction == 1 else (entry - ep) / entry
            contrib = ret * eq * 100.0
            net += contrib
            trades += 1
            if ret > 0:
                wins += 1
                gross_p += contrib
            else:
                gross_l += -contrib
            qty -= eq
            if ex in ("sl", "trail_tp", "reversal") or qty < 1e-12:
                active = False
                qty = 1.0
    return {
        "trades": trades, "wins": wins, "net_profit_pct": net,
        "gross_profit_pct": gross_p, "gross_loss_pct": gross_l,
    }


# ---------------------------------------------------------------------------
# analyze_full sozlesmesi
# ---------------------------------------------------------------------------
def test_ttp_analyze_full_contract():
    df = _frame([100.0] * 40)
    orders = _bot().analyze_full(df)["orders"]
    assert len(orders) == len(df)
    assert set(orders.columns) == {
        "signal", "sl", "tp", "strength", "in_position", "exit",
        "exit_qty_pct", "exit_price",
    }
    assert orders["signal"].isin([-1, 0, 1]).all()
    assert orders["in_position"].isin([True, False]).all()
    assert orders["exit"].isin(["", "sl", "tp_partial", "trail_tp", "reversal"]).all()
    assert orders["exit_qty_pct"].between(0.0, 1.0).all()


def test_ttp_analyze_full_keeps_legacy_analyze_contract():
    df = _frame([100.0] * 40)
    legacy = _bot().analyze(df)["orders"]
    assert set(legacy.columns) == {"signal", "sl", "tp", "strength"}


def test_ttp_analyze_full_partial_then_sl():
    df = _frame([100.0] * 10 + [195.0, 215.0, 215.0, 215.0, 215.0] + [120.0] * 10)
    orders = _bot().analyze_full(df)["orders"]
    exits = [(i, orders["exit"].iloc[i]) for i in range(len(df)) if orders["exit"].iloc[i]]
    assert exits == [(11, "tp_partial"), (15, "sl")]
    assert orders["exit_qty_pct"].iloc[11] == pytest.approx(0.5)
    assert orders["exit_qty_pct"].iloc[15] == pytest.approx(0.5)
    assert orders["exit_price"].iloc[11] == pytest.approx(195.0 * 1.09)
    assert bool(orders["in_position"].iloc[10]) is True
    assert bool(orders["in_position"].iloc[16]) is False


def test_ttp_analyze_full_reversal():
    df = _frame([100.0] * 10 + [140.0] * 5 + [90.0] * 10)
    orders = _bot().analyze_full(df)["orders"]
    exits = [orders["exit"].iloc[i] for i in range(len(df)) if orders["exit"].iloc[i]]
    # 140 -> 90 dususu SL'yi vurur (pozisyon %6 SL ile); reversal varsa SL once olur
    assert exits and set(exits) <= {"sl", "reversal"}


# ---------------------------------------------------------------------------
# optimize_ttp.py run_backtest ile birebir parite
# ---------------------------------------------------------------------------
def test_ttp_analyze_full_parity_real_btc(btc_df):
    """Gercek BTC verisinde (varsayilan OOS parametreleri) optimizer ile birebir."""
    bot = TtpTsl()
    orders = bot.analyze_full(btc_df)["orders"]
    replay = _replay(orders, btc_df)
    ref = _load_ot().run_backtest(btc_df, bot._params())
    assert replay["trades"] == ref["trades"]
    assert replay["wins"] == ref["wins"]
    assert replay["net_profit_pct"] == pytest.approx(ref["net_profit_pct"], rel=1e-9)
    assert replay["gross_profit_pct"] == pytest.approx(ref["gross_profit_pct"], rel=1e-9)
    assert replay["gross_loss_pct"] == pytest.approx(ref["gross_loss_pct"], rel=1e-9)


@pytest.mark.parametrize("symbol", [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "DOGEUSDT", "KSMUSDT", "PORTALUSDT", "DODOXUSDT",
])
def test_ttp_analyze_full_parity_real_multi_symbol(symbol):
    """Gercek veride coklu sembolde optimizer ile birebir (CSV yoksa skip)."""
    from app.data import loader

    data_dir = loader.DEFAULT_DATA_DIR / "futures_4h_data"
    if not (data_dir / f"{symbol}_4h.csv").exists():
        pytest.skip(f"{symbol}_4h.csv yok")
    df = loader.load_csv(symbol, "4h")
    bot = TtpTsl()
    orders = bot.analyze_full(df)["orders"]
    replay = _replay(orders, df)
    ref = _load_ot().run_backtest(df, bot._params())
    assert replay["trades"] == ref["trades"]
    assert replay["wins"] == ref["wins"]
    assert replay["net_profit_pct"] == pytest.approx(ref["net_profit_pct"], rel=1e-9)
    assert replay["gross_profit_pct"] == pytest.approx(ref["gross_profit_pct"], rel=1e-9)
    assert replay["gross_loss_pct"] == pytest.approx(ref["gross_loss_pct"], rel=1e-9)


@pytest.mark.parametrize("series", [
    [100.0] * 10 + [195.0, 215.0, 215.0, 215.0, 215.0] + [120.0] * 10,
    [100.0] * 12 + [90.0] * 6 + [130.0] * 6 + [80.0] * 8,
    [100.0] * 8 + [160.0] * 4 + [170.0] * 4 + [110.0] * 6 + [150.0] * 4,
])
@pytest.mark.parametrize("ttp_patch", [
    {},
    {"tp_trail_enabled": True, "dist_perc": 0.03},
    {"sl_trail_mode": "ON", "tp_qty_pct": 1.0},
    {"be_enabled": False, "tp_qty_pct": 0.3, "tp_trail_enabled": True},
])
def test_ttp_analyze_full_parity_with_optimize(series, ttp_patch):
    df = _frame(series)
    bot = _bot(**ttp_patch)
    orders = bot.analyze_full(df)["orders"]
    replay = _replay(orders, df)
    ref = _load_ot().run_backtest(df, bot._params())
    assert replay["trades"] == ref["trades"]
    assert replay["wins"] == ref["wins"]
    assert replay["net_profit_pct"] == pytest.approx(ref["net_profit_pct"])
    assert replay["gross_profit_pct"] == pytest.approx(ref["gross_profit_pct"])
    assert replay["gross_loss_pct"] == pytest.approx(ref["gross_loss_pct"])


# ---------------------------------------------------------------------------
# engine managed mod
# ---------------------------------------------------------------------------
def test_ttp_engine_managed_partial_close():
    df = _frame([100.0] * 10 + [195.0, 215.0, 215.0, 215.0, 215.0] + [120.0] * 10)
    orders = _bot().analyze_full(df)["orders"]
    m = BacktestEngine(initial_equity=10000, risk_per_trade=0.02).run(df, orders, "4h")
    assert m["total_trades"] == 2
    reasons = sorted(t["reason"] for t in m["trades"])
    assert reasons == ["stop_loss", "take_profit"]
    # kismi + kalan yarimlar esit ve risk-bazli boyuta uygun (slippage dahil)
    t0, t1 = m["trades"]
    assert t0["qty"] == pytest.approx(t1["qty"])
    entry_px = 195.0 * (1 + 0.0001)  # engine sinyal+1 bar acilisinda slippage uygular
    size = BacktestEngine(initial_equity=10000, risk_per_trade=0.02).position_size(
        entry_px, 195.0 * 0.94, 10000.0
    )["qty"]
    assert t0["qty"] + t1["qty"] == pytest.approx(size)


def test_ttp_engine_managed_sl_only():
    df = _frame([100.0] * 10 + [140.0] * 5 + [90.0] * 10)
    orders = _bot().analyze_full(df)["orders"]
    m = BacktestEngine(initial_equity=10000, risk_per_trade=0.02).run(df, orders, "4h")
    assert m["total_trades"] == 1
    assert m["trades"][0]["reason"] == "stop_loss"


def test_ttp_engine_managed_uses_per_bar_trailing_sl():
    # Mode ON: SL yukselen high'i takip eder; statik SL (130*0.94) dokunulmaz,
    # trailed SL (yuksek fiyattan) tetiklenir -> engine per-bar sl kullaniyor
    df = _frame([100.0] * 10 + [130.0, 138.0, 138.0, 129.0])
    orders = _bot(sl_trail_mode="ON").analyze_full(df)["orders"]
    m = BacktestEngine(initial_equity=10000, risk_per_trade=0.02).run(df, orders, "4h")
    assert m["total_trades"] == 1
    assert m["trades"][0]["reason"] == "stop_loss"
    # trailed SL = son yuksek barin yuksekligi * (1 - 0.06)
    assert m["trades"][0]["sl"] == pytest.approx(138.0 * 1.005 * 0.94)
    assert m["trades"][0]["sl"] > 130.0 * 0.94


def test_ttp_engine_managed_end_of_test_closes_open():
    # Cikis yok: pozisyon test sonuna kadar tasinir ve end_of_test ile kapanir
    df = _frame([100.0] * 10 + [130.0] * 15)
    orders = _bot().analyze_full(df)["orders"]
    assert bool(orders["in_position"].iloc[-1]) is True
    m = BacktestEngine(initial_equity=10000, risk_per_trade=0.02).run(df, orders, "4h")
    assert m["total_trades"] == 1
    assert m["trades"][0]["reason"] == "end_of_test"


# ---------------------------------------------------------------------------
# manage (canli)
# ---------------------------------------------------------------------------
def test_ttp_manage_returns_partial_then_final_exit():
    df = _frame([100.0] * 10 + [195.0, 215.0, 215.0, 215.0, 215.0] + [120.0] * 10)
    bot = _bot()
    entry_ts, entry_price, side = df.index[10], 195.0, "BUY"
    first = bot.manage(df, entry_ts, entry_price, side, 100.0)
    assert first["exit"] == "tp_partial"
    assert first["exit_qty_pct"] == pytest.approx(0.5)
    assert first["active"] is True
    assert first["exit_price"] == pytest.approx(195.0 * 1.09)
    second = bot.manage(df, entry_ts, entry_price, side, 50.0, tp_already_hit=True)
    assert second["exit"] == "sl"
    assert second["exit_qty_pct"] == pytest.approx(1.0)
    assert second["active"] is False
    full = bot.analyze_full(df)["orders"]
    sl_exit = full["exit_price"][full["exit"] == "sl"].iloc[0]
    assert second["exit_price"] == pytest.approx(float(sl_exit))


def test_ttp_manage_tp_already_hit_prevents_second_partial():
    df = _frame([100.0] * 10 + [195.0, 215.0, 215.0, 215.0, 215.0] + [120.0] * 10)
    bot = _bot()
    res = bot.manage(df, df.index[10], 195.0, "BUY", 50.0, tp_already_hit=True)
    assert res["exit"] != "tp_partial"


def test_ttp_manage_partial_qty_pct_is_fraction_not_quantity():
    # exit_qty_pct her zaman kesir olmali (mutlak miktar degil)
    df = _frame([100.0] * 10 + [195.0, 215.0, 215.0, 215.0, 215.0] + [120.0] * 10)
    res = _bot().manage(df, df.index[10], 195.0, "BUY", 250.0)
    assert res["exit"] == "tp_partial"
    assert 0.0 < res["exit_qty_pct"] < 1.0
    assert res["exit_qty_pct"] == pytest.approx(0.5)


def test_ttp_manage_active_trails_sl():
    df = _frame([100.0] * 10 + [130.0] * 15)
    res = _bot().manage(df, df.index[10], 100.0, "BUY", 1.0)
    assert res["active"] is True
    assert res["exit"] == ""
    assert res["sl"] is not None
    assert res["sl"] >= 100.0  # be_enabled: SL giris seviyesine cekilir


def test_ttp_manage_full_tp_qty_pct_closes():
    df = _frame([100.0] * 10 + [195.0, 215.0, 215.0, 215.0, 215.0] + [120.0] * 10)
    bot = _bot(tp_qty_pct=1.0)
    res = bot.manage(df, df.index[10], 195.0, "BUY", 1.0)
    assert res["exit"] == "tp_partial"
    assert res["active"] is False
    assert res["exit_qty_pct"] == pytest.approx(1.0)


def test_ttp_manage_short_side_reversal():
    df = _frame([100.0] * 10 + [60.0] * 5 + [110.0] * 8)
    res = _bot().manage(df, df.index[10], 60.0, "SELL", 1.0)
    # 60 -> 110 yukselisi SELL'de SL'yi vurur ya da reversal (cross_up) cikisi olur
    assert res["exit"] in ("sl", "reversal")
    assert res["active"] is False


def test_ttp_manage_tolerates_naive_timestamp_on_tz_aware_index():
    df = _frame([100.0] * 10 + [195.0, 215.0, 215.0, 215.0, 215.0] + [120.0] * 10)
    df.index = df.index.tz_localize("UTC")
    bot = _bot()
    naive = str(df.index[10].tz_localize(None))
    res = bot.manage(df, naive, 195.0, "BUY", 1.0)
    assert res["exit"] == "tp_partial"


def test_ttp_manage_tolerates_mid_bar_open_time_fallback():
    # restore sonrasi `open_time` fallback'i: naive, mikro saniyeli, bar ortasi
    df = _frame([100.0] * 10 + [195.0, 215.0, 215.0, 215.0, 215.0] + [120.0] * 10)
    df.index = df.index.tz_localize("UTC")
    bot = _bot()
    aware = df.index[10] + pd.Timedelta(minutes=7, seconds=30, microseconds=123456)
    naive_mid = str(aware.tz_localize(None))
    res = bot.manage(df, naive_mid, 195.0, "BUY", 1.0)
    assert res["exit"] == "tp_partial"
    assert res["exit_bar_idx"] == 11
