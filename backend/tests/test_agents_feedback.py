"""Ajan geri bildirim dongusu (feedback) testleri."""
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


def _vote(aid, category, vote, price, confidence=0.6):
    return AgentResult(aid, vote, 0.3, "test", confidence=confidence,
                       category=category)


def _record(db, rows):
    recs = [_vote(a, c, v, p) for a, c, v, p in rows]
    return record_votes(db, "BTCUSDT", "2025-01-01T00:00:00", recs,
                        price=100.0)


def test_record_and_resolve_hit_miss(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    _record(db, [("a1", "technical", "BUY", 100.0),
                 ("a2", "statistical", "SELL", 100.0)])
    counts = resolve_symbol(db, "BTCUSDT", 105.0)
    assert counts == {"hit": 1, "miss": 1, "na": 0}
    history = vote_history(db, "a1")
    assert history[0]["outcome"] == "hit"
    assert resolve_symbol(db, "BTCUSDT", 105.0) == {"hit": 0, "miss": 0, "na": 0}


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
    resolve_symbol(db, "BTCUSDT", 1000.0)
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
    resolve_symbol(db, "BTCUSDT", 1.0)
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
    resolve_symbol(db, "BTCUSDT", 1.0)
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
