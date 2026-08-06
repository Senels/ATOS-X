import sqlite3
from pathlib import Path

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


def test_close_trade_by_symbol_closes_all_open(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.save_trade("ETHUSDT", "BUY", 3000.0, 2.0)
    db.save_trade("ETHUSDT", "BUY", 3100.0, 2.0)
    db.close_trade_by_symbol("ETHUSDT", 3200.0, 200.0)

    rows = db.get_trades(limit=10)
    assert rows[0][7] == "CLOSED"
    assert rows[1][7] == "CLOSED"  # hayalet/mukerrer satirlar da kapanir (restore dongusu onlenir)


def test_save_signal(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    assert _signals_count(db) == 0
    db.save_signal("BTCUSDT", "BUY", 65000.0, 0.8, "test")
    assert _signals_count(db) == 1


def test_ttp_state_roundtrip(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    assert db.get_open_trade_ttp_state("BTCUSDT") == (None, False)

    db.save_trade("BTCUSDT", "BUY", 65000.0, 0.5, entry_ts="2026-08-05 12:00:00")
    assert db.get_open_trade_ttp_state("BTCUSDT") == ("2026-08-05 12:00:00", False)

    db.update_trade_protection("BTCUSDT", ttp_tp_hit=True)
    assert db.get_open_trade_ttp_state("BTCUSDT") == ("2026-08-05 12:00:00", True)

    db.update_trade_protection("BTCUSDT", ttp_tp_hit=False)
    assert db.get_open_trade_ttp_state("BTCUSDT") == ("2026-08-05 12:00:00", False)

    db.close_trade_by_symbol("BTCUSDT", 66000.0, 100.0)
    assert db.get_open_trade_ttp_state("BTCUSDT") == (None, False)


def test_ttp_state_legacy_columns_migrated(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    conn = sqlite3.connect(db.db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()]
    conn.close()
    assert "ttp_tp_hit" in cols
    assert "entry_ts" in cols


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


def test_clear_closed_trades_keeps_open(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.save_trade("BTCUSDT", "BUY", 65000.0, 0.5)
    db.close_trade_by_symbol("BTCUSDT", 66000.0, 500.0)
    db.save_trade("ETHUSDT", "BUY", 3000.0, 2.0)

    n = db.clear_closed_trades()
    assert n == 1
    rows = db.get_trades(limit=10)
    assert len(rows) == 1
    assert rows[0][7] == "OPEN"


def test_clear_operational_wipes_tables(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.save_signal("BTCUSDT", "BUY", 65000.0, 0.8, "test")
    db.save_backtest_run("BTCUSDT", "4h", "csv", {"a": 1}, {"b": 2})
    db.save_risk_event("block_add", "Engel: side:LONG", "2026-08-03T10:00:00")
    db.save_performance(10000.0, 1, 5, 60.0)

    counts = db.clear_operational()
    assert counts["signals"] == 1
    assert counts["backtest_runs"] == 1
    assert counts["risk_events"] == 1
    assert counts["performance"] == 1
    assert _signals_count(db) == 0
    assert db.get_risk_events() == []
    assert db.get_performance() == []


# -- yedekleme ------------------------------------------------------------
def test_backup_creates_copy(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.save_trade("BTCUSDT", "BUY", 65000.0, 0.5)

    res = db.backup(backup_dir=str(tmp_path / "bk"))
    assert res["ok"] is True
    assert Path(res["path"]).exists()

    conn = sqlite3.connect(res["path"])
    rows = conn.execute("SELECT * FROM trades").fetchall()
    conn.close()
    assert len(rows) == 1


def test_backup_retention_deletes_old(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.save_trade("BTCUSDT", "BUY", 65000.0, 0.5)

    for _ in range(5):
        db.backup(backup_dir=str(tmp_path / "bk"), keep=3)
    files = sorted(Path(tmp_path / "bk").glob("t_backup_*.db"))
    assert len(files) == 3


def test_backup_missing_db(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    Path(db.db_path).unlink()
    res = db.backup(backup_dir=str(tmp_path / "bk"))
    assert res["ok"] is False
    assert res["error"] == "db_not_found"


# -- fiyat alarmlari ------------------------------------------------------
def test_price_alert_save_and_get(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    assert db.save_price_alert("BTCUSDT", 65000.0, "above") is True
    rows = db.get_price_alerts()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["price"] == 65000.0
    assert rows[0]["side"] == "above"
    assert rows[0]["created"]


def test_price_alert_duplicate_idempotent(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.save_price_alert("BTCUSDT", 65000.0, "above")
    db.save_price_alert("BTCUSDT", 65000.0, "above")
    db.save_price_alert("BTCUSDT", 65000.0, "below")
    rows = db.get_price_alerts()
    assert len(rows) == 2


def test_price_alert_delete_and_clear(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.save_price_alert("BTCUSDT", 65000.0, "above")
    db.save_price_alert("ETHUSDT", 2900.0, "below")
    assert db.delete_price_alert("BTCUSDT", 65000.0, "above") is True
    rows = db.get_price_alerts()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "ETHUSDT"
    assert db.clear_price_alerts() == 1
    assert db.get_price_alerts() == []
