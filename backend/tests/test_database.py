import sqlite3

from app.core.database import Database


def _signals_count(db) -> int:
    conn = sqlite3.connect(db.db_path)
    n = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    conn.close()
    return n


def test_trade_open_close_by_symbol(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    tid = db.save_trade("BTCUSDT", "BUY", 65000.0, 0.5)
    assert tid > 0

    rows = db.get_trades(limit=10)
    assert len(rows) == 1
    assert rows[0][7] == "OPEN"

    db.close_trade_by_symbol("BTCUSDT", 64000.0, -123.45)
    rows = db.get_trades(limit=10)
    assert rows[0][7] == "CLOSED"
    assert rows[0][6] == -123.45
    assert rows[0][4] == 64000.0


def test_close_trade_by_symbol_closes_latest_open(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.save_trade("ETHUSDT", "BUY", 3000.0, 2.0)
    db.save_trade("ETHUSDT", "BUY", 3100.0, 2.0)
    db.close_trade_by_symbol("ETHUSDT", 3200.0, 200.0)

    rows = db.get_trades(limit=10)
    assert rows[0][7] == "CLOSED"
    assert rows[1][7] == "OPEN"


def test_save_signal(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    assert _signals_count(db) == 0
    db.save_signal("BTCUSDT", "BUY", 65000.0, 0.8, "test")
    assert _signals_count(db) == 1


def test_backtest_runs_roundtrip(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    rid = db.save_backtest_run("BTCUSDT", "4h", "csv", {"risk_per_trade": 0.02},
                               {"total_trades": 10, "net_profit": 100.0})
    assert rid > 0

    runs = db.get_backtest_runs(limit=10)
    assert len(runs) == 1
    run = runs[0]
    assert run["symbol"] == "BTCUSDT"
    assert run["interval"] == "4h"
    assert run["source"] == "csv"
    assert run["params"]["risk_per_trade"] == 0.02
    assert run["metrics"]["total_trades"] == 10


def test_backtest_runs_filter_by_symbol(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.save_backtest_run("BTCUSDT", "4h", "csv", {}, {"total_trades": 5})
    db.save_backtest_run("ETHUSDT", "4h", "csv", {}, {"total_trades": 7})

    runs = db.get_backtest_runs(limit=10, symbol="ETHUSDT")
    assert len(runs) == 1
    assert runs[0]["symbol"] == "ETHUSDT"


def test_closed_trades_since_returns_only_closed(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.save_trade("BTCUSDT", "BUY", 65000.0, 0.5)
    db.close_trade_by_symbol("BTCUSDT", 66000.0, 500.0)
    db.save_trade("ETHUSDT", "BUY", 3000.0, 2.0)  # hala OPEN

    rows = db.get_closed_trades_since(days=1)
    assert len(rows) == 1
    assert rows[0][1] == "BTCUSDT"
    assert rows[0][6] == 500.0


def test_symbol_pnl_aggregates(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.save_trade("BTCUSDT", "BUY", 65000.0, 0.5)
    db.close_trade_by_symbol("BTCUSDT", 66000.0, 500.0)
    db.save_trade("BTCUSDT", "BUY", 65000.0, 0.5)
    db.close_trade_by_symbol("BTCUSDT", 63000.0, -200.0)
    db.save_trade("ETHUSDT", "BUY", 3000.0, 2.0)
    db.close_trade_by_symbol("ETHUSDT", 3300.0, 600.0)
    db.save_trade("XRPUSDT", "BUY", 1.0, 100.0)  # OPEN, dahil edilmez

    rows = db.get_symbol_pnl()
    assert len(rows) == 2
    assert rows[0] == {"symbol": "ETHUSDT", "trades": 1, "wins": 1, "losses": 0, "net_pnl": 600.0}
    assert rows[1] == {"symbol": "BTCUSDT", "trades": 2, "wins": 1, "losses": 1, "net_pnl": 300.0}


def test_open_trade_protection_defaults_false(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.save_trade("BTCUSDT", "BUY", 65000.0, 0.5)
    assert db.get_open_trade_protection("BTCUSDT") == (False, False)
    assert db.get_open_trade_protection("ETHUSDT") == (False, False)


def test_update_trade_protection_persists(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.save_trade("BTCUSDT", "BUY", 65000.0, 0.5)
    db.update_trade_protection("BTCUSDT", trailing=True)
    assert db.get_open_trade_protection("BTCUSDT") == (True, False)
    db.update_trade_protection("BTCUSDT", breakeven=True)
    assert db.get_open_trade_protection("BTCUSDT") == (True, True)


def test_update_trade_protection_targets_latest_open(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.save_trade("BTCUSDT", "BUY", 65000.0, 0.5)
    db.close_trade_by_symbol("BTCUSDT", 66000.0, 500.0)
    db.save_trade("BTCUSDT", "BUY", 64000.0, 0.5)
    db.update_trade_protection("BTCUSDT", breakeven=True)
    assert db.get_open_trade_protection("BTCUSDT") == (False, True)

    rows = db.get_trades(limit=10)
    open_rows = [r for r in rows if r[7] == "OPEN"]
    assert len(open_rows) == 1
    assert open_rows[0][11] == 0  # trailing
    assert open_rows[0][12] == 1  # breakeven
