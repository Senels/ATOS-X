from fastapi.testclient import TestClient

from app import main as main_mod
from app.main import app


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


def test_dashboard_pages():
    client = TestClient(app)
    cases = [
        ("/dashboard/html", "ATOS X Dashboard"),
        ("/dashboard/settings", "ATOS X - Strategy Manager"),
    ]
    for path, marker in cases:
        resp = client.get(path)
        assert resp.status_code == 200
        assert marker in resp.text, f"{path} dosyasi bulunamadi"
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
