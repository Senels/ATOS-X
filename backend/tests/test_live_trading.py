import app.strategy.auto_trader as at_mod
import pytest
from app import main as main_mod
from app.core.database import Database
from app.main import app
from fastapi.testclient import TestClient


class FakeTelegram:
    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(message)


class FakeBinance:
    def __init__(self):
        self.open_calls = []
        self.client = None
        self.testnet = False

    async def connect(self):
        self.client = True
        return True

    async def load_all_symbols(self):
        return ["BTCUSDT", "ETHUSDT"]

    async def get_all_tickers(self):
        return {"BTCUSDT": 65000.0, "ETHUSDT": 3000.0}

    async def get_klines(self, symbol, interval, limit):
        return None

    async def get_price(self, symbol="BTCUSDT"):
        return 65000.0

    async def place_market_order(self, symbol, side, quantity):
        self.open_calls.append((symbol, side, quantity))
        return {"symbol": symbol, "side": side, "quantity": quantity}

    async def close_position(self, symbol):
        return {"symbol": symbol}


@pytest.fixture
def trader(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "at.db"))
    monkeypatch.setattr(at_mod, "Database", lambda *a, **k: db)
    fb = FakeBinance()
    tr = at_mod.AutoTrader(fb, paper=False, live_trading_enabled=True)
    return tr, fb, db


def _make_trader(tmp_path, monkeypatch, **kwargs):
    db = Database(str(tmp_path / "at.db"))
    monkeypatch.setattr(at_mod, "Database", lambda *a, **k: db)
    return at_mod.AutoTrader(FakeBinance(), **kwargs)


# ---- trading mode ----

def test_mode_paper(tmp_path, monkeypatch):
    tr = _make_trader(tmp_path, monkeypatch, paper=True)
    assert tr.trading_mode == "paper"


def test_mode_kill_switch(tmp_path, monkeypatch):
    tr = _make_trader(tmp_path, monkeypatch, paper=False)
    assert tr.trading_mode == "kill-switch"
    assert tr.live_trading_enabled is False


def test_mode_testnet(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "at.db"))
    monkeypatch.setattr(at_mod, "Database", lambda *a, **k: db)
    fb = FakeBinance()
    fb.testnet = True
    tr = at_mod.AutoTrader(fb, paper=False, live_trading_enabled=True)
    assert tr.trading_mode == "testnet"


def test_mode_live(tmp_path, monkeypatch):
    tr = _make_trader(tmp_path, monkeypatch, paper=False, live_trading_enabled=True)
    assert tr.trading_mode == "live"


# ---- kill-switch ----

async def test_submit_open_kill_switch_blocks(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "at.db"))
    monkeypatch.setattr(at_mod, "Database", lambda *a, **k: db)
    fb = FakeBinance()
    tr = at_mod.AutoTrader(fb, paper=False)  # live_trading_enabled default False
    res = await tr._submit_open("BTCUSDT", "BUY", 0.1)
    assert res is None
    assert fb.open_calls == []
    assert any(e["type"] == "live_order_blocked" for e in tr.risk_events)


async def test_submit_open_live_passes(trader):
    tr, fb, _db = trader
    res = await tr._submit_open("BTCUSDT", "BUY", 0.1)
    assert res == {"symbol": "BTCUSDT", "side": "BUY", "quantity": 0.1}
    assert fb.open_calls == [("BTCUSDT", "BUY", 0.1)]


async def test_submit_open_paper_skips_exchange(tmp_path, monkeypatch):
    tr = _make_trader(tmp_path, monkeypatch, paper=True)
    fb = tr.binance
    res = await tr._submit_open("BTCUSDT", "BUY", 0.1)
    assert res == {"symbol": "BTCUSDT", "side": "BUY", "quantity": 0.1, "paper": True}
    assert fb.open_calls == []


# ---- min-notional ----

async def test_min_notional_blocks_entry(tmp_path, monkeypatch):
    tr = _make_trader(tmp_path, monkeypatch, paper=True, min_notional=100000.0)
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
    assert "BTCUSDT" not in tr.active_positions
    assert any(e["type"] == "min_notional_blocked" for e in tr.risk_events)


async def test_min_notional_zero_disabled(trader):
    tr, _fb, _db = trader
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
    assert "BTCUSDT" in tr.active_positions


async def test_min_notional_allows_large_entry(tmp_path, monkeypatch):
    tr = _make_trader(tmp_path, monkeypatch, paper=True, min_notional=10.0)
    await tr.open_position("BTCUSDT", "BUY", 65000.0, 63000.0, 69000.0)
    assert "BTCUSDT" in tr.active_positions


# ---- halt_entries ----

async def test_halt_entries_blocks_new_entries(trader):
    tr, _fb, _db = trader
    tr.halt_entries = True
    await tr.process_signals([{
        "symbol": "BTCUSDT", "signal": "BUY", "price": 65000.0,
        "sl": 63000.0, "tp": 69000.0, "reason": "test",
    }])
    assert "BTCUSDT" not in tr.active_positions


async def test_halt_entries_allows_when_off(trader):
    tr, _fb, _db = trader
    await tr.process_signals([{
        "symbol": "BTCUSDT", "signal": "BUY", "price": 65000.0,
        "sl": 63000.0, "tp": 69000.0, "reason": "test",
    }])
    assert "BTCUSDT" in tr.active_positions


class _FakeGiris:
    def __init__(self):
        self.halt_entries = False
        self.trading_mode = "paper"
        self.risk_events = []
        self.running = True
        self.trading_symbols = []
        self.active_positions = {}
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
        self.loss_halted = False
        self.consecutive_losses = 0
        self.max_consecutive_losses = 5
        self.daily_loss_halted = False
        self.day_pnl = 0.0
        self.equity_halted = False
        self.min_equity = 0.0
        self.live_prices = {}
        self.live_trading_enabled = False

    def _log_risk_event(self, event_type, message, **extra):
        self.risk_events.append(event_type)


def test_command_giris_toggle():
    fake = _FakeGiris()
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/giris kapali")
        assert fake.halt_entries is True
        assert "durduruldu" in reply
        assert "halt_entries" in fake.risk_events

        reply = main_mod._telegram_command("/giris acik")
        assert fake.halt_entries is False
        assert "acildi" in reply

        reply = main_mod._telegram_command("/giris")
        assert "acik" in reply

        reply = main_mod._telegram_command("/giris kapali")
        reply = main_mod._telegram_command("/giris")
        assert "KAPALI" in reply
    finally:
        main_mod.auto_trader = None


def test_api_halt_entries_endpoint():
    fake = _FakeGiris()
    main_mod.auto_trader = fake
    try:
        client = TestClient(app)
        resp = client.post("/api/v1/halt_entries", json={"halt": True})
        client.close()
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "halt_entries": True}
        assert fake.halt_entries is True
    finally:
        main_mod.auto_trader = None


def test_status_has_trading_mode():
    fake = _FakeGiris()
    fake.trading_mode = "kill-switch"
    main_mod.auto_trader = fake
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/status")
        client.close()
        assert resp.status_code == 200
        body = resp.json()
        assert body["trading_mode"] == "kill-switch"
        assert body["halt_entries"] is False
    finally:
        main_mod.auto_trader = None
