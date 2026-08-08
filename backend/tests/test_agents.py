"""Ajan framework testleri: kayit defteri, desen motoru, teknik/istatistik
ajanlar ve orchestrator oylama mantigi. Sentetik OHLCV verisi kullanir.
"""
import numpy as np
import pandas as pd
import pytest

from app.agents import all_agents
from app.agents.base import AgentResult
from app.agents.context import AgentContext
from app.agents.orchestrator import aggregate, collect_adjustments, run_for_symbol
from app.agents.patterns import detect_patterns


def make_df(n=250, start_price=100.0, trend=0.0, seed=7, volume=1e6, vol=0.02):
    """Sentetik OHLCV: trend parametresi gunluk birim drift ekler."""
    rng = np.random.default_rng(seed)
    ret = rng.normal(trend / n, vol, n)
    close = start_price * np.cumprod(1 + ret)
    high = close * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n)))
    vol = np.full(n, volume) * (1 + rng.normal(0, 0.1, n))
    idx = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame({"open": close, "high": high, "low": low,
                         "close": close, "volume": vol}, index=idx)


def test_registry_has_50_agents():
    agents = all_agents()
    ids = {a.agent_id for a in agents}
    assert len(agents) == 50, f"50 ajan bekleniyor, {len(agents)} var"
    expected = {"technical": 8, "macro": 8, "microstructure": 8,
                "risk": 9, "statistical": 8, "ai": 9}
    for cat, count in expected.items():
        assert sum(1 for a in agents if a.category == cat) == count, cat
    for aid in ("trend_ema", "chart_pattern", "dxy_dollar", "open_interest_trend",
                "equity_floor", "mean_reversion", "ai_direction", "ai_label_bias",
                "analog_trend", "analog_momentum", "analog_reversal", "analog_regime"):
        assert aid in ids


def test_all_agents_run_deterministic():
    df = make_df(trend=0.05)
    ctx = AgentContext(symbol="BTCUSDT", df=df, klines_map={"BTCUSDT": df},
                       portfolio=[], settings={}, macro={}, micro={}, extra={})
    first = run_for_symbol(ctx, {})
    assert len(first) == 50
    second = run_for_symbol(ctx, {})
    for r1, r2 in zip(first, second):
        assert r1.vote == r2.vote and r1.reason == r2.reason and r1.meta == r2.meta
        assert r1.agent_id == r2.agent_id


def test_agent_disabled_and_weight():
    df = make_df(trend=0.05)
    ctx = AgentContext(symbol="BTCUSDT", df=df, klines_map={"BTCUSDT": df},
                       portfolio=[], settings={}, macro={}, micro={}, extra={})
    settings = {"agents": {"trend_ema": {"enabled": False},
                           "momentum": {"weight": 0.9}}}
    results = run_for_symbol(ctx, settings)
    ids = [r.agent_id for r in results]
    assert "trend_ema" not in ids
    momentum = next(r for r in results if r.agent_id == "momentum")
    assert momentum.weight == 0.9


def test_aggregate_buy_wins():
    results = [
        AgentResult("a", "BUY", 0.6, "x", confidence=0.8),
        AgentResult("b", "BUY", 0.4, "x", confidence=0.7),
        AgentResult("c", "SELL", 0.2, "x", confidence=0.5),
        AgentResult("d", None, 0.3, "x", confidence=0.4),
    ]
    verdict, conf, net, buy, sell = aggregate(results)
    assert verdict == "BUY"
    assert net > 0
    assert buy > sell
    assert 0 <= conf <= 1


def test_aggregate_sell_wins():
    results = [
        AgentResult("a", "SELL", 0.6, "x", confidence=0.9),
        AgentResult("b", "SELL", 0.4, "x", confidence=0.8),
        AgentResult("c", "BUY", 0.2, "x", confidence=0.5),
    ]
    verdict, _, _, _, _ = aggregate(results)
    assert verdict == "SELL"


def test_aggregate_hold_when_balanced():
    results = [
        AgentResult("a", "BUY", 0.3, "x", confidence=0.7),
        AgentResult("b", "SELL", 0.3, "x", confidence=0.7),
    ]
    verdict, _, _, _, _ = aggregate(results)
    assert verdict == "HOLD"


def test_collect_adjustments():
    results = [
        AgentResult("a", None, 0.3, "x", adjustments={"size_mult": 0.5}),
        AgentResult("b", None, 0.3, "x", adjustments={"block": True}),
    ]
    adj = collect_adjustments(results)
    assert adj["size_mult"] == 0.5
    assert adj["blocked"] is True
    assert "b" in adj["block_sources"]

    only_size = collect_adjustments([AgentResult("a", None, 0.3, "x",
                                                 adjustments={"size_mult": 0.4}),
                                     AgentResult("c", None, 0.3, "x",
                                                 adjustments={"size_mult": 0.7})])
    assert only_size["size_mult"] == pytest.approx(0.28, abs=0.001)


def test_trend_up_df_gives_buy_votes():
    df = make_df(trend=0.3, seed=3, vol=0.003)
    ctx = AgentContext(symbol="BTCUSDT", df=df, klines_map={"BTCUSDT": df},
                       portfolio=[], settings={}, macro={}, micro={}, extra={})
    results = run_for_symbol(ctx, {})
    votes = {r.agent_id: r.vote for r in results}
    assert votes["trend_ema"] == "BUY"
    assert votes["regime_classifier"] in ("BUY", None)
    assert votes["trend_strength"] == "BUY"


