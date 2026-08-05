import asyncio

import pytest

from app.api import backtest as bt
from app.core.database import Database
from app.data import loader


@pytest.fixture(scope="module", autouse=True)
def skip_without_data():
    data_dir = loader.DEFAULT_DATA_DIR / "futures_4h_data"
    if not (data_dir / "BTCUSDT_4h.csv").exists():
        pytest.skip("BTCUSDT_4h.csv yok; backtest API testleri atlandi")
    yield


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


def test_backtest_risk_params_in_metrics(api_db):
    res = asyncio.run(bt.run_backtest(
        symbol="BTCUSDT", interval="4h", limit=100, source="csv",
    ))
    params = res["params"]
    assert "max_drawdown_pct" in params
    assert "max_consecutive_losses" in params
    assert "trailing_activate_pct" in params
    assert "breakeven_activate_pct" in params
    assert "max_position_age_hours" in params


def test_backtest_risk_override_params(api_db):
    res = asyncio.run(bt.run_backtest(
        symbol="BTCUSDT", interval="4h", limit=100, source="csv",
        max_drawdown_pct=5.0, max_consecutive_losses=3,
        trailing_activate_pct=3.0, trailing_sl_pct=1.5,
        breakeven_activate_pct=2.0, max_position_age_hours=6,
        min_equity=9000.0,
    ))
    params = res["params"]
    assert params["max_drawdown_pct"] == 5.0
    assert params["max_consecutive_losses"] == 3
    assert params["trailing_activate_pct"] == 3.0
    assert params["trailing_sl_pct"] == 1.5
    assert params["breakeven_activate_pct"] == 2.0
    assert params["max_position_age_hours"] == 6
    assert params["min_equity"] == 9000.0


def test_history(api_db):
    asyncio.run(bt.run_backtest(symbol="BTCUSDT", interval="4h", limit=100, source="csv"))
    hist = asyncio.run(bt.backtest_history(limit=10))
    assert len(hist["runs"]) == 1

    hist_eth = asyncio.run(bt.backtest_history(symbol="ETHUSDT", limit=10))
    assert len(hist_eth["runs"]) == 0


def test_backtest_ttp_managed_path(api_db, monkeypatch):
    """active_strategy=ttp iken API analyze_full/managed yolu kullanmali (time_stop yok)."""
    import copy

    from app.strategy import settings as ss

    st = copy.deepcopy(ss._state)
    st["active_strategy"] = "ttp"
    monkeypatch.setattr(ss, "_state", st)

    res = asyncio.run(bt.run_backtest(
        symbol="BTCUSDT", interval="4h", limit=200, source="csv",
    ))
    reasons = {t["reason"] for t in res["trades"]}
    assert reasons <= {"stop_loss", "take_profit", "tp_partial", "trail_tp", "reversal"}
    assert "time_stop" not in reasons


def test_compare(api_db):
    asyncio.run(bt.run_backtest(symbol="BTCUSDT", interval="4h", limit=100, source="csv"))
    runs = api_db.get_backtest_runs(limit=10)
    rid = runs[0]["id"]

    cmp = asyncio.run(bt.backtest_compare(a=rid, b=rid))
    assert str(rid) in cmp["runs"]
    assert cmp["runs"][str(rid)]["metrics"]["total_trades"] > 0

    with pytest.raises(Exception):
        asyncio.run(bt.backtest_compare(a=999999, b=rid))
