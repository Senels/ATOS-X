from fastapi.testclient import TestClient

import numpy as np
import pandas as pd

from app import main as main_mod
from app.main import app


class _FakeKlines:
    def __init__(self):
        self.calls = []

    async def get_klines(self, symbol, interval, limit):
        self.calls.append((symbol, interval, limit))
        rng = np.random.default_rng(3)
        close = 100 + np.cumsum(rng.normal(0, 0.3, 120))
        high = close + 0.4
        low = close - 0.4
        open_ = np.roll(close, 1)
        open_[0] = close[0]
        vol = rng.uniform(50, 300, 120)
        return pd.DataFrame({"open": open_, "high": high, "low": low,
                             "close": close, "volume": vol})


class _FakeTrader:
    def __init__(self, positions):
        self.active_positions = positions
        self.trading_symbols = []
        self.trade_history = []
        self.equity = 10000.0
        self.paper = True
        self.top_symbols = []
        self.binance = None
        self._conc_blocks = set()
        self.max_position_pct = 75.0
        self.max_side_pct = 150.0
        self.drawdown_pct = 0.0
        self.risk_halted = False
        self.running = True
        self.loss_halted = False
        self.consecutive_losses = 0
        self.max_consecutive_losses = 5
        self.daily_loss_halted = False
        self.day_pnl = 0.0
        self.equity_halted = False
        self.min_equity = 0.0
        self.live_prices = {}
        self.risk_events = [{"time": "2026-01-01T00:00:00", "type": "drawdown_halt",
                             "message": "test"}]


def test_health():
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("online", "starting", "initializing")
    assert "uptime" in body


def test_health_has_concentration():
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    conc = resp.json()["concentration"]
    assert "long_pct" in conc
    assert "short_pct" in conc
    assert "blocks" in conc
    assert "max_position_pct" in conc
    assert "max_side_pct" in conc


def test_status_has_concentration():
    client = TestClient(app)
    resp = client.get("/api/v1/status")
    assert resp.status_code == 200
    assert "concentration" in resp.json()
    client.close()


def test_status_has_loss_fields():
    client = TestClient(app)
    resp = client.get("/api/v1/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "loss_halted" in body
    assert "consecutive_losses" in body
    client.close()


def test_dashboard_pages():
    client = TestClient(app)
    cases = [
        ("/dashboard/html", "ATOS X Dashboard"),
        ("/dashboard/settings", "ATOS X - Strategy Manager"),
        ("/optimize/html", "Parametre Optimizasyonu"),
        ("/backtest/html", "ATOS X Backtest"),
    ]
    for path, marker in cases:
        resp = client.get(path)
        assert resp.status_code == 200
        assert marker in resp.text, f"{path} dosyasi bulunamadi"
    client.close()


def test_backtest_compare_ui_present():
    client = TestClient(app)
    resp = client.get("/backtest/html")
    assert resp.status_code == 200
    assert "Karsilastir" in resp.text
    assert "cmp-cb" in resp.text
    assert "compare?a=" in resp.text
    assert "backtest/compare" in resp.text
    client.close()


def test_dashboard_has_priority_watchlist():
    client = TestClient(app)
    resp = client.get("/dashboard/html")
    assert resp.status_code == 200
    assert "Priority Watchlist" in resp.text
    assert "badge-paper" in resp.text
    assert "Equity Curve" in resp.text
    assert "PnL by Symbol" in resp.text
    client.close()


def test_priority_endpoint():
    client = TestClient(app)
    resp = client.get("/api/v1/priority")
    assert resp.status_code == 200
    body = resp.json()
    assert "count" in body
    assert isinstance(body["symbols"], list)
    assert isinstance(body["scanned"], list)
    client.close()


def test_equity_curve_endpoint():
    client = TestClient(app)
    resp = client.get("/api/v1/equity_curve?points=50")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["equity"], list)
    assert len(body["equity"]) == len(body["timestamps"])
    client.close()


