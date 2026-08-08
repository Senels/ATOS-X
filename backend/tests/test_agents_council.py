"""Analog bellek, danisma (deliberation) ve konsensus karari testleri."""
import numpy as np
import pandas as pd

from app.agents.analog import AnalogMemory
from app.agents.base import AgentResult
from app.agents.context import AgentContext
from app.agents.deliberation import deliberate
from app.agents.orchestrator import consensus_verdict, run_council


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


def _write_csv(path, df, seed=0):
    ts = (df.index.astype("int64") // 10 ** 6).to_numpy()
    out = pd.DataFrame({
        "timestamp": ts,
        "open": df["open"].to_numpy(),
        "high": df["high"].to_numpy(),
        "low": df["low"].to_numpy(),
        "close": df["close"].to_numpy(),
        "volume": df["volume"].to_numpy(),
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)
    return path


def _agent(aid, vote, category, weight=0.3, confidence=0.5, **kw):
    return AgentResult(aid, vote, weight, "test", confidence=confidence, category=category, **kw)


# ----------------------------------------------------------------------
# AnalogMemory
# ----------------------------------------------------------------------

def test_analog_build_from_archive_and_query(tmp_path):
    data_dir = tmp_path / "data"
    for i, trend in enumerate((0.05, -0.05, 0.1)):
        _write_csv(data_dir / f"SYM{i}USDT_4h.csv", make_df(n=400, trend=trend, seed=i, vol=0.01), seed=i)
    mem = AnalogMemory(horizon=24, min_rows=60, k=10,
                       path=tmp_path / "mem.npz", meta_path=tmp_path / "meta.json")
    info = mem.build(["SYM0USDT", "SYM1USDT", "SYM2USDT"], data_dir=str(data_dir))
    assert info["rows"] > 100
    assert info["symbols"] == 3
    res = mem.query(make_df(n=300, trend=0.08, seed=3, vol=0.01), key="trend")
    assert res["neighbors"] == 10
    assert abs(res["mean_fwd_pct"]) > 0
    assert 0 <= res["confidence"] <= 1


def test_analog_roundtrip(tmp_path):
    mem = AnalogMemory(horizon=24, min_rows=60, k=5,
                       path=tmp_path / "mem.npz", meta_path=tmp_path / "meta.json")
    mem.vectors = np.zeros((10, 23), dtype=np.float32)
    mem.fwd = np.ones(10, dtype=np.float32)
    mem.codes = np.zeros(10, dtype=np.int32)
    mem.ts = np.arange(10)
    mem.symbols = ["AUSDT"]
    mem.save()
    mem2 = AnalogMemory(horizon=24, k=5,
                        path=tmp_path / "mem.npz", meta_path=tmp_path / "meta.json")
    assert mem2.load()
    assert mem2.vectors.shape == (10, 23)
    assert mem2.symbols == ["AUSDT"]


def test_analog_query_without_memory(tmp_path):
    mem = AnalogMemory(horizon=24, k=5,
                       path=tmp_path / "nope.npz", meta_path=tmp_path / "nope.json")
    assert mem.query(make_df(n=300)) == {}
    assert mem.load() is False


def test_analog_query_below_min_k_degrades(tmp_path):
    mem = AnalogMemory(horizon=24, k=5)
    mem.vectors = np.zeros((3, 23), dtype=np.float32)
    mem.fwd = np.array([1.0, -1.0, 2.0], dtype=np.float32)
    res = mem.query(make_df(n=300), key="regime")
    assert res["neighbors"] == 3


# ----------------------------------------------------------------------
# Danisma (deliberation)
# ----------------------------------------------------------------------

def test_deliberate_weak_agent_follows_strong_majority():
    results = [
        _agent("strong_a", "BUY", "technical", weight=0.8, confidence=0.7),
        _agent("strong_b", "BUY", "technical", weight=0.8, confidence=0.7),
        _agent("weak", "SELL", "technical", weight=0.2, confidence=0.3),
        _agent("risk_x", None, "risk", confidence=0.2),
    ]
    out = deliberate(results, {})
    by_id = {r.agent_id: r for r in out}
    assert by_id["weak"].vote == "BUY"
    assert by_id["weak"].meta.get("consulted") is True
    assert by_id["weak"].confidence <= 0.5
    assert by_id["risk_x"].confidence == 0.2


def test_deliberate_weak_agent_abstains_on_split():
    results = [
        _agent("a", "BUY", "technical", weight=0.4, confidence=0.6),
        _agent("b", "SELL", "technical", weight=0.4, confidence=0.6),
        _agent("weak", "BUY", "technical", weight=0.2, confidence=0.3),
    ]
    out = deliberate(results, {})
    weak = next(r for r in out if r.agent_id == "weak")
    assert weak.vote is None
    assert "cekims" in weak.reason


def test_deliberate_high_confidence_untouched():
    results = [
        _agent("a", "BUY", "technical", weight=0.8, confidence=0.7),
        _agent("b", "BUY", "technical", weight=0.8, confidence=0.7),
        _agent("conf", "SELL", "technical", weight=0.3, confidence=0.6),
    ]
    out = deliberate(results, {})
    conf = next(r for r in out if r.agent_id == "conf")
    assert conf.vote == "SELL"
    assert "consulted" not in conf.meta


def test_deliberate_cross_bonus_agreement():
    results = [
        _agent("m1", "BUY", "macro", weight=0.5, confidence=0.6),
        _agent("m2", "BUY", "macro", weight=0.5, confidence=0.6),
        _agent("m3", "BUY", "macro", weight=0.5, confidence=0.6),
        _agent("a1", "BUY", "ai", weight=0.5, confidence=0.6),
        _agent("a2", "BUY", "ai", weight=0.5, confidence=0.6),
        _agent("a3", "BUY", "ai", weight=0.5, confidence=0.6),
    ]
    out = deliberate(results, {})
    for r in out:
        assert r.confidence >= 0.55
        assert r.meta.get("cross_bonus") is True


# ----------------------------------------------------------------------
# Konsensus karari
# ----------------------------------------------------------------------

def _full_buy_council():
    return [
        _agent(f"t{i}", "BUY", "technical", weight=0.4, confidence=0.6) for i in range(4)
    ] + [
        _agent(f"s{i}", "BUY", "statistical", weight=0.4, confidence=0.6) for i in range(4)
    ] + [
        _agent(f"m{i}", "BUY", "macro", weight=0.4, confidence=0.6) for i in range(4)
    ] + [
        _agent(f"x{i}", "BUY", "microstructure", weight=0.4, confidence=0.6) for i in range(4)
    ] + [
        _agent(f"a{i}", "BUY", "ai", weight=0.4, confidence=0.6) for i in range(4)
    ] + [
        _agent(f"r{i}", None, "risk", weight=0.3, confidence=0.5) for i in range(4)
    ]


def test_consensus_verdict_buy():
    verdict = consensus_verdict(_full_buy_council(), {})
    assert verdict["verdict"] == "BUY"
    assert verdict["votes"] == 20
    assert verdict["agree_categories"] == 5
    assert verdict["quorum_ok"] is True
    assert verdict["confidence"] >= 0.25
    assert verdict["hold_reason"] is None


def test_consensus_verdict_low_quorum_holds():
    verdict = consensus_verdict([_agent("t1", "BUY", "technical", weight=0.9, confidence=0.9),
                                 _agent("t2", "BUY", "technical", weight=0.9, confidence=0.9)], {})
    assert verdict["verdict"] == "HOLD"
    assert verdict["hold_reason"] == "yetersiz quorum"


def test_consensus_verdict_risk_veto_wins():
    results = _full_buy_council() + [
        _agent("blocker", None, "risk", weight=1.0, confidence=0.9,
               adjustments={"block": True}),
    ]
    verdict = consensus_verdict(results, {})
    assert verdict["verdict"] == "HOLD"
    assert verdict["hold_reason"] == "risk vetosu"
    assert verdict["blocked"] is True


def test_consensus_verdict_weak_consensus_holds():
    results = _full_buy_council() + [
        _agent(f"sell{i}", "SELL", "statistical", weight=1.0, confidence=0.9) for i in range(4)
    ]
    verdict = consensus_verdict(results, {})
    assert verdict["verdict"] == "HOLD"
    assert verdict["hold_reason"] == "zayif konsensus"


def test_consensus_verdict_few_categories_hold():
    results = [_agent(f"t{i}", "BUY", "technical", weight=0.6, confidence=0.8) for i in range(15)]
    verdict = consensus_verdict(results, {})
    assert verdict["verdict"] == "HOLD"
    assert verdict["hold_reason"] == "yetersiz kategori"


def test_run_council_pipeline():
    df = make_df(trend=0.3, seed=3, vol=0.003)
    ctx = AgentContext(
        symbol="BTCUSDT", df=df, klines_map={"BTCUSDT": df},
        portfolio=[], settings={}, macro={}, micro={},
        extra={"analog": {"trend": {"mean_fwd_pct": 2.0, "neighbors": 20, "confidence": 0.6}}})
    results, verdict = run_council(ctx, {})
    assert len(results) == 50
    assert isinstance(verdict["verdict"], str)
    assert verdict["adjustments"]["size_mult"] > 0
