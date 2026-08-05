import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_mod
from app.core.database import Database


class _FakeTrader:
    def __init__(self, running=False):
        self.running = running


class _FakeOpsDB:
    def __init__(self, db_path, backup_res=None, restore_res=None):
        self.db_path = db_path
        self.backup_res = backup_res
        self.restore_res = restore_res
        self.restore_calls = []

    def backup(self):
        return self.backup_res or {"ok": True, "path": "x.db", "kept": 3,
                                   "deleted": [], "integrity": "ok", "size": 100}

    def restore(self, path):
        self.restore_calls.append(path)
        return self.restore_res or {"ok": True, "path": path, "previous": "old.db",
                                    "integrity": "ok", "tables": 5}


# ---- Database.backup integrity ----

def test_backup_integrity_ok(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.save_trade("BTCUSDT", "BUY", 65000.0, 0.5)
    res = db.backup(backup_dir=str(tmp_path / "bk"))
    assert res["ok"] is True
    assert res["integrity"] == "ok"
    assert res["size"] > 0
    conn = sqlite3.connect(res["path"])
    try:
        rows = conn.execute("SELECT * FROM trades").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1


# ---- Database.restore ----

def test_restore_overwrites_db(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.save_trade("BTCUSDT", "BUY", 65000.0, 0.5)
    backup_path = db.backup(backup_dir=str(tmp_path / "bk"))["path"]
    db.save_trade("ETHUSDT", "BUY", 3000.0, 2.0)

    out = db.restore(backup_path)
    assert out["ok"] is True
    assert out["tables"] >= 1
    assert out["previous"] is not None
    assert Path(out["previous"]).exists()
    conn = sqlite3.connect(db.db_path)
    try:
        rows = conn.execute("SELECT * FROM trades").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1


def test_restore_rejects_corrupt(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    bad = tmp_path / "corrupt.db"
    bad.write_bytes(b"not a sqlite database at all")
    out = db.restore(str(bad))
    assert out["ok"] is False
    assert out["error"] == "integrity_check_failed"


def test_restore_missing_file(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    out = db.restore(str(tmp_path / "nope.db"))
    assert out["ok"] is False
    assert out["error"] == "backup_not_found"


# ---- Telegram komutlari ----

def test_command_yedekler_lists(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.save_trade("BTCUSDT", "BUY", 65000.0, 0.5)
    db.backup(backup_dir=str(tmp_path / "backups"))
    main_mod.app.state.db = db
    main_mod.auto_trader = None
    try:
        reply = main_mod._telegram_command("/yedekler")
    finally:
        main_mod.app.state.db = None
    assert reply is not None
    assert "son yedekler" in reply
    assert "t_backup_" in reply


def test_command_yedekler_none(tmp_path):
    main_mod.app.state.db = Database(str(tmp_path / "t.db"))
    main_mod.auto_trader = None
    try:
        reply = main_mod._telegram_command("/yedekler")
    finally:
        main_mod.app.state.db = None
    assert reply is not None
    assert "yedek yok" in reply


def test_command_geriyukle_running_blocked(tmp_path):
    main_mod.app.state.db = Database(str(tmp_path / "t.db"))
    main_mod.auto_trader = _FakeTrader(running=True)
    try:
        reply = main_mod._telegram_command("/geriyukle dosya.db")
    finally:
        main_mod.auto_trader = None
        main_mod.app.state.db = None
    assert reply is not None
    assert "motoru durdurun" in reply


def test_command_geriyukle_restores(tmp_path):
    fake = _FakeOpsDB(str(tmp_path / "t.db"))
    main_mod.app.state.db = fake
    main_mod.auto_trader = _FakeTrader(running=False)
    try:
        reply = main_mod._telegram_command("/geriyukle a_backup.db")
    finally:
        main_mod.auto_trader = None
        main_mod.app.state.db = None
    assert reply is not None
    assert "DB geri yuklendi" in reply
    assert fake.restore_calls == [str(Path(tmp_path / "t.db").parent / "backups" / "a_backup.db")]


def test_command_geriyukle_usage(tmp_path):
    main_mod.app.state.db = _FakeOpsDB(str(tmp_path / "t.db"))
    main_mod.auto_trader = _FakeTrader(running=False)
    try:
        reply = main_mod._telegram_command("/geriyukle")
    finally:
        main_mod.auto_trader = None
        main_mod.app.state.db = None
    assert reply is not None
    assert "kullanim" in reply


# ---- REST ----

def test_api_backup_trigger(tmp_path):
    main_mod.app.state.db = _FakeOpsDB(str(tmp_path / "t.db"))
    try:
        client = TestClient(main_mod.app)
        resp = client.post("/api/v1/backup")
        client.close()
    finally:
        main_mod.app.state.db = None
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_api_backups_list(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.save_trade("BTCUSDT", "BUY", 65000.0, 0.5)
    db.backup(backup_dir=str(tmp_path / "backups"))
    main_mod.app.state.db = db
    try:
        client = TestClient(main_mod.app)
        resp = client.get("/api/v1/backups")
        client.close()
    finally:
        main_mod.app.state.db = None
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert len(body["items"]) >= 1
    assert body["items"][0]["size"] > 0


def test_api_restore_refuses_running(tmp_path):
    main_mod.app.state.db = _FakeOpsDB(str(tmp_path / "t.db"))
    main_mod.auto_trader = _FakeTrader(running=True)
    try:
        client = TestClient(main_mod.app)
        resp = client.post("/api/v1/backup/restore", json={"path": "x.db"})
        client.close()
    finally:
        main_mod.auto_trader = None
        main_mod.app.state.db = None
    assert resp.status_code == 200
    assert resp.json()["error"] == "motor_calisiyor"


def test_api_restore_runs(tmp_path):
    fake = _FakeOpsDB(str(tmp_path / "t.db"))
    main_mod.app.state.db = fake
    main_mod.auto_trader = _FakeTrader(running=False)
    try:
        client = TestClient(main_mod.app)
        resp = client.post("/api/v1/backup/restore", json={"path": "x.db"})
        client.close()
    finally:
        main_mod.auto_trader = None
        main_mod.app.state.db = None
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert fake.restore_calls == ["x.db"]