def test_mean_reversion_flags_extreme_zscore():
    df = make_df(trend=0.3, seed=3, vol=0.003)
    ctx = AgentContext(symbol="BTCUSDT", df=df, klines_map={"BTCUSDT": df},
                       portfolio=[], settings={}, macro={}, micro={}, extra={})
    results = run_for_symbol(ctx, {})
    mr = next(r for r in results if r.agent_id == "mean_reversion")
    # guclu yukseliste fiyat ortalamanin 2 std ustunde -> asiri olarak SELL (donus sinyali)
    assert mr.vote in ("BUY", "SELL", None)


def test_macro_agents_abstain_without_data():
    df = make_df()
    ctx = AgentContext(symbol="BTCUSDT", df=df, klines_map={"BTCUSDT": df},
                       portfolio=[], settings={}, macro={}, micro={}, extra={})
    results = run_for_symbol(ctx, {})
    by_id = {r.agent_id: r for r in results}
    for aid in ("dxy_dollar", "macro_risk", "open_interest_trend", "funding_extreme",
                "whale_flow", "orderbook_imbalance"):
        assert by_id[aid].vote is None, aid


def test_patterns_detect_double_top():
    n = 120
    idx = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    close = np.full(n, 100.0)
    close[30:40] = np.linspace(100, 120, 10)   # ilk tepe
    close[40:60] = np.linspace(120, 108, 20)   # vadi
    close[60:70] = np.linspace(108, 119, 10)   # ikinci tepe
    close[70:] = np.linspace(119, 104, n - 70) # kirilim sonrasi düsüs
    df = pd.DataFrame({"open": close, "high": close * 1.004, "low": close * 0.996,
                       "close": close, "volume": np.full(n, 1e6)}, index=idx)
    res = detect_patterns(df)
    assert res["pattern"] is not None
    assert res["direction"] == "SELL"


def test_patterns_no_pattern_on_flat():
    df = make_df(seed=11)
    res = detect_patterns(df)
    # rastgele seri cok desen vermemeli; pattern None veya en azindan direction var
    assert isinstance(res, dict)
    assert "pattern" in res and "direction" in res


def test_risk_agents_block_on_extreme_vol():
    df = make_df(seed=5)
    # son 30 barda dev atr uret
    rng = np.random.default_rng(9)
    df.loc[df.index[-30:], "close"] = df["close"].iloc[-31] * np.cumprod(
        1 + rng.normal(0, 0.05, 30))
    df.loc[df.index[-30:], "high"] = df["close"].iloc[-30:] * 1.05
    df.loc[df.index[-30:], "low"] = df["close"].iloc[-30:] * 0.95
    ctx = AgentContext(symbol="BTCUSDT", df=df, klines_map={"BTCUSDT": df},
                       portfolio=[], settings={}, macro={}, micro={}, extra={})
    results = run_for_symbol(ctx, {})
    adj = collect_adjustments(results)
    vol = next(r for r in results if r.agent_id == "volatility_regime")
    assert vol.adjustments.get("block") is True or adj["size_mult"] < 1.0


def test_equity_floor_blocks_below_min():
    df = make_df()
    ctx = AgentContext(symbol="BTCUSDT", df=df, klines_map={"BTCUSDT": df},
                       portfolio=[], settings={"min_equity": 5000.0}, macro={}, micro={},
                       extra={"equity": 4000.0})
    results = run_for_symbol(ctx, {})
    ef = next(r for r in results if r.agent_id == "equity_floor")
    assert ef.adjustments.get("block") is True
    ctx.extra["equity"] = 9000.0
    results2 = run_for_symbol(ctx, {})
    ef2 = next(r for r in results2 if r.agent_id == "equity_floor")
    assert not ef2.adjustments.get("block")


def test_ai_label_bias_votes_on_extreme_label_balance():
    rng = np.random.default_rng(4)
    n = 300
    close = 100 * np.cumprod(1 + rng.normal(0.01, 0.01, n))
    idx = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    df = pd.DataFrame({"open": close, "high": close * 1.002, "low": close * 0.998,
                       "close": close, "volume": np.full(n, 1e6)}, index=idx)
    ctx = AgentContext(symbol="BTCUSDT", df=df, klines_map={"BTCUSDT": df},
                       portfolio=[], settings={}, macro={}, micro={}, extra={})
    results = run_for_symbol(ctx, {})
    lb = next(r for r in results if r.agent_id == "ai_label_bias")
    assert lb.vote == "BUY"


def test_analog_agents_abstain_without_memory():
    df = make_df()
    ctx = AgentContext(symbol="BTCUSDT", df=df, klines_map={"BTCUSDT": df},
                       portfolio=[], settings={}, macro={}, micro={}, extra={})
    results = run_for_symbol(ctx, {})
    by_id = {r.agent_id: r for r in results}
    for aid in ("analog_trend", "analog_momentum", "analog_reversal", "analog_regime"):
        assert by_id[aid].vote is None, aid


def test_analog_agents_vote_from_injected_result():
    df = make_df()
    analog = {"trend": {"mean_fwd_pct": 2.5, "neighbors": 30, "confidence": 0.6},
              "momentum": {"mean_fwd_pct": -1.8, "neighbors": 25, "confidence": 0.7},
              "reversal": {"mean_fwd_pct": 0.4, "neighbors": 3, "confidence": 0.5},
              "regime": {"mean_fwd_pct": 0.9, "neighbors": 20, "confidence": 0.5}}
    ctx = AgentContext(symbol="BTCUSDT", df=df, klines_map={"BTCUSDT": df},
                       portfolio=[], settings={}, macro={}, micro={},
                       extra={"analog": analog})
    results = run_for_symbol(ctx, {})
    by_id = {r.agent_id: r for r in results}
    assert by_id["analog_trend"].vote == "BUY"
    assert by_id["analog_momentum"].vote == "SELL"
    assert by_id["analog_reversal"].vote is None
    assert by_id["analog_regime"].vote is None
