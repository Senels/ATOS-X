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

    def close_trade_by_symbol(self, symbol: str, exit_price: float, pnl: float):
        """Sembolun en guncel OPEN kaydini kapatir (canli trader icin)."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE trades
            SET exit_price = ?, pnl = ?, status = 'CLOSED', exit_time = CURRENT_TIMESTAMP
            WHERE id = (
                SELECT id FROM trades
                WHERE symbol = ? AND status = 'OPEN'
                ORDER BY entry_time DESC LIMIT 1
            )
        ''', (exit_price, pnl, symbol))
        conn.commit()
        conn.close()

    def get_open_trade_entry_time(self, symbol: str):
        """Sembolun en guncel OPEN kaydinin acilis zamanini doner (yoksa None)."""
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT entry_time FROM trades WHERE symbol = ? AND status = 'OPEN' "
            "ORDER BY entry_time DESC LIMIT 1", (symbol,)
        ).fetchone()
        conn.close()
        return row[0] if row and row[0] else None

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
