"""vectorbt entegrasyonuyla ATOS X portföy analizi.

GitHub repo: https://github.com/polak792/vectorbt (MIT lisans)
- Backtest, risk analizi, performans metriklerini hesaplar.
- SQLite'den (sqlite-mcp read_query) sinyal verilerini okur.
- AI modelleriyle entegre: her sinyal confidence-weighted backtest.

NOT: canlı DB okuma için sqlite-mcp read_query kullanılır; yazma olmaz.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import pandas as pd

try:
    import vectorbt as vbt
except ImportError:
    vbt = None

try:
    import sqlite3
except ImportError:
    sqlite3 = None

DB_PATH = os.getenv("DB_PATH", "atos.db")


def load_signals_last_30d(db_path: str = DB_PATH) -> pd.DataFrame:
    """SQLite'den son 30 günlük sinyalleri çeker (salt-okunur)."""
    if sqlite3 is None:
        return pd.DataFrame()
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            """
            SELECT symbol, created_at as ts,
                   direction as signal,
                   confidence,
                   price
            FROM signals
            WHERE created_at > datetime('now', '-30 days')
            ORDER BY created_at ASC
            """,
            conn,
        )
        return df
    except sqlite3.Error:
        return pd.DataFrame()
    finally:
        conn.close()


def _build_price_from_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Sinyal tablosundan OHLCV + entry/exit maskesi kurar."""
    if df.empty:
        return pd.DataFrame()

    df = df.copy()
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.sort_values("ts").reset_index(drop=True)
    df["close"] = df["price"].astype(float)
    df["entries"] = df["signal"] == "BUY"
    df["exits"] = df["signal"] == "SELL"
    return df


def backtest_signals(
    db_path: str = DB_PATH,
    fee: float = 0.001,
) -> Optional[Any]:
    """ATOS X sinyallerine göre vectorbt Portfolio nesnesi oluşturur.

    Returns:
        vbt.Portfolio | None → Vektör backtest sonucu, yoksa None.
    """
    if vbt is None:
        return None

    raw = load_signals_last_30d(db_path)
    sig = _build_price_from_signals(raw)
    if sig.empty or not (sig["entries"].any() and sig["exits"].any()):
        return None

    pf = vbt.Portfolio.from_signals(
        close=sig["close"],
        entries=sig["entries"],
        exits=sig["exits"],
        qty=sig.get("confidence", 0.5) * 0.01,
        fees=fee,
        cash_sharing=True,
        direction="both",
    )
    return pf


def portfolio_stats(pf: Any) -> dict[str, Any]:
    """Portfolio'den temel metrikleri JSON-serializable çıkarır."""
    if pf is None:
        return {"error": "no_portfolio"}

    stats = pf.stats()
    try:
        total_ret = float(pf.total_return().iloc[-1])
    except Exception:
        total_ret = 0.0
    try:
        sharpe = float(pf.sharpe_ratio().iloc[-1])
    except Exception:
        sharpe = 0.0
    try:
        max_dd = float(pf.max_drawdown().iloc[-1])
    except Exception:
        max_dd = 0.0

    return {
        "total_return": total_ret,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "n_trades": int(len(pf.trades)),
        "winrate": (
            float(stats.loc["Win Rate [%]"].iloc[0])
            if "Win Rate [%]" in stats.index
            else 0.0
        ),
    }
