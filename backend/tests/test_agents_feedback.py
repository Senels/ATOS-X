"""Ajan geri bildirim dongusu (feedback) testleri."""
import numpy as np
import pandas as pd
import pytest

from app.agents.base import AgentResult
from app.agents.feedback import (
    record_votes,
    resolve_stale,
    resolve_symbol,
    update_weights,
    vote_history,
)
from app.agents.registry import all_agents
from app.core.database import Database


def _df(n=120, seed=1):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0.001, 0.01, n))
    idx = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame({"open": close, "high": close * 1.002,
                         "low": close * 0.998, "close": close,
                         "volume": np.full(n, 1e6)}, index=idx)


def _vote(aid, category, vote, price, confidence=0.6):
    return AgentResult(aid, vote, 0.3, "test", confidence=confidence,
                       category=category)


def _record(db, rows, bar_ts="2025-01-02 00:00:00+00:00"):
    recs = [_vote(a, c, v, p) for a, c, v, p in rows]
    return record_votes(db, "BTCUSDT", bar_ts, recs, price=100.0)


def test_record_and_resolve_hit_miss(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    _record(db, [("a1", "technical", "BUY", 100.0),
                 ("a2", "statistical", "SELL", 100.0)])
    df = _df()
    df["close"] = np.linspace(100, 200, len(df))  # yukselen kapanislar
    counts = resolve_symbol(db, df, "BTCUSDT", resolution_bars=5)
    assert counts == {"hit": 1, "miss": 1, "na": 0}
    history = vote_history(db, "a1")
    assert history[0]["outcome"] == "hit"
    assert resolve_symbol(db, df, "BTCUSDT", resolution_bars=5) == \
        {"hit": 0, "miss": 0, "na": 0}


def test_resolve_waits_until_horizon(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    _record(db, [("a1", "technical", "BUY", 100.0)])
    df = _df(n=60)
    early = resolve_symbol(db, df.iloc[:20], "BTCUSDT", resolution_bars=24)
    assert early == {"hit": 0, "miss": 0, "na": 0}
    late = resolve_symbol(db, df, "BTCUSDT", resolution_bars=24)
    assert late["hit"] + late["miss"] == 1


def test_resolve_na_when_bar_gone(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    _record(db, [("a1", "technical", "BUY", 100.0)])
    df = _df()
    df.index = df.index + pd.Timedelta(days=400)
    counts = resolve_symbol(db, df, "BTCUSDT", resolution_bars=5)
    assert counts == {"hit": 0, "miss": 0, "na": 1}


def test_record_skips_abstainers(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    n = record_votes(db, "BTCUSDT", "b1", [AgentResult("x1", None, 0.3, "t",
                                                       category="macro")])
    assert n == 0


def test_update_weights_ewma(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    agent = next(a for a in all_agents() if a.category != "risk")
    _record(db, [(agent.agent_id, agent.category, "BUY", 100.0 + i)
                 for i in range(30)])
    df = _df()
    df["close"] = df["close"] * 1.05  # tum ileri kapanislar yuksek -> hit
    df["open"] = df["open"] * 1.05
    df["high"] = df["high"] * 1.05
    df["low"] = df["low"] * 1.05
    resolve_symbol(db, df, "BTCUSDT", resolution_bars=5)
    summary = update_weights(db, {}, apply=False)
    entry = summary["updated"][agent.agent_id]
    assert entry["samples"] == 30
    assert entry["enabled"] is True
    assert entry["weight"] == pytest.approx(agent.default_weight * 1.5, rel=0.01)


def test_update_weights_disables_weak_agent(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    agent = next(a for a in all_agents() if a.category != "risk")
    _record(db, [(agent.agent_id, agent.category, "BUY", 100.0 - i)
                 for i in range(30)])
    df = _df()
    df["close"] = df["close"] * 0.95
    df["open"] = df["open"] * 0.95
    df["high"] = df["high"] * 0.95
    df["low"] = df["low"] * 0.95
    resolve_symbol(db, df, "BTCUSDT", resolution_bars=5)
    summary = update_weights(db, {}, apply=False)
    disabled = [d for d in summary["disabled"] if d["agent_id"] == agent.agent_id]
    assert disabled, "zayif ajan devre disi kalmali"
    assert disabled[0]["enabled"] is False
    assert disabled[0]["reason"] == "dusuk isabet"
    assert disabled[0]["weight"] == pytest.approx(agent.default_weight * 0.25,
                                                  rel=0.01)


def test_update_weights_skips_risk(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    risk = next(a for a in all_agents() if a.category == "risk")
    _record(db, [(risk.agent_id, "risk", "BUY", 100.0 - i) for i in range(30)])
    df = _df()
    df["close"] = df["close"] * 0.95
    resolve_symbol(db, df, "BTCUSDT", resolution_bars=5)
    summary = update_weights(db, {}, apply=False)
    assert risk.agent_id not in summary["updated"]
    assert not any(d["agent_id"] == risk.agent_id for d in summary["disabled"])


def test_resolve_stale(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    _record(db, [("a1", "technical", "BUY", 100.0)])
    import sqlite3
    with sqlite3.connect(str(tmp_path / "t.db")) as conn:
        conn.execute("UPDATE agent_votes SET created_at = '2020-01-01'")
    assert resolve_stale(db, days=1) == 1
    assert vote_history(db, "a1")[0]["outcome"] == "na"
