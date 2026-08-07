import asyncio

import numpy as np
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


def test_backtest_ai_filter_no_predictor(api_db, monkeypatch):
    monkeypatch.setattr(bt, "_get_predictor", lambda: None)
    res = asyncio.run(bt.run_backtest(
        symbol="BTCUSDT", interval="4h", limit=100, source="csv",
        ai_filter=True, ai_threshold=0.55,
    ))
    assert res["ai_filter"] is True
    assert res["ai_applied"] is False
    assert res["total_trades"] > 0


def test_backtest_ab_mode_no_predictor(api_db, monkeypatch):
    monkeypatch.setattr(bt, "_get_predictor", lambda: None)
    res = asyncio.run(bt.run_backtest(
        symbol="BTCUSDT", interval="4h", limit=100, source="csv",
        ab_mode=True,
    ))
    assert res["ai_filter"] is True
    assert res["ai_applied"] is False
    assert res["ab"] is None
    assert res["total_trades"] > 0


def test_backtest_scan_multi(api_db):
    res = asyncio.run(bt.run_backtest_scan(
        symbols="BTCUSDT,ETHUSDT", interval="4h", limit=100, source="csv",
    ))
    assert len(res["results"]) == 2
    assert res["summary"]["symbols"] == 2
    assert res["results"][0]["signals"] >= 0


def test_backtest_scan_ab(api_db, monkeypatch):
    monkeypatch.setattr(bt, "_get_predictor", lambda: None)
    res = asyncio.run(bt.run_backtest_scan(
        symbols="BTCUSDT", interval="4h", limit=100, source="csv",
        ab_mode=True,
    ))
    assert res["ab_mode"] is True
    assert res["ai_applied"] is False
    row = res["results"][0]
    assert "base_net" in row and "ai_net" in row
    assert row["base_net"] == row["net"]


def test_backtest_scan_ai_filter(api_db, monkeypatch):
    monkeypatch.setattr(bt, "_get_predictor", lambda: None)
    res = asyncio.run(bt.run_backtest_scan(
        symbols="BTCUSDT", interval="4h", limit=100, source="csv",
        ai_filter=True,
    ))
    assert res["ai_filter"] is True
    row = res["results"][0]
    assert "base_net" in row and "net" in row


def test_backtest_scan_ai_filter_with_mask(api_db, monkeypatch):
    calls = {"n": 0}

    def fake_predictor():
        return object()

    def fake_mask(predictor, df, sig, threshold):
        calls["n"] += 1
        m = np.zeros(len(df), dtype=bool)
        m[np.asarray(sig) != 0] = True
        return m

    monkeypatch.setattr(bt, "_get_predictor", fake_predictor)
    monkeypatch.setattr(bt, "_ai_mask", fake_mask)
    res = asyncio.run(bt.run_backtest_scan(
        symbols="BTCUSDT", interval="4h", limit=100, source="csv",
        ai_filter=True,
    ))
    assert calls["n"] == 1
    row = res["results"][0]
    assert row["blocked"] > 0
    assert row["signal_stats"]["blocked"] > 0
    assert res["summary"]["blocked"] > 0


def test_backtest_ttp_json_override(api_db):
    res = asyncio.run(bt.run_backtest(
        symbol="BTCUSDT", interval="4h", limit=100, source="csv",
        ttp='{"sl_long_atr_mul": 2.5, "tp_long_rr": 2.0}',
    ))
    assert res["settings"]["ttp"]["sl_long_atr_mul"] == 2.5
    assert res["settings"]["ttp"]["tp_long_rr"] == 2.0
    assert res["settings"]["ttp"]["sl_trail_mode"] == "TP"


def test_backtest_sl_timeframe_override(api_db):
    res = asyncio.run(bt.run_backtest(
        symbol="BTCUSDT", interval="4h", limit=100, source="csv",
        sl_timeframe="2h",
    ))
    assert res["settings"]["sl_timeframe"] == "2h"


def test_binance_cached_roundtrip(tmp_path, monkeypatch):
    """Onbellek yolu: ilk cagri indirir, ikinci cagri diskten okur."""
    import pandas as pd

    calls = {"n": 0}

    async def fake_fetch(symbol, interval, target):
        calls["n"] += 1
        idx = pd.date_range("2025-01-01", periods=100, freq="4h", tz="UTC")
        df = pd.DataFrame(
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0},
            index=idx,
        )
        return df

    monkeypatch.setattr(bt, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(bt, "_fetch_binance_history", fake_fetch)

    first = asyncio.run(bt._load_binance_cached("TESTUSDT", "4h", 80))
    second = asyncio.run(bt._load_binance_cached("TESTUSDT", "4h", 80))
    assert len(first) == 80
    assert len(second) == 80
    assert calls["n"] == 1
    assert (tmp_path / "4h" / "TESTUSDT.csv").exists()


def test_binance_history_multi_chunk(monkeypatch):
    """1 yil 4h (2190 bar) gibi buyuk istekler parcali cekimle tamamlanir."""
    import pandas as pd

    async def fake_get_klines(symbol, interval, limit, start_time=None):
        n = limit
        if start_time is None:
            start = pd.Timestamp("2025-01-01", tz="UTC")
        else:
            start = pd.Timestamp(start_time, unit="ms", tz="UTC")
        idx = pd.date_range(start, periods=n, freq="4h", tz="UTC")
        return pd.DataFrame(
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0},
            index=idx,
        )

    class FakeClient:
        async def connect(self):
            return True

        async def get_klines(self, symbol, interval, limit, start_time=None):
            return await fake_get_klines(symbol, interval, limit, start_time)

    monkeypatch.setattr(
        "app.exchange.binance_client.BinanceClient", lambda: FakeClient())
    df = asyncio.run(bt._fetch_binance_history("BTCUSDT", "4h", 2190))
    assert len(df) == 2190
    assert df.index.is_unique
    expected_first = pd.Timestamp("2025-01-01", tz="UTC") - pd.Timedelta(hours=690 * 4)
    assert abs((df.index[0] - expected_first).total_seconds()) < 300


