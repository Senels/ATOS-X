import pytest
from starlette.testclient import TestClient

from app import main as main_mod
from app.core.database import Database
from app.main import app
from app.strategy import auto_trader as at_mod


class FakeBinance:
    def __init__(self):
        self.balance = {"balance": 12000.0, "available": 8000.0, "unrealized": 500.0}
        self.open_positions = []
        self.algo_orders = []

    async def get_account_balance(self):
        return dict(self.balance)

    async def get_open_positions(self):
        return self.open_positions

    async def get_open_algo_orders(self):
        return self.algo_orders


class PlainBinance:
    """get_account_balance yontemine sahip olmayan istemci."""

    async def get_open_positions(self):
        return []

    async def get_open_algo_orders(self):
        return []


@pytest.fixture
def trader(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "at.db"))
    monkeypatch.setattr(at_mod, "Database", lambda *a, **k: db)
    fb = FakeBinance()
    tr = at_mod.AutoTrader(fb, paper=False)
    return tr, fb, db


async def test_sync_balance_updates_equity(trader):
    tr, fb, db = trader
    await tr._sync_balance()
    assert tr.equity == 12500.0
    assert tr.peak_equity == 12500.0
    assert tr.live_balance == fb.balance


async def test_sync_balance_drawdown_from_real_balance(trader):
    tr, fb, db = trader
    tr.peak_equity = 20000.0
    await tr._sync_balance()
    assert tr.equity == 12500.0
    assert tr.drawdown_pct == pytest.approx(37.5, abs=0.01)


async def test_sync_balance_keeps_peak_when_greater(trader):
    tr, fb, db = trader
    tr.peak_equity = 30000.0
    await tr._sync_balance()
    assert tr.peak_equity == 30000.0
    assert tr.drawdown_pct == pytest.approx(58.33, abs=0.01)


async def test_sync_balance_skips_without_method(trader):
    tr, fb, db = trader
    tr.binance = PlainBinance()
    await tr._sync_balance()
    assert tr.live_balance is None
    assert tr.equity == 10000.0


async def test_sync_balance_ignores_invalid_balance(trader):
    tr, fb, db = trader
    fb.balance = {"balance": 0.0, "available": 0.0, "unrealized": 0.0}
    await tr._sync_balance()
    assert tr.equity == 10000.0
    assert tr.live_balance is None


async def test_reconcile_syncs_balance(trader):
    tr, fb, db = trader
    await tr.reconcile_positions()
    assert tr.equity == 12500.0
    assert tr.live_balance == fb.balance


class _FakeTrader:
    def __init__(self):
        self.active_positions = {}
        self.live_prices = {}
        self.equity = 12500.0
        self.peak_equity = 15000.0
        self.drawdown_pct = 16.67
        self.day_pnl = 120.5
        self.paper = False
        self.trading_mode = "live"
        self.halt_entries = False
        self.live_balance = {"balance": 12000.0, "available": 8000.0,
                             "unrealized": 500.0}


def _fake_position(symbol="BTCUSDT", side="BUY", entry=100.0, qty=2.0, mark=110.0):
    return {symbol: {"side": side, "entry_price": entry, "quantity": qty,
                     "sl": 95.0, "tp": 110.0,
                     "sl_order_id": "SL_1", "tp_order_id": "TP_1"}}


def test_portfolio_synced_live():
    ft = _FakeTrader()
    ft.active_positions = _fake_position()
    ft.live_prices = {"BTCUSDT": 110.0}
    main_mod.auto_trader = ft
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/portfolio")
        client.close()
    finally:
        main_mod.auto_trader = None
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "live"
    assert body["synced"] is True
    assert body["balance"] == 12000.0
    assert body["available"] == 8000.0
    assert body["unrealized_pnl"] == 500.0
    assert body["equity"] == 12500.0
    assert body["drawdown_pct"] == 16.67
    assert body["count"] == 1
    pos = body["positions"][0]
    assert pos["symbol"] == "BTCUSDT"
    assert pos["upnl"] == 20.0
    assert pos["notional"] == 200.0
    assert pos["protected"] is True
    assert body["total_notional"] == 200.0


def test_portfolio_paper_computes_upnl():
    ft = _FakeTrader()
    ft.paper = True
    ft.trading_mode = "paper"
    ft.live_balance = None
    ft.active_positions = _fake_position(side="SELL", entry=200.0, qty=1.0, mark=190.0)
    ft.live_prices = {"BTCUSDT": 190.0}
    main_mod.auto_trader = ft
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/portfolio")
        client.close()
    finally:
        main_mod.auto_trader = None
    body = resp.json()
    assert body["mode"] == "paper"
    assert body["synced"] is False
    assert body["balance"] is None
    assert body["unrealized_pnl"] == 10.0


def test_portfolio_empty_when_no_trader():
    main_mod.auto_trader = None
    client = TestClient(app)
    resp = client.get("/api/v1/portfolio")
    client.close()
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0
    assert body["equity"] == 10000.0


def test_dashboard_has_portfolio_card():
    client = TestClient(app)
    resp = client.get("/dashboard/html")
    client.close()
    assert resp.status_code == 200
    assert "Portfolio" in resp.text
    assert "loadPortfolio" in resp.text
    assert 'id="portEquity"' in resp.text
    assert "/api/v1/portfolio" in resp.text
