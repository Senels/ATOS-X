import numpy as np
import pandas as pd
from starlette.testclient import TestClient

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
        self.trading_mode = "paper"
        self.halt_entries = False
        self.risk_events = [{"time": "2026-01-01T00:00:00", "type": "drawdown_halt",
                             "message": "test"}]

    async def update_sl(self, symbol, new_sl):
        pos = self.active_positions.get(symbol)
        if not pos:
            return {"ok": False, "error": "position_not_found"}
        entry = float(pos["entry_price"])
        if pos["side"] == "BUY" and new_sl >= entry:
            return {"ok": False, "error": "sl_above_entry"}
        if pos["side"] == "SELL" and new_sl <= entry:
            return {"ok": False, "error": "sl_below_entry"}
        old = pos.get("sl")
        pos["sl"] = new_sl
        return {"ok": True, "symbol": symbol, "old_sl": old, "new_sl": new_sl}

    async def update_tp(self, symbol, new_tp):
        pos = self.active_positions.get(symbol)
        if not pos:
            return {"ok": False, "error": "position_not_found"}
        entry = float(pos["entry_price"])
        if pos["side"] == "BUY" and new_tp <= entry:
            return {"ok": False, "error": "tp_below_entry"}
        if pos["side"] == "SELL" and new_tp >= entry:
            return {"ok": False, "error": "tp_above_entry"}
        old = pos.get("tp")
        pos["tp"] = new_tp
        return {"ok": True, "symbol": symbol, "old_tp": old, "new_tp": new_tp}

    async def close_position(self, symbol, price, reason):
        self.active_positions.pop(symbol, None)


def test_health():
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("online", "starting", "initializing")
    assert "uptime" in body


def test_root_uses_settings_version():
    with TestClient(app) as client:
        resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["message"] == "🚀 ATOS X"
    assert body["version"] == main_mod.settings.APP_VERSION


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


def test_health_has_code_version():
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    cv = resp.json()["code_version"]
    assert "start_commit" in cv
    assert "current_commit" in cv
    assert "in_sync" in cv
    assert isinstance(cv["in_sync"], bool)


def test_git_head_format():
    head = main_mod.git_head()
    assert head == "unknown" or (
        len(head) == 40 and all(c in "0123456789abcdef" for c in head)
    )


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


def test_status_has_trading_field():
    client = TestClient(app)
    resp = client.get("/api/v1/status")
    assert resp.status_code == 200
    assert "trading" in resp.json()
    assert isinstance(resp.json()["trading"], bool)
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
    assert 'id="min_signal_strength"' in resp.text
    assert "Binance Futures Tum Coin Taramasi" in resp.text
    assert "/api/v1/backtest/market-symbols" in resp.text
    assert "/api/v1/backtest/scan/start" in resp.text
    assert "symbols', 'market'" in resp.text
    client.close()


def test_dashboard_has_priority_watchlist():
    client = TestClient(app)
    resp = client.get("/dashboard/html")
    assert resp.status_code == 200
    assert "Öncelikli İzleme" in resp.text
    assert "badge-paper" in resp.text
    assert "Equity Eğrisi" in resp.text
    assert "Sembol Bazlı PnL" in resp.text
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
    assert "<th>Koruma</th>" in resp.text
    assert "badge-protected" in resp.text
    assert "badge-unprotected" in resp.text
    client.close()


def test_dashboard_positions_table_has_actions():
    client = TestClient(app)
    resp = client.get("/dashboard/html")
    assert resp.status_code == 200
    assert "<th>İşlem</th>" in resp.text
    assert "applyPos" in resp.text
    assert "closePos" in resp.text
    assert "/api/v1/positions/" in resp.text
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


def test_position_sl_endpoint():
    fake = _FakeTrader({
        "BTCUSDT": {"side": "BUY", "entry_price": 100.0, "quantity": 2.0,
                    "sl": 95.0, "tp": 110.0},
    })
    main_mod.auto_trader = fake
    try:
        client = TestClient(app)
        resp = client.post("/api/v1/positions/BTCUSDT/sl",
                           json={"price": 97.0})
        client.close()
    finally:
        main_mod.auto_trader = None
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["new_sl"] == 97.0
    assert fake.active_positions["BTCUSDT"]["sl"] == 97.0


