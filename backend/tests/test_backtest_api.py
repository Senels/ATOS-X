import asyncio

import pytest

from app.api import backtest as bt
from app.core.database import Database


@pytest.fixture
def api_db(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "bt_api.db"))
    monkeypatch.setattr(bt, "_db", db)
    return db


def test_run_backtest_saves_run(api_db):
    res = asyncio.run(bt.run_backtest(
        symbol="BTCUSDT", interval="4h", limit=100, source="csv",
    ))
    assert res["total_trades"] > 0
    assert res["net_profit"] is not None
    assert "equity_curve" in res

    runs = api_db.get_backtest_runs(limit=10)
    assert len(runs) == 1
    assert runs[0]["metrics"]["total_trades"] == res["total_trades"]
    assert "equity_curve" not in runs[0]["metrics"]


def test_engine_params_fall_back_to_settings(api_db):
    res = asyncio.run(bt.run_backtest(
        symbol="BTCUSDT", interval="4h", limit=100, source="csv",
        risk_per_trade=0.05, fee_rate=0.001,
    ))
    assert res["params"]["risk_per_trade"] == 0.05
    assert res["params"]["fee_rate"] == 0.001


def test_history(api_db):
    asyncio.run(bt.run_backtest(symbol="BTCUSDT", interval="4h", limit=100, source="csv"))
    hist = asyncio.run(bt.backtest_history(limit=10))
    assert len(hist["runs"]) == 1

    hist_eth = asyncio.run(bt.backtest_history(symbol="ETHUSDT", limit=10))
    assert len(hist_eth["runs"]) == 0


def test_compare(api_db):
    asyncio.run(bt.run_backtest(symbol="BTCUSDT", interval="4h", limit=100, source="csv"))
    runs = api_db.get_backtest_runs(limit=10)
    rid = runs[0]["id"]

    cmp = asyncio.run(bt.backtest_compare(a=rid, b=rid))
    assert str(rid) in cmp["runs"]
    assert cmp["runs"][str(rid)]["metrics"]["total_trades"] > 0

    with pytest.raises(Exception):
        asyncio.run(bt.backtest_compare(a=999999, b=rid))
