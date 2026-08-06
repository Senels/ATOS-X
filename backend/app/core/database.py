import json
import shutil
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
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN ttp_tp_hit INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN entry_ts TEXT")
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
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                signal TEXT NOT NULL,
                ai_direction TEXT,
                ai_confidence REAL,
                council_confidence REAL,
                strength REAL,
                price REAL NOT NULL,
                bar_ts TEXT,
                executed INTEGER DEFAULT 0,
                outcome TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
        print("Veritabani hazir")

    def save_trade(self, symbol: str, side: str, entry_price: float, quantity: float,
                   entry_ts: str = None, ttp_tp_hit: int = 0):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO trades (symbol, side, entry_price, quantity, entry_ts, ttp_tp_hit, status)
            VALUES (?, ?, ?, ?, ?, ?, 'OPEN')
        ''', (symbol, side, entry_price, quantity, entry_ts, 1 if ttp_tp_hit else 0))
        conn.commit()
        trade_id = cursor.lastrowid
        conn.close()
        return trade_id

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

    def reduce_trade_quantity(self, symbol: str, new_qty: float):
        """Acik pozisyonun miktarini gunceller (kismi kapanis sonrasi)."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE trades SET quantity = ? WHERE id = ("
            "SELECT id FROM trades WHERE symbol = ? AND status = 'OPEN' "
            "ORDER BY id DESC LIMIT 1)", (float(new_qty), symbol)
        )
        conn.commit()
        conn.close()

    def update_trade_protection(self, symbol: str, trailing: bool | None = None,
                                breakeven: bool | None = None, ttp_tp_hit: bool | None = None):
        """Acik pozisyonun trailing/breakeven/ttp_tp_hit bayraklarini DB'de gunceller."""
        updates, params = [], []
        if trailing is not None:
            updates.append("trailing = ?")
            params.append(1 if trailing else 0)
        if breakeven is not None:
            updates.append("breakeven = ?")
            params.append(1 if breakeven else 0)
        if ttp_tp_hit is not None:
            updates.append("ttp_tp_hit = ?")
            params.append(1 if ttp_tp_hit else 0)
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

    def get_open_trade_ttp_state(self, symbol: str):
        """Acik pozisyonun DB'deki entry_ts ve ttp_tp_hit durumunu doner."""
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT entry_ts, ttp_tp_hit FROM trades WHERE symbol = ? AND status = 'OPEN' "
            "ORDER BY id DESC LIMIT 1", (symbol,)
        ).fetchone()
        conn.close()
        if not row:
            return None, False
        return (row[0] if row[0] else None), bool(row[1])

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

    def save_prediction(self, symbol: str, signal: str, price: float,
                        ai_direction: str = None, ai_confidence: float = None,
                        council_confidence: float = None, strength: float = None,
                        executed: bool = False, bar_ts: str = None):
        """Bir sinyalin AI yon tahminini kaydeder (feedback dongusu icin).

        Ayni (symbol, bar_ts) icin tek kayit tutulur (tarama dongusu ayni bari
        tekrar degerlendirir); yeni kayit `executed=True` ise mevcut satir
        guncellenir.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        if bar_ts is not None:
            row = cursor.execute(
                "SELECT id, executed FROM predictions WHERE symbol=? AND bar_ts=? LIMIT 1",
                (symbol, bar_ts),
            ).fetchone()
            if row:
                if executed and not row[1]:
                    cursor.execute(
                        "UPDATE predictions SET executed=1 WHERE id=?", (row[0],)
                    )
                    conn.commit()
                conn.close()
                return
        cursor.execute('''
            INSERT INTO predictions (symbol, signal, ai_direction, ai_confidence,
                                     council_confidence, strength, price, bar_ts, executed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (symbol, signal, ai_direction, ai_confidence,
              council_confidence, strength, price, bar_ts, int(bool(executed))))
        conn.commit()
        conn.close()

    def list_pending_predictions(self, limit: int = 500):
        """Sonucu cozumlenmemis tahminleri en eskiden itibaren doner."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, symbol, signal, ai_direction, ai_confidence, price,
                   created_at, bar_ts, executed FROM predictions
            WHERE outcome = 'pending' ORDER BY id ASC LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [
            {"id": r[0], "symbol": r[1], "signal": r[2], "ai_direction": r[3],
             "ai_confidence": r[4], "price": r[5], "created_at": r[6], "bar_ts": r[7],
             "executed": r[8]}
            for r in rows
        ]

    def resolve_prediction(self, pred_id: int, outcome: str):
        """Bekleyen tahminin sonucunu yazar: hit | miss | na."""
        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            UPDATE predictions SET outcome = ?, resolved_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (outcome, pred_id))
        conn.commit()
        conn.close()

    def resolve_stale_predictions(self, days: int = 7):
        """Belirli yastan eski bekleyen tahminleri veri yoklugu nedeniyle 'na' yapar."""
        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            UPDATE predictions SET outcome = 'na', resolved_at = CURRENT_TIMESTAMP
            WHERE outcome = 'pending'
              AND created_at < datetime('now', ?)
        ''', (f"-{days} days",))
        conn.commit()
        conn.close()

    def ai_stats(self, limit_hours: int = 0) -> dict:
        """Tahmin istatistikleri: toplam, hit rate, yon dagilimi, ortalama guven."""
        conn = sqlite3.connect(self.db_path)
        if limit_hours > 0:
            rows = conn.execute('''
                SELECT outcome, signal, ai_direction, ai_confidence, executed
                FROM predictions
                WHERE outcome != 'pending'
                  AND created_at >= datetime('now', ?)
            ''', (f"-{limit_hours} hours",)).fetchall()
        else:
            rows = conn.execute('''
                SELECT outcome, signal, ai_direction, ai_confidence, executed
                FROM predictions
                WHERE outcome != 'pending'
            ''').fetchall()
        conn.close()
        total = len(rows)
        hits = sum(1 for r in rows if r[0] == "hit")
        resolved = sum(1 for r in rows if r[0] in ("hit", "miss"))
        by_direction: dict = {}
        for r in rows:
            if r[0] not in ("hit", "miss"):
                continue
            d = r[2] or "NA"
            b = by_direction.setdefault(d, {"total": 0, "hits": 0, "conf_sum": 0.0})
            b["total"] += 1
            b["conf_sum"] += float(r[3] or 0.0)
            if r[0] == "hit":
                b["hits"] += 1
        for d, b in by_direction.items():
            b["accuracy"] = round(b["hits"] / b["total"], 4) if b["total"] else 0.0
            b["avg_confidence"] = round(b["conf_sum"] / b["total"], 4) if b["total"] else 0.0
            b.pop("conf_sum", None)
        confs = [float(r[3] or 0.0) for r in rows]
        return {
            "total": total,
            "resolved": resolved,
            "pending": self._count_pending(),
            "hits": hits,
            "misses": resolved - hits,
            "accuracy": round(hits / resolved, 4) if resolved else 0.0,
            "executed": sum(1 for r in rows if r[4]),
            "avg_confidence": round(sum(confs) / len(confs), 4) if confs else 0.0,
            "by_direction": by_direction,
        }

    def _count_pending(self) -> int:
        conn = sqlite3.connect(self.db_path)
        n = conn.execute(
            "SELECT COUNT(*) FROM predictions WHERE outcome = 'pending'"
        ).fetchone()[0]
        conn.close()
        return int(n)

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
    def backup(self, backup_dir: str = None, keep: int = 14, verify: bool = True) -> dict:
        """SQLite online backup API ile tutarli bir kopya alir.

        `keep` adet en genc yedek saklanir; eskileri silinir. `verify=True`
        iken kopya `PRAGMA integrity_check` ile dogrulanir; bozuk kopya
        silinir ve hata dondurulur. Sonuc:
        `{"ok": True, "path": ..., "kept": N, "deleted": [...], "integrity": "ok", "size": N}`.
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
                integrity = "ok"
                if verify:
                    row = target.execute("PRAGMA integrity_check").fetchone()
                    integrity = row[0] if row else "unknown"
            finally:
                target.close()
        finally:
            source.close()

        if verify and integrity != "ok":
            try:
                dst.unlink()
            except OSError:
                pass
            return {"ok": False, "error": "integrity_check_failed",
                    "integrity": integrity}

        backups = sorted(base.glob(f"{src.stem}_backup_*.db"))
        deleted = []
        for old in backups[:-keep]:
            try:
                old.unlink()
                deleted.append(old.name)
            except OSError:
                pass
        return {"ok": True, "path": str(dst), "kept": min(len(backups), keep),
                "deleted": deleted, "integrity": integrity, "size": dst.stat().st_size}

    def restore(self, backup_path: str, keep_current: bool = True) -> dict:
        """Bir yedek dosyasini calisan veritabani uzerine geri yukler.

        Yedek once `PRAGMA integrity_check` ile dogrulanir; mevcut DB
        `pre_restore_<zaman>.db` olarak kopyalanir (`keep_current=True`) ve
        yedek asil dosyanin yerine kopyalanir. Sonuc:
        `{"ok": True, "path": ..., "previous": ..., "tables": N}`.
        """
        src = Path(backup_path)
        if not src.exists() or src.suffix != ".db":
            return {"ok": False, "error": "backup_not_found"}
        try:
            conn = sqlite3.connect(str(src))
            try:
                row = conn.execute("PRAGMA integrity_check").fetchone()
                integrity = row[0] if row else "unknown"
            finally:
                conn.close()
        except sqlite3.DatabaseError:
            return {"ok": False, "error": "integrity_check_failed",
                    "integrity": "invalid_database"}
        if integrity != "ok":
            return {"ok": False, "error": "integrity_check_failed",
                    "integrity": integrity}

        dst = Path(self.db_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        previous = None
        if keep_current and dst.exists():
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            previous = str(dst.with_name(f"{dst.stem}.pre_restore_{stamp}.db"))
            shutil.copy2(str(dst), previous)
        shutil.copy2(str(src), str(dst))

        conn = sqlite3.connect(str(dst))
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            restored_integrity = row[0] if row else "unknown"
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'")]
        finally:
            conn.close()
        if restored_integrity != "ok":
            return {"ok": False, "error": "restore_integrity_failed",
                    "integrity": restored_integrity, "previous": previous}
        return {"ok": True, "path": str(dst), "previous": previous,
                "integrity": restored_integrity, "tables": len(tables)}

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
