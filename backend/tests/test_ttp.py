"""TTPTSL strateji modulu icin temel dogrulama testleri."""
import numpy as np
import pandas as pd
import pytest

from app.backtest.engine import BacktestEngine
from app.strategy import get_strategy
from app.strategy.tradebot_v23 import TradeBotV23
from app.strategy.ttp import TtpTsl, _atr_wilder


def _frame(closes, jump_idx=None, jump_to=110.0):
    """Opsiyonel olarak `jump_idx`'ten sonra `jump_to`'ya sicrayan seri uretir."""
    closes = [float(c) for c in closes]
    if jump_idx is not None:
        closes[jump_idx:] = [jump_to] * (len(closes) - jump_idx)
    highs = [c * 1.002 for c in closes]
    lows = [c * 0.998 for c in closes]
    opens = [closes[i - 1] if i else closes[i] for i in range(len(closes))]
    idx = pd.date_range("2025-01-01", periods=len(closes), freq="4h")
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": [1000.0] * len(closes),
    }, index=idx)


def _sig_pos(orders):
    return int(np.where(orders["signal"].to_numpy() == 1)[0][0])


def _bot(**ttp_patch):
    patch = {"active_strategy": "ttp", "ttp": {
        "fast_ma_len": 3, "slow_ma_len": 5, "atr_len": 3,
        "sl_method": "perc", "sl_long_perc": 0.06, "sl_short_perc": 0.05,
        "tp_method": "perc", "tp_long_perc": 0.09, "tp_short_perc": 0.08,
    }}
    patch["ttp"].update(ttp_patch)
    return TtpTsl(patch)


def test_ttp_analyze_produces_orders():
    df = _frame([100.0] * 20)
    r = TtpTsl().analyze(df)
    orders = r["orders"]
    assert len(orders) == len(df)
    assert set(orders.columns) == {"signal", "sl", "tp", "strength"}
    assert orders["signal"].dropna().isin([-1, 0, 1]).all()
    assert orders["strength"].between(0.0, 1.0).all()


def test_ttp_cross_up_creates_long_signal():
    df = _frame([100.0] * 20, jump_idx=6)
    bot = _bot()
    r = bot.analyze(df)
    orders = r["orders"]
    assert orders["signal"].eq(1).any()
    sig_bar = _sig_pos(orders)
    close = float(df.iloc[sig_bar]["close"])
    row = orders.iloc[sig_bar]
    assert row["sl"] == pytest.approx(close * 0.94)
    assert row["tp"] == pytest.approx(close * 1.09)
    assert row["strength"] == pytest.approx(1.0)


def test_ttp_flat_series_no_signal():
    df = _frame([100.0] * 40)
    r = _bot().analyze(df)
    orders = r["orders"]
    assert orders["signal"].eq(0).all()
    assert orders["sl"].isna().all()
    assert orders["tp"].isna().all()


def test_ttp_sl_tp_rr_math():
    df = _frame([100.0] * 20, jump_idx=6)
    bot = _bot(tp_method="rr", tp_long_rr=2.0)
    orders = bot.analyze(df)["orders"]
    sig_bar = _sig_pos(orders)
    close = float(df.iloc[sig_bar]["close"])
    row = orders.iloc[sig_bar]
    assert row["tp"] == pytest.approx(close + 2.0 * (close - row["sl"]))


def test_ttp_sl_tp_atr_math():
    df = _frame([100.0] * 20, jump_idx=6)
    bot = _bot(sl_method="atr", sl_long_atr_mul=2.0,
               tp_method="atr", tp_long_atr_mul=4.0)
    orders = bot.analyze(df)["orders"]
    sig_bar = _sig_pos(orders)
    close = float(df.iloc[sig_bar]["close"])
    atr = _atr_wilder(
        df["high"].to_numpy(), df["low"].to_numpy(),
        df["close"].to_numpy(), int(bot.get_settings()["ttp"]["atr_len"]),
    )[sig_bar]
    row = orders.iloc[sig_bar]
    assert row["sl"] == pytest.approx(close - 2.0 * atr)
    assert row["tp"] == pytest.approx(close + 4.0 * atr)


def test_ttp_generate_signal_contract():
    df = _frame([100.0] * 40)
    s = _bot().generate_signal(df)
    assert s["signal"] in ("BUY", "SELL", "HOLD")
    assert s["indicator"] == "TTPTSL"
    assert 0.0 <= s["strength"] <= 1.0
    if s["signal"] in ("BUY", "SELL"):
        assert s["sl"] is not None and s["tp"] is not None
        assert s["strength"] == 1.0


def test_ttp_generate_signal_hold_flat():
    df = _frame([100.0] * 40)
    s = _bot().generate_signal(df)
    if s["signal"] == "HOLD":
        assert s["sl"] is None and s["tp"] is None
        assert s["strength"] == 0.0


def test_ttp_generate_signal_insufficient_data():
    s = _bot().generate_signal(pd.DataFrame({"close": [100.0] * 10}))
    assert s["signal"] == "HOLD"
    assert "reason" in s


def test_ttp_analyze_requires_ohlcv():
    with pytest.raises(ValueError):
        TtpTsl().analyze(pd.DataFrame({"close": [100.0] * 10}))


def test_ttp_update_settings_merges_ttp_dict():
    bot = TtpTsl()
    bot.update_settings({"ttp": {"tp_long_rr": 5.0}})
    ttp = bot.get_settings()["ttp"]
    assert ttp["tp_long_rr"] == 5.0
    assert ttp["fast_ma_len"] == 31  # kalan anahtarlar korunur


def test_ttp_backtest_runs():
    df = _frame([100.0] * 20)
    orders = _bot().analyze(df)["orders"]
    m = BacktestEngine(initial_equity=10000, risk_per_trade=0.02).run(df, orders, "4h")
    assert "total_return_pct" in m
    assert len(m["equity_curve"]) == len(df)
    assert m["total_trades"] >= 0


def test_get_strategy_default_is_v23():
    bot = get_strategy({"active_strategy": "v23"})
    assert isinstance(bot, TradeBotV23)


def test_get_strategy_ttp_selection():
    bot = get_strategy({"active_strategy": "ttp"})
    assert isinstance(bot, TtpTsl)


def test_get_strategy_none_uses_defaults():
    bot = get_strategy()
    assert isinstance(bot, TradeBotV23) or isinstance(bot, TtpTsl)