def test_position_sl_endpoint_invalid_direction():
    fake = _FakeTrader({
        "BTCUSDT": {"side": "BUY", "entry_price": 100.0, "quantity": 2.0,
                    "sl": 95.0, "tp": 110.0},
    })
    main_mod.auto_trader = fake
    try:
        client = TestClient(app)
        resp = client.post("/api/v1/positions/BTCUSDT/sl",
                           json={"price": 105.0})
        client.close()
    finally:
        main_mod.auto_trader = None
    assert resp.json()["ok"] is False
    assert resp.json()["error"] == "sl_above_entry"


def test_position_tp_endpoint():
    fake = _FakeTrader({
        "BTCUSDT": {"side": "BUY", "entry_price": 100.0, "quantity": 2.0,
                    "sl": 95.0, "tp": 110.0},
    })
    main_mod.auto_trader = fake
    try:
        client = TestClient(app)
        resp = client.post("/api/v1/positions/BTCUSDT/tp",
                           json={"price": 115.0})
        client.close()
    finally:
        main_mod.auto_trader = None
    body = resp.json()
    assert body["ok"] is True
    assert body["new_tp"] == 115.0
    assert fake.active_positions["BTCUSDT"]["tp"] == 115.0


def test_position_close_endpoint():
    fake = _FakeTrader({
        "BTCUSDT": {"side": "BUY", "entry_price": 100.0, "quantity": 2.0,
                    "sl": 95.0, "tp": 110.0},
    })
    fake.live_prices = {"BTCUSDT": 108.0}
    main_mod.auto_trader = fake
    try:
        client = TestClient(app)
        resp = client.post("/api/v1/positions/BTCUSDT/close")
        client.close()
    finally:
        main_mod.auto_trader = None
    body = resp.json()
    assert body["ok"] is True
    assert body["price"] == 108.0
    assert "BTCUSDT" not in fake.active_positions


def test_position_close_endpoint_missing_price():
    fake = _FakeTrader({
        "BTCUSDT": {"side": "BUY", "entry_price": 100.0, "quantity": 2.0,
                    "sl": 95.0, "tp": 110.0},
    })
    main_mod.auto_trader = fake
    main_mod.app.state.binance = None
    try:
        client = TestClient(app)
        resp = client.post("/api/v1/positions/BTCUSDT/close")
        client.close()
    finally:
        main_mod.auto_trader = None
        main_mod.app.state.binance = None
    assert resp.json()["ok"] is False
    assert resp.json()["error"] == "price_not_found"


def test_position_close_endpoint_not_found():
    fake = _FakeTrader({})
    main_mod.auto_trader = fake
    try:
        client = TestClient(app)
        resp = client.post("/api/v1/positions/ETHUSDT/close")
        client.close()
    finally:
        main_mod.auto_trader = None
    assert resp.json()["ok"] is False
    assert resp.json()["error"] == "position_not_found"


def test_position_edit_endpoints_not_running():
    main_mod.auto_trader = None
    client = TestClient(app)
    resp1 = client.post("/api/v1/positions/BTCUSDT/sl", json={"price": 90.0})
    resp2 = client.post("/api/v1/positions/BTCUSDT/tp", json={"price": 120.0})
    resp3 = client.post("/api/v1/positions/BTCUSDT/close")
    client.close()
    assert resp1.json() == {"ok": False, "error": "not_running"}
    assert resp2.json() == {"ok": False, "error": "not_running"}
    assert resp3.json() == {"ok": False, "error": "not_running"}


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
    assert all(0.0 <= s.get("strength", 0.0) <= 1.0 for s in body["signals"])
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
    assert "Canlı Sinyaller" in resp.text
    client.close()


def test_dashboard_signals_show_strength_column():
    client = TestClient(app)
    resp = client.get("/dashboard/html")
    assert resp.status_code == 200
    assert "Güç" in resp.text
    assert "s.strength" in resp.text
    assert "badge-buy" in resp.text
    client.close()


def test_dashboard_has_signals_interval_selector():
    client = TestClient(app)
    resp = client.get("/dashboard/html")
    assert resp.status_code == 200
    assert 'id="signalsInterval"' in resp.text
    assert "signalsIntervalChange" in resp.text
    assert "localStorage.getItem('signalsInterval')" in resp.text
    assert "/api/v1/signals?limit=10&interval=" in resp.text
    for iv in ("15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d"):
        assert 'value="' + iv + '"' in resp.text
    client.close()


