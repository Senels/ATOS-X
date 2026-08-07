from datetime import datetime, timedelta, timezone

import pytest

import app.strategy.auto_trader as at_mod
from app.core.database import Database


class FakeTelegram:
    async def send(self, message):
        pass

    async def send_signal(self, *a, **k):
        pass

    async def send_trade(self, *a, **k):
        pass

    async def send_stop_summary(self, *a, **k):
        pass


class FakeBinance:
    async def connect(self):
        return True

    async def get_price(self, symbol="BTCUSDT"):
        return 100.0

    async def get_all_tickers(self):
        return {"BTCUSDT": 100.0}

    async def place_market_order(self, symbol, side, quantity):
        return {"symbol": symbol, "side": side, "quantity": quantity}

    async def close_position(self, symbol):
        return {"symbol": symbol}


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "risk.db"))


@pytest.fixture
def make_trader(tmp_path, monkeypatch):
    def _make(**seed):
        d = Database(str(tmp_path / "rt.db"))
        if seed:
            d.save_state_batch(seed)
        monkeypatch.setattr(at_mod, "Database", lambda *a, **k: d)
        return at_mod.AutoTrader(FakeBinance(), paper=True), d

    return _make


def test_state_roundtrip(db):
    db.save_state_batch({"equity": 1234.5, "risk_halted": 1})
    assert db.get_state("equity") == "1234.5"
    state = db.get_all_state()
    assert float(state["equity"]) == pytest.approx(1234.5)
    assert int(state["risk_halted"]) == 1
    assert db.get_state("missing", "x") == "x"


def test_restore_equity_and_peak(make_trader):
    tr, _ = make_trader(equity=9500.0, peak_equity=12000.0)
    assert tr.equity == pytest.approx(9500.0)
    assert tr.peak_equity == pytest.approx(12000.0)


def test_restore_peak_clamped_to_equity(make_trader):
    tr, _ = make_trader(equity=11000.0, peak_equity=9000.0)
    assert tr.peak_equity == pytest.approx(11000.0)


def test_restore_same_day_daily_state(make_trader):
    today = _utc_now().date().isoformat()
    tr, _ = make_trader(day_start_date=today, day_pnl=-120.0, daily_loss_halted=1)
    assert tr.day_start_date == today
    assert tr.day_pnl == pytest.approx(-120.0)
    assert tr.daily_loss_halted is True


def test_restore_new_day_resets_daily(make_trader):
    yesterday = (_utc_now() - timedelta(days=1)).date().isoformat()
    tr, _ = make_trader(day_start_date=yesterday, day_pnl=-500.0, daily_loss_halted=1)
    assert tr.day_start_date == _utc_now().date().isoformat()
    assert tr.day_pnl == 0.0
    assert tr.daily_loss_halted is False


def test_restore_risk_halted(make_trader):
    tr, _ = make_trader(risk_halted=1)
    assert tr.risk_halted is True


def test_restore_no_state_defaults(make_trader):
    tr, _ = make_trader()
    assert tr.equity == pytest.approx(float(at_mod.strat_settings.get_settings()["initial_equity"]))
    assert tr.risk_halted is False
    assert tr.daily_loss_halted is False


def test_equity_halted_recomputed(make_trader):
    tr, _ = make_trader(equity=4000.0, equity_halted=1)
    assert tr.equity_halted is True
    tr2, _ = make_trader(equity=6000.0, equity_halted=1)
    assert tr2.equity_halted is False


def test_loss_halted_not_restored_from_stale_flag(make_trader):
    tr, _ = make_trader(loss_halted=1)
    assert tr.loss_halted is False


async def test_persist_after_open(make_trader):
    tr, db = make_trader()
    await tr.open_position("BTCUSDT", "BUY", 100.0, 90.0, 120.0, "test")
    assert "BTCUSDT" in tr.active_positions
    state = db.get_all_state()
    assert float(state["equity"]) == pytest.approx(tr.equity)
    assert float(state["peak_equity"]) == pytest.approx(tr.peak_equity)


async def test_persist_after_close(make_trader):
    tr, db = make_trader()
    await tr.open_position("BTCUSDT", "BUY", 100.0, 90.0, 120.0, "test")
    await tr.close_position("BTCUSDT", 105.0, "test_close")
    assert "BTCUSDT" not in tr.active_positions
    state = db.get_all_state()
    assert float(state["equity"]) == pytest.approx(tr.equity)
    assert float(state["day_pnl"]) == pytest.approx(tr.day_pnl)


async def test_update_daily_pnl_persists(make_trader):
    tr, db = make_trader()
    await tr._update_daily_pnl(-42.0)
    state = db.get_all_state()
    assert float(state["day_pnl"]) == pytest.approx(-42.0)


async def test_check_drawdown_persists(make_trader):
    tr, db = make_trader(equity=9000.0, peak_equity=12000.0)
    tr.max_drawdown_pct = 10.0
    await tr._check_drawdown()
    state = db.get_all_state()
    assert tr.risk_halted is True
    assert int(state["risk_halted"]) == 1
    assert float(state["peak_equity"]) == pytest.approx(12000.0)
def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