def test_binance_cached_refetch_when_short(tmp_path, monkeypatch):
    """Onbellek dosyasi istenen bardan az veri iceriyorsa yeniden indirilir."""
    import pandas as pd

    calls = {"n": 0}

    async def fake_fetch(symbol, interval, target):
        calls["n"] += 1
        n = 100 if calls["n"] == 1 else 250
        idx = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
        return pd.DataFrame(
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0},
            index=idx,
        )

    monkeypatch.setattr(bt, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(bt, "_fetch_binance_history", fake_fetch)

    first = asyncio.run(bt._load_binance_cached("TESTUSDT", "4h", 200))
    second = asyncio.run(bt._load_binance_cached("TESTUSDT", "4h", 200))
    assert len(first) == 100
    assert len(second) == 200
    assert calls["n"] == 2


def test_binance_cached_refetch_when_stale(tmp_path, monkeypatch):
    """Onbellek dosyasi bayatsa yeniden indirilir."""
    import time

    import pandas as pd

    async def fake_fetch(symbol, interval, target):
        idx = pd.date_range("2025-01-01", periods=50, freq="4h", tz="UTC")
        return pd.DataFrame(
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0},
            index=idx,
        )

    monkeypatch.setattr(bt, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(bt, "_fetch_binance_history", fake_fetch)
    monkeypatch.setattr(bt, "_CACHE_MAX_AGE_SEC", 3600)

    asyncio.run(bt._load_binance_cached("TESTUSDT", "4h", 50))
    path = tmp_path / "4h" / "TESTUSDT.csv"
    old = time.time() - 7200
    import os
    os.utime(path, (old, old))

    asyncio.run(bt._load_binance_cached("TESTUSDT", "4h", 50))
    mtime = os.path.getmtime(path)
    assert mtime > old


def test_market_symbols(monkeypatch):
    async def fake_load_all(self):
        return ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    monkeypatch.setattr("app.exchange.binance_client.BinanceClient.load_all_symbols", fake_load_all)
    res = asyncio.run(bt.market_symbols())
    assert res["count"] == 3
    assert "BTCUSDT" in res["symbols"]


def test_scan_start_market_job(monkeypatch, api_db):
    """symbols=market job akisi: baslat -> calis -> done -> sonuc doner."""
    async def fake_load_all(self):
        return ["BTCUSDT", "ETHUSDT"]

    async def fake_load_data(symbol, interval, limit, source):
        return loader.load_csv(symbol, interval, limit=limit)

    monkeypatch.setattr("app.exchange.binance_client.BinanceClient.load_all_symbols", fake_load_all)
    monkeypatch.setattr(bt, "_load_data", fake_load_data)

    async def scenario():
        started = await bt.scan_start(
            symbols="market", interval="4h", limit=100, source="csv",
        )
        for _ in range(200):
            st = await bt.scan_status(started["job_id"])
            if st["status"] in ("done", "failed"):
                return st
            await asyncio.sleep(0.05)
        return await bt.scan_status(started["job_id"])

    st = asyncio.run(scenario())
    assert st["status"] == "done"
    assert st["result"]["summary"]["symbols"] == 2

    with pytest.raises(Exception):
        asyncio.run(bt.scan_status("yok-boyle-bir-job"))


def test_scan_respects_banned_symbols(api_db, monkeypatch):
    monkeypatch.setattr(bt, "_banned_symbols", lambda: {"ETHUSDT", "DOGEUSDT"})
    res = asyncio.run(bt.run_backtest_scan(
        symbols="BTCUSDT,ETHUSDT,DOGEUSDT", interval="4h", limit=100, source="csv",
    ))
    assert [r["symbol"] for r in res["results"]] == ["BTCUSDT"]
    assert res["summary"]["symbols"] == 1


def test_scan_start_market_respects_banned(monkeypatch, api_db):
    async def fake_load_all(self):
        return ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    async def fake_load_data(symbol, interval, limit, source):
        return loader.load_csv(symbol, interval, limit=limit)

    monkeypatch.setattr("app.exchange.binance_client.BinanceClient.load_all_symbols", fake_load_all)
    monkeypatch.setattr(bt, "_load_data", fake_load_data)
    monkeypatch.setattr(bt, "_banned_symbols", lambda: {"SOLUSDT"})

    async def scenario():
        started = await bt.scan_start(
            symbols="market", interval="4h", limit=100, source="csv",
        )
        assert started["total"] == 2
        for _ in range(200):
            st = await bt.scan_status(started["job_id"])
            if st["status"] in ("done", "failed"):
                return st
            await asyncio.sleep(0.05)
        return await bt.scan_status(started["job_id"])

    st = asyncio.run(scenario())
    assert st["status"] == "done"
    assert st["result"]["summary"]["symbols"] == 2