def test_live_signals_interval_param():
    fake_klines = _FakeKlines()
    main_mod.app.state.binance = fake_klines
    ft = _FakeTrader({})
    ft.priority = ["BTCUSDT", "ETHUSDT"]
    main_mod.auto_trader = ft
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/signals?limit=5&interval=1h")
        client.close()
    finally:
        main_mod.auto_trader = None
        main_mod.app.state.binance = None
    assert resp.status_code == 200
    assert ("BTCUSDT", "1h", 400) in fake_klines.calls
    assert ("ETHUSDT", "1h", 400) in fake_klines.calls


def test_market_regime_endpoint():
    fake_klines = _FakeKlines()
    main_mod.app.state.binance = fake_klines
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/market/regime?symbol=BTCUSDT&interval=4h")
        client.close()
    finally:
        main_mod.app.state.binance = None
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["symbol"] == "BTCUSDT"
    assert body["interval"] == "4h"
    assert body["trend"]["regime"] in ("UP", "DOWN", "RANGE")
    assert body["volatility"]["regime"] in ("LOW", "NORMAL", "HIGH", "EXTREME")
    assert "liquidity" in body


def test_market_regimes_endpoint():
    fake_klines = _FakeKlines()
    main_mod.app.state.binance = fake_klines
    ft = _FakeTrader({})
    ft.priority = ["BTCUSDT", "ETHUSDT"]
    main_mod.auto_trader = ft
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/market/regimes?limit=5")
        client.close()
    finally:
        main_mod.auto_trader = None
        main_mod.app.state.binance = None
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert {r["symbol"] for r in body["regimes"]} == {"BTCUSDT", "ETHUSDT"}
    assert ("BTCUSDT", "4h", 400) in fake_klines.calls


def test_market_regimes_endpoint_not_running():
    main_mod.auto_trader = None
    client = TestClient(app)
    resp = client.get("/api/v1/market/regimes")
    client.close()
    assert resp.json() == {"regimes": [], "count": 0, "scanned": []}


def test_market_scores_endpoint():
    fake_klines = _FakeKlines()
    main_mod.app.state.binance = fake_klines
    ft = _FakeTrader({})
    ft.priority = ["BTCUSDT", "ETHUSDT"]
    main_mod.auto_trader = ft
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/market/scores?limit=5")
        client.close()
    finally:
        main_mod.auto_trader = None
        main_mod.app.state.binance = None
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert len(body["scores"]) == 2
    scores = [s["score"] for s in body["scores"]]
    assert scores == sorted(scores, reverse=True)
    first = body["scores"][0]
    assert first["symbol"] in ("BTCUSDT", "ETHUSDT")
    assert "momentum_pct" in first and "trend" in first


def test_market_scores_endpoint_not_running():
    main_mod.auto_trader = None
    client = TestClient(app)
    resp = client.get("/api/v1/market/scores")
    client.close()
    assert resp.json() == {"scores": [], "count": 0, "scanned": []}


def test_market_decision_endpoint():
    fake_klines = _FakeKlines()
    main_mod.app.state.binance = fake_klines
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/market/decision?symbol=BTCUSDT&interval=4h")
        client.close()
    finally:
        main_mod.app.state.binance = None
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["symbol"] == "BTCUSDT"
    assert body["verdict"] in ("BUY", "SELL", "HOLD")
    assert "confidence" in body and "votes" in body and "reason" in body


def test_market_decisions_endpoint():
    fake_klines = _FakeKlines()
    main_mod.app.state.binance = fake_klines
    ft = _FakeTrader({})
    ft.priority = ["BTCUSDT", "ETHUSDT"]
    main_mod.auto_trader = ft
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/market/decisions?limit=5")
        client.close()
    finally:
        main_mod.auto_trader = None
        main_mod.app.state.binance = None
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert {d["symbol"] for d in body["decisions"]} == {"BTCUSDT", "ETHUSDT"}
    order = {"BUY": 0, "SELL": 1, "HOLD": 2}
    verdicts = [order[d["verdict"]] for d in body["decisions"]]
    assert verdicts == sorted(verdicts)


def test_market_decisions_endpoint_not_running():
    main_mod.auto_trader = None
    client = TestClient(app)
    resp = client.get("/api/v1/market/decisions")
    client.close()
    assert resp.json() == {"decisions": [], "count": 0, "scanned": []}