def test_trades_summary_endpoint():
    client = TestClient(app)
    resp = client.get("/api/v1/trades/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["symbols"], list)
    assert "count" in body
    client.close()


def test_positions_protection_status():
    fake = _FakeTrader({
        "BTCUSDT": {"side": "BUY", "sl_order_id": "SL_1", "tp_order_id": "TP_1"},
        "ETHUSDT": {"side": "SELL", "sl_order_id": None, "tp_order_id": None},
        "SOLUSDT": {"side": "BUY", "sl_order_id": None, "tp_order_id": "TP_3"},
    })
    main_mod.auto_trader = fake
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/positions")
        client.close()
    finally:
        main_mod.auto_trader = None
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 3
    assert body["protected"] == 2
    assert body["unprotected"] == 1
    assert body["positions"]["BTCUSDT"]["protected"] is True
    assert body["positions"]["ETHUSDT"]["protected"] is False
    assert body["positions"]["SOLUSDT"]["protected"] is True


def test_positions_payload_has_unrealized_pnl():
    fake = _FakeTrader({
        "BTCUSDT": {"side": "BUY", "entry_price": 100.0, "quantity": 2.0,
                    "sl_order_id": "SL_1", "tp_order_id": "TP_1"},
        "ETHUSDT": {"side": "SELL", "entry_price": 200.0, "quantity": 1.0,
                    "sl_order_id": None, "tp_order_id": None},
    })
    fake.live_prices = {"BTCUSDT": 110.0, "ETHUSDT": 180.0}
    main_mod.auto_trader = fake
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/positions")
        client.close()
    finally:
        main_mod.auto_trader = None
    assert resp.status_code == 200
    body = resp.json()
    btc = body["positions"]["BTCUSDT"]
    eth = body["positions"]["ETHUSDT"]
    assert btc["mark"] == 110.0
    assert btc["upnl"] == 20.0
    assert btc["upnl_pct"] == 10.0
    assert eth["upnl"] == 20.0
    assert body["total_upnl"] == 40.0


def test_positions_payload_no_mark_price():
    fake = _FakeTrader({
        "BTCUSDT": {"side": "BUY", "entry_price": 100.0, "quantity": 2.0,
                    "sl_order_id": "SL_1", "tp_order_id": "TP_1"},
    })
    fake.live_prices = {}
    main_mod.auto_trader = fake
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/positions")
        client.close()
    finally:
        main_mod.auto_trader = None
    assert resp.status_code == 200
    pos = resp.json()["positions"]["BTCUSDT"]
    assert pos["mark"] is None
    assert pos["upnl"] is None


def test_metrics_positions_have_protection_flag():
    fake = _FakeTrader({
        "BTCUSDT": {"side": "BUY", "entry_price": 65000.0, "quantity": 0.5,
                    "sl_order_id": "SL_1", "tp_order_id": "TP_1"},
        "ETHUSDT": {"side": "SELL", "entry_price": 3000.0, "quantity": 2.0,
                    "sl_order_id": None, "tp_order_id": None},
    })
    main_mod.auto_trader = fake
    try:
        client = TestClient(app)
        resp = client.get("/dashboard/metrics")
        client.close()
    finally:
        main_mod.auto_trader = None
    assert resp.status_code == 200
    positions = resp.json()["positions"]
    assert positions["BTCUSDT"]["protected"] is True
    assert positions["ETHUSDT"]["protected"] is False


def test_dashboard_positions_table_has_protection():
    client = TestClient(app)
    resp = client.get("/dashboard/html")
    assert resp.status_code == 200
    assert "<th>Protection</th>" in resp.text
    assert "badge-protected" in resp.text
    assert "badge-unprotected" in resp.text
    client.close()


def test_risk_events_endpoint():
    client = TestClient(app)
    resp = client.get("/api/v1/risk/events")
    assert resp.status_code == 200
    body = resp.json()
    assert "events" in body
    assert isinstance(body["events"], list)
    assert "count" in body
    client.close()


def test_risk_events_type_filter():
    fake = _FakeTrader({})
    fake.risk_events = [
        {"time": "2026-08-03T10:00:00", "type": "drawdown_halt", "message": "a"},
        {"time": "2026-08-03T11:00:00", "type": "block_add", "message": "b"},
    ]
    main_mod.auto_trader = fake
    try:
        client = TestClient(app)
        r1 = client.get("/api/v1/risk/events?type=drawdown_halt")
        r2 = client.get("/api/v1/risk/events?type=trailing_move")
        client.close()
    finally:
        main_mod.auto_trader = None
    assert r1.json()["count"] == 1
    assert r1.json()["events"][0]["type"] == "drawdown_halt"
    assert r2.json()["count"] == 0


def test_risk_positions_endpoint():
    fake = _FakeTrader({
        "BTCUSDT": {"side": "BUY", "entry_price": 100.0, "quantity": 2.0,
                    "sl": 95.0, "tp": 110.0,
                    "sl_order_id": "SL_1", "tp_order_id": "TP_1",
                    "open_time": "2026-08-03T10:00:00"},
    })
    fake.live_prices = {"BTCUSDT": 105.0}
    main_mod.auto_trader = fake
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/risk/positions")
        client.close()
    finally:
        main_mod.auto_trader = None
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    btc = body["positions"]["BTCUSDT"]
    assert btc["notional"] == 200.0
    assert btc["sl_distance_pct"] == 5.0
    assert btc["risk_amount"] == 10.0
    assert btc["protected"] is True
    assert btc["upnl"] == 10.0
    assert btc["size_pct"] == 2.0
    assert btc["age_hours"] is not None
    assert body["total_notional"] == 200.0
    assert body["total_risk_amount"] == 10.0


def test_risk_positions_short_and_unprotected():
    fake = _FakeTrader({
        "ETHUSDT": {"side": "SELL", "entry_price": 200.0, "quantity": 1.0,
                    "sl": 210.0, "tp": 180.0,
                    "sl_order_id": None, "tp_order_id": None},
    })
    fake.live_prices = {"ETHUSDT": 190.0}
    main_mod.auto_trader = fake
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/risk/positions")
        client.close()
    finally:
        main_mod.auto_trader = None
    body = resp.json()
    eth = body["positions"]["ETHUSDT"]
    assert eth["sl_distance_pct"] == 5.0
    assert eth["risk_amount"] == 10.0
    assert eth["protected"] is False
    assert eth["upnl"] == 10.0


def test_live_signals_endpoint():
    fake_klines = _FakeKlines()
    main_mod.app.state.binance = fake_klines
    ft = _FakeTrader({})
    ft.priority = ["BTCUSDT", "ETHUSDT"]
    main_mod.auto_trader = ft
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/signals?limit=5")
        client.close()
    finally:
        main_mod.auto_trader = None
        main_mod.app.state.binance = None
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert {s["symbol"] for s in body["signals"]} == {"BTCUSDT", "ETHUSDT"}
    assert all(s["signal"] in ("BUY", "SELL", "HOLD") for s in body["signals"])
    assert body["scanned"] == ["BTCUSDT", "ETHUSDT"]
    assert fake_klines.calls == [("BTCUSDT", "4h", 400), ("ETHUSDT", "4h", 400)]


def test_live_signals_endpoint_empty_when_no_trader():
    main_mod.auto_trader = None
    client = TestClient(app)
    resp = client.get("/api/v1/signals")
    client.close()
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


def test_live_signals_endpoint_no_candidates():
    ft = _FakeTrader({})
    ft.priority = []
    ft.trading_symbols = []
    main_mod.auto_trader = ft
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/signals")
        client.close()
    finally:
        main_mod.auto_trader = None
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


def test_dashboard_has_live_signals_card():
    client = TestClient(app)
    resp = client.get("/dashboard/html")
    assert resp.status_code == 200
    assert "Live Signals" in resp.text
    client.close()
