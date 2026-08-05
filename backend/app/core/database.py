import json
import sqlite3
from datetime import datetime
from pathlib import Path

class Database:
    def __init__(self, db_path: str = "atos.db"):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                quantity REAL NOT NULL,
                pnl REAL,
                status TEXT DEFAULT 'OPEN',
                entry_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                exit_time TIMESTAMP
            )
        ''')
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN reason TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN trailing INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN breakeven INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                signal TEXT NOT NULL,
                price REAL NOT NULL,
                confidence REAL,
                reason TEXT,
                executed BOOLEAN DEFAULT 0,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                equity REAL,
                open_positions INTEGER,
                total_trades INTEGER,
                win_rate REAL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS backtest_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,
                source TEXT DEFAULT 'csv',
                params_json TEXT,
                metrics_json TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS risk_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                type TEXT NOT NULL,
                message TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS price_alerts (
                symbol TEXT NOT NULL,
                price REAL NOT NULL,
                side TEXT NOT NULL,
                created TEXT NOT NULL,
                PRIMARY KEY (symbol, price, side)
            )
        ''')
        conn.commit()
        conn.close()
        print("Veritabani hazir")

    def save_trade(self, symbol: str, side: str, entry_price: float, quantity: float):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO trades (symbol, side, entry_price, quantity, status)
            VALUES (?, ?, ?, ?, 'OPEN')
        ''', (symbol, side, entry_price, quantity))
        conn.commit()
        trade_id = cursor.lastrowid
        conn.close()
        return trade_id

    def close_trade(self, trade_id: int, exit_price: float, pnl: float):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE trades 
            SET exit_price = ?, pnl = ?, status = 'CLOSED', exit_time = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (exit_price, pnl, trade_id))
        conn.commit()
        conn.close()

    def close_trade_by_symbol(self, symbol: str, exit_price: float, pnl: float, reason: str = ""):
        """Sembolun en guncel OPEN kaydini kapatir (canli trader icin)."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE trades
            SET exit_price = ?, pnl = ?, status = 'CLOSED', exit_time = CURRENT_TIMESTAMP, reason = ?
            WHERE id = (
                SELECT id FROM trades
                WHERE symbol = ? AND status = 'OPEN'
                ORDER BY entry_time DESC LIMIT 1
            )
        ''', (exit_price, pnl, reason, symbol))
        conn.commit()
        conn.close()

    def get_closed_trades(self, limit: int = 200):
        """Kapanan islemleri trade_history formatinda (yeni -> eski) doner."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT symbol, side, entry_price, exit_price, quantity, pnl, exit_time, reason
            FROM trades
            WHERE status = 'CLOSED'
            ORDER BY id DESC LIMIT ?
        ''', (int(limit),))
        rows = cursor.fetchall()
        conn.close()
        return [{
            "symbol": r[0],
            "side": r[1],
            "entry": r[2],
            "exit": r[3],
            "qty": r[4],
            "pnl": r[5],
            "time": (datetime.fromisoformat(r[6]).isoformat() if r[6] else ""),
            "reason": r[7] or "",
        } for r in rows]

    def get_open_trade_entry_time(self, symbol: str):
        """Sembolun en guncel OPEN kaydinin acilis zamanini doner (yoksa None)."""
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT entry_time FROM trades WHERE symbol = ? AND status = 'OPEN' "
            "ORDER BY entry_time DESC LIMIT 1", (symbol,)
        ).fetchone()
        conn.close()
        return row[0] if row and row[0] else None

    def update_trade_protection(self, symbol: str, trailing: bool | None = None,
                                breakeven: bool | None = None):
        """Acik pozisyonun trailing/breakeven bayraklarini DB'de gunceller."""
        updates, params = [], []
        if trailing is not None:
            updates.append("trailing = ?")
            params.append(1 if trailing else 0)
        if breakeven is not None:
            updates.append("breakeven = ?")
            params.append(1 if breakeven else 0)
        if not updates:
            return
        params.append(symbol)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            f"UPDATE trades SET {', '.join(updates)} WHERE id = ("
            "SELECT id FROM trades WHERE symbol = ? AND status = 'OPEN' "
            "ORDER BY id DESC LIMIT 1)", params
        )
        conn.commit()
        conn.close()

    def get_open_trade_protection(self, symbol: str):
        """Acik pozisyonun DB'deki trailing/breakeven bayraklarini doner."""
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT trailing, breakeven FROM trades WHERE symbol = ? AND status = 'OPEN' "
            "ORDER BY id DESC LIMIT 1", (symbol,)
        ).fetchone()
        conn.close()
        if not row:
            return False, False
        return bool(row[0]), bool(row[1])

    def save_risk_event(self, event_type: str, message: str, ts: str):
        """Risk/blok olayini kalici olarak DB'ye yazar."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO risk_events (time, type, message) VALUES (?, ?, ?)",
            (ts, event_type, message),
        )
        conn.commit()
        conn.close()

    def get_risk_events(self, limit: int = 50):
        """En son risk/blok olaylarini zaman sirasiyla doner."""
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT time, type, message FROM risk_events "
            "ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [{"time": r[0], "type": r[1], "message": r[2]} for r in rows]

    def save_state(self, key: str, value):
        """Uygulama durumunu (skaler) kalici yazar; ayni anahtar ustune yazilir."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)",
            (key, str(value)),
        )
        conn.commit()
        conn.close()

    def save_state_batch(self, mapping: dict):
        """Uygulama durumlarini tek islemde topluca yazar."""
        conn = sqlite3.connect(self.db_path)
        conn.executemany(
            "INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)",
            [(k, str(v)) for k, v in mapping.items()],
        )
        conn.commit()
        conn.close()

    def get_state(self, key: str, default=None):
        """Uygulama durumunu doner; yoksa `default`."""
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT value FROM app_state WHERE key = ?", (key,)
        ).fetchone()
        conn.close()
        return row[0] if row else default

    def get_all_state(self) -> dict:
        """Tum uygulama durumunu anahtar -> metin olarak doner."""
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("SELECT key, value FROM app_state").fetchall()
        conn.close()
        return {k: v for k, v in rows}

    def save_signal(self, symbol: str, signal: str, price: float, confidence: float, reason: str = ""):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO signals (symbol, signal, price, confidence, reason)
            VALUES (?, ?, ?, ?, ?)
        ''', (symbol, signal, price, confidence, reason))
        conn.commit()
        conn.close()

    def save_performance(self, equity: float, open_positions: int, total_trades: int, win_rate: float):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO performance (equity, open_positions, total_trades, win_rate)
            VALUES (?, ?, ?, ?)
        ''', (equity, open_positions, total_trades, win_rate))
        conn.commit()
        conn.close()

    def get_trades(self, limit: int = 50):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM trades ORDER BY entry_time DESC LIMIT ?
        ''', (limit,))
        trades = cursor.fetchall()
        conn.close()
        return trades

    def get_performance(self, limit: int = 10):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM performance ORDER BY timestamp DESC LIMIT ?
        ''', (limit,))
        perfs = cursor.fetchall()
        conn.close()
        return perfs

    def get_performance_series(self, limit: int = 200):
        """Equity egrisi icin en eski -> en yeni sirada (timestamp, equity, open_positions)."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT timestamp, equity, open_positions FROM performance
            ORDER BY timestamp DESC LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        conn.close()
        rows.reverse()
        return rows

    def get_closed_trades_since(self, days: int = 1):
        """Son `days` gun icinde kapanan islemler (gunluk rapor icin)."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM trades
            WHERE status = 'CLOSED' AND exit_time >= datetime('now', ?)
            ORDER BY exit_time DESC
        ''', (f"-{int(days)} days",))
        rows = cursor.fetchall()
        conn.close()
        return rows

    def get_symbol_pnl(self, limit: int = 100):
        """Kapanan islemleri sembol bazinda toplar (net PnL buyukten kucuge)."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT symbol,
                   COUNT(*) AS trades,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) AS losses,
                   COALESCE(SUM(pnl), 0) AS net_pnl
            FROM trades
            WHERE status = 'CLOSED'
            GROUP BY symbol
            ORDER BY net_pnl DESC
            LIMIT ?
        ''', (int(limit),))
        rows = cursor.fetchall()
        conn.close()
        cols = ["symbol", "trades", "wins", "losses", "net_pnl"]
        return [dict(zip(cols, r)) for r in rows]

    def save_backtest_run(self, symbol: str, interval: str, source: str,
                          params: dict, metrics: dict):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO backtest_runs (symbol, interval, source, params_json, metrics_json)
            VALUES (?, ?, ?, ?, ?)
        ''', (symbol, interval, source,
              json.dumps(params, ensure_ascii=False, default=str),
              json.dumps(metrics, ensure_ascii=False, default=str)))
        conn.commit()
        run_id = cursor.lastrowid
        conn.close()
        return run_id

    def clear_closed_trades(self):
        """Kapanan islem kayitlarini siler; silinen satir sayisini doner."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.execute("DELETE FROM trades WHERE status = 'CLOSED'")
        conn.commit()
        n = cur.rowcount
        conn.close()
        return n

    def clear_operational(self):
        """Sinyal/backtest/risk/performans tablolarini bosaltir; sayilar doner."""
        counts = {}
        conn = sqlite3.connect(self.db_path)
        for table in ("signals", "backtest_runs", "risk_events", "performance"):
            cur = conn.execute(f"DELETE FROM {table}")
            counts[table] = cur.rowcount
        conn.commit()
        conn.close()
        return counts

    def get_backtest_runs(self, limit: int = 20, symbol: str | None = None):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        if symbol:
            cursor.execute('''
                SELECT * FROM backtest_runs
                WHERE symbol = ? ORDER BY created_at DESC, id DESC LIMIT ?
            ''', (symbol, limit))
        else:
            cursor.execute('''
                SELECT * FROM backtest_runs ORDER BY created_at DESC, id DESC LIMIT ?
            ''', (limit,))
        rows = cursor.fetchall()
        conn.close()
        cols = ["id", "created_at", "symbol", "interval", "source", "params_json", "metrics_json"]
        out = []
        for r in rows:
            d = dict(zip(cols, r))
            try:
                d["params"] = json.loads(d.pop("params_json"))
            except Exception:
                d["params"] = {}
            try:
                d["metrics"] = json.loads(d.pop("metrics_json"))
            except Exception:
                d["metrics"] = {}
            out.append(d)
        return out

    # -- yedekleme ---------------------------------------------------------
    def backup(self, backup_dir: str = None, keep: int = 14) -> dict:
        """SQLite online backup API ile tutarli bir kopya alir.

        `keep` adet en genc yedek saklanir; eskileri silinir. Sonuc:
        `{"ok": True, "path": ..., "kept": N, "deleted": [...]}`.
        """
        src = Path(self.db_path)
        if not src.exists():
            return {"ok": False, "error": "db_not_found"}
        base = Path(backup_dir) if backup_dir else src.parent / "backups"
        base.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        dst = base / f"{src.stem}_backup_{stamp}.db"

        source = sqlite3.connect(str(src))
        try:
            target = sqlite3.connect(str(dst))
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()

        backups = sorted(base.glob(f"{src.stem}_backup_*.db"))
        deleted = []
        for old in backups[:-keep]:
            try:
                old.unlink()
                deleted.append(old.name)
            except OSError:
                pass
        return {"ok": True, "path": str(dst), "kept": min(len(backups), keep),
                "deleted": deleted}

    # -- fiyat alarmlari ---------------------------------------------------
    def save_price_alert(self, symbol: str, price: float, side: str,
                         created: str = None) -> bool:
        """Alarmi kalici kaydeder; ayni (symbol, price, side) idempotent."""
        if created is None:
            created = datetime.now().isoformat()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO price_alerts (symbol, price, side, created)"
                " VALUES (?, ?, ?, ?)",
                (symbol, price, side, created))
            conn.commit()
            return True
        except sqlite3.Error:
            return False
        finally:
            conn.close()

    def get_price_alerts(self) -> list:
        """Tum alarmlari {symbol, price, side, created} sozlukleri olarak."""
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT symbol, price, side, created FROM price_alerts"
            ).fetchall()
        finally:
            conn.close()
        return [{"symbol": r[0], "price": r[1], "side": r[2], "created": r[3]}
                for r in rows]

    def delete_price_alert(self, symbol: str, price: float, side: str) -> bool:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "DELETE FROM price_alerts WHERE symbol=? AND price=? AND side=?",
                (symbol, price, side))
            conn.commit()
            return True
        except sqlite3.Error:
            return False
        finally:
            conn.close()

    def clear_price_alerts(self) -> int:
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute("DELETE FROM price_alerts")
            conn.commit()
            return cur.rowcount
        except sqlite3.Error:
            return 0
        finally:
            conn.close()