def test_data_collect_endpoint(monkeypatch):
    captured = {}

    async def fake_collect(client, symbols, interval="4h", bars=400, skip_stablecoins=True):
        captured["symbols"] = symbols
        captured["interval"] = interval
        captured["bars"] = bars
        return {"written": symbols, "skipped": [], "failed": [],
                "interval": interval, "bars": bars, "path": "/tmp"}

    monkeypatch.setattr(main_mod, "collect_klines", fake_collect)
    ft = _FakeTrader({})
    ft.trading_symbols = ["BTCUSDT", "ETHUSDT"]
    ft.binance = object()
    main_mod.auto_trader = ft
    try:
        client = TestClient(app)
        resp = client.post("/api/v1/data/collect?symbols=BTCUSDT&interval=4h&bars=500")
        client.close()
    finally:
        main_mod.auto_trader = None
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert captured["symbols"] == ["BTCUSDT"]
    assert captured["interval"] == "4h"
    assert captured["bars"] == 500


def test_data_backfill_endpoint(monkeypatch):
    captured = {}

    async def fake_backfill(client, symbols, interval="4h", days=30, skip_stablecoins=True):
        captured["symbols"] = symbols
        captured["days"] = days
        return {"written": symbols, "failed": [], "interval": interval,
                "days": days, "path": "/tmp"}

    monkeypatch.setattr(main_mod, "backfill_klines", fake_backfill)
    ft = _FakeTrader({})
    ft.trading_symbols = ["BTCUSDT", "ETHUSDT"]
    ft.binance = object()
    main_mod.auto_trader = ft
    try:
        client = TestClient(app)
        resp = client.post("/api/v1/data/backfill?symbols=BTCUSDT&interval=4h&days=7")
        client.close()
    finally:
        main_mod.auto_trader = None
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert captured["symbols"] == ["BTCUSDT"]
    assert captured["days"] == 7


def test_data_endpoints_not_running():
    main_mod.auto_trader = None
    client = TestClient(app)
    resp1 = client.post("/api/v1/data/collect")
    resp2 = client.post("/api/v1/data/backfill")
    client.close()
    assert resp1.json()["error"] == "not_running"
    assert resp2.json()["error"] == "not_running"


def test_dashboard_has_decision_council_card():
    client = TestClient(app)
    resp = client.get("/dashboard/html")
    assert resp.status_code == 200
    assert "Decision Council" in resp.text
    assert "loadDecisions" in resp.text
    assert 'id="decisionBody"' in resp.text
    assert "/api/v1/market/decisions" in resp.text
    client.close()


def test_dashboard_has_coin_scores_card():
    client = TestClient(app)
    resp = client.get("/dashboard/html")
    assert resp.status_code == 200
    assert "Coin Skorları" in resp.text
    assert "loadScores" in resp.text
    assert 'id="scoreBody"' in resp.text
    assert "/api/v1/market/scores" in resp.text
    client.close()


def test_dashboard_has_market_regime_card():
    client = TestClient(app)
    resp = client.get("/dashboard/html")
    assert resp.status_code == 200
    assert "Piyasa Rejimi" in resp.text
    assert "loadRegimes" in resp.text
    assert 'id="regimeBody"' in resp.text
    assert "/api/v1/market/regimes" in resp.text
    client.close()


def test_settings_has_intelligence_toggles():
    client = TestClient(app)
    resp = client.get("/dashboard/settings")
    client.close()
    assert resp.status_code == 200
    assert 'id="r_council"' in resp.text
    assert 'id="r_council_conf"' in resp.text
    assert 'id="r_min_strength"' in resp.text
    assert 'id="r_score_rank"' in resp.text
    assert "use_decision_council" in resp.text
    assert "use_score_ranking" in resp.text
    assert "council_min_confidence" in resp.text
    assert "min_signal_strength" in resp.text


def test_metrics_endpoint_returns_prometheus_format():
    """The /metrics endpoint should return Prometheus text-format content."""
    client = TestClient(app)
    resp = client.get("/metrics")
    client.close()
    assert resp.status_code == 200
    # Prometheus text format always starts with a comment or metric line
    assert "atos_" in resp.text or resp.text.startswith("#")


def test_metrics_endpoint_content_type():
    """The /metrics endpoint should return a text/plain content-type."""
    client = TestClient(app)
    resp = client.get("/metrics")
    client.close()
    assert "text/plain" in resp.headers.get("content-type", "")
