from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
from starlette.testclient import TestClient

from app import main as main_mod
from app.main import app


class _FakeTrader:
    def __init__(self):
        self.priority = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        self.trading_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def _df(hours_ago):
    idx = pd.DatetimeIndex(
        [datetime.now(timezone.utc).replace(tzinfo=None) - pd.Timedelta(hours=hours_ago)]
    ).tz_localize("UTC")
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

    original_load_csv = main_mod.loader.load_csv
    main_mod.loader.load_csv = fake_load
    try:
        c = _client()
        resp = c.get("/api/v1/data/status")
        c.close()
    finally:
        main_mod.auto_trader = None
        main_mod.loader.load_csv = original_load_csv
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


def test_dashboard_has_data_freshness_card():
    c = _client()
    resp = c.get("/dashboard/html")
    c.close()
    assert resp.status_code == 200
    assert "Veri Tazeliği" in resp.text
    assert "dataFreshBody" in resp.text
    assert "loadDataStatus" in resp.text
    assert "/api/v1/data/status" in resp.text


def test_dashboard_has_backfill_button():
    c = _client()
    resp = c.get("/dashboard/html")
    c.close()
    assert resp.status_code == 200
    assert "backfillStale" in resp.text
    assert "/api/v1/data/backfill/stale" in resp.text
    assert "BE</span>" in resp.text


def test_dashboard_has_signals_filter():
    c = _client()
    resp = c.get("/dashboard/html")
    c.close()
    assert resp.status_code == 200
    assert "signalsFilter" in resp.text
    assert "signalsFilterChange" in resp.text


def test_backfill_stale_endpoint(monkeypatch):
    ft = _FakeTrader()
    ft.binance = SimpleNamespace(client=object())
    main_mod.auto_trader = ft
    monkeypatch.setattr(main_mod.loader, "load_csv",
                        lambda symbol, interval="4h", data_dir=None, limit=None: (_ for _ in ()).throw(FileNotFoundError("missing")))

    async def fake_backfill(client, symbols, interval="4h", days=30,
                            data_dir=None, skip_stablecoins=True):
        return {"written": list(symbols), "failed": [], "skipped": [],
                "interval": interval, "days": days, "path": "/tmp"}

    monkeypatch.setattr(main_mod, "backfill_klines", fake_backfill)
    try:
        c = _client()
        resp = c.post("/api/v1/data/backfill/stale")
        c.close()
    finally:
        main_mod.auto_trader = None
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert set(body["symbols"]) == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    assert len(body["written"]) == 3


def test_backfill_stale_all_fresh(monkeypatch):
    ft = _FakeTrader()
    ft.binance = SimpleNamespace(client=object())
    main_mod.auto_trader = ft
    monkeypatch.setattr(main_mod.loader, "load_csv",
                        lambda symbol, interval="4h", data_dir=None, limit=None: _fresh_df())
    called = []

    async def fake_backfill(*a, **k):
        called.append(1)

    monkeypatch.setattr(main_mod, "backfill_klines", fake_backfill)
    try:
        c = _client()
        resp = c.post("/api/v1/data/backfill/stale")
        c.close()
    finally:
        main_mod.auto_trader = None
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["symbols"] == []
    assert called == []
