"""Agent konseyi egitim yoneticisi (retrain) testleri."""
import numpy as np
import pandas as pd

from app.agents.analog import MEMORY_PATH
from app.agents.base import AgentResult
from app.agents.feedback import record_votes, resolve_symbol
from app.agents.retrain import (
    accuracy_trigger,
    agent_accuracy,
    agent_retrain_due,
    last_trained_at,
    memory_summary,
)
from app.core.database import Database


def test_last_trained_at_returns_float_or_none():
    ts = last_trained_at()
    if MEMORY_PATH.exists():
        assert isinstance(ts, float) and ts > 0
    else:
        assert ts is None


def test_agent_retrain_due():
    now = 1_000_000.0
    assert agent_retrain_due(None, now, 24) is True
    assert agent_retrain_due(now - 25 * 3600, now, 24) is True
    assert agent_retrain_due(now - 60, now, 24) is False


def test_accuracy_trigger():
    now = 1_000_000.0
    assert accuracy_trigger(None, 0.40, None, now) is False
    assert accuracy_trigger(0.55, 0.40, None, now) is False
    assert accuracy_trigger(0.30, 0.40, now - 100, now) is False
    assert accuracy_trigger(0.30, 0.40, now - 7 * 3600, now) is True


def test_agent_accuracy_from_votes(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    assert agent_accuracy(db) is None
    df = pd.DataFrame({"open": np.linspace(100, 200, 40),
                       "high": np.linspace(100, 202, 40),
                       "low": np.linspace(100, 198, 40),
                       "close": np.linspace(100, 200, 40),
                       "volume": np.full(40, 1e6)},
                      index=pd.date_range("2025-01-01", periods=40,
                                          freq="4h", tz="UTC"))
    recs = [AgentResult(f"a{i}", "BUY", 0.3, "t", category="technical")
            for i in range(4)]
    record_votes(db, "BTCUSDT", str(df.index[0]), recs, price=100.0)
    resolve_symbol(db, df, "BTCUSDT", resolution_bars=5)
    acc = agent_accuracy(db, window=100)
    assert acc == 1.0


def test_memory_summary_shape():
    summary = memory_summary()
    assert isinstance(summary, dict)
    assert "loaded" in summary
