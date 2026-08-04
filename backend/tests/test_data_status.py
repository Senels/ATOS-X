from datetime import datetime

from fastapi.testclient import TestClient

import pandas as pd

from app import main as main_mod
from app.main import app


class _FakeTrader:
    def __init__(self):
        self.priority = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        self.trading_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def _df(hours_ago):
    idx = pd.DatetimeIndex([datetime.utcnow() - pd.Timedelta(hours=hours_ago)]).tz_localize("UTC")
    return pd.DataFrame({"open": [100.0], "high": [101.0], "low": [99.0],
                         "close": [100.0], "volume": [1.0]}, index=idx)


def _fresh_df():
    return _df(1)


def _stale_df():
    return _df(72)


def _client():
    return TestClient(app)


def test_data_status_ok():
    ft = _FakeTrader()
    main_mod.auto_trader = ft

    def fake_load(symbol, interval="4h", data_dir=None, limit=None):
        if symbol == "BTCUSDT":
            return _fresh_df()
        if symbol == "ETHUSDT":
            return _stale_df()
        raise FileNotFoundError("missing")

    main_mod.loader.load_csv = fake_load
    try:
        c = _client()
        resp = c.get("/api/v1/data/status")
        c.close()
    finally:
        main_mod.auto_trader = None
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["count"] == 3
    assert body["fresh"] == 1
    assert body["stale"] == 1
    assert body["missing"] == 1
    by_sym = {r["symbol"]: r for r in body["rows"]}
    assert by_sym["BTCUSDT"]["state"] == "ok"
    assert by_sym["ETHUSDT"]["state"] == "stale"
    assert by_sym["SOLUSDT"]["state"] == "missing"
    assert by_sym["ETHUSDT"]["age_hours"] > 12.0


def test_data_status_not_running():
    main_mod.auto_trader = None
    c = _client()
    resp = c.get("/api/v1/data/status")
    c.close()
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"] == "not_running"
    assert body["count"] == 0
