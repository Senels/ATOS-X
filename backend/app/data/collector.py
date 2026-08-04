"""Market Collector: Binance kline toplama ve backfill.

`legacy/data/futures_{interval}_data/` klasorune `loader.load_csv` ile uyumlu
CSV dosyalari yazar (sutunlar: timestamp[ms], open, high, low, close, volume).
"""
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from app.data.loader import _data_dir, is_stablecoin_symbol

_KLINES_LIMIT = 1000
_INTERVAL_MS = {
    "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
    "4h": 14_400_000, "6h": 21_600_000, "8h": 28_800_000, "12h": 43_200_000,
    "1d": 86_400_000,
}


def _period_ms(interval: str) -> int:
    return _INTERVAL_MS.get(str(interval).lower(), 14_400_000)


def _to_csv_frame(df: pd.DataFrame) -> pd.DataFrame:
    """get_klines DataFrame'ini loader uyumlu CSV formuna cevirir."""
    out = df[["open", "high", "low", "close", "volume"]].copy()
    out["timestamp"] = df.index.astype("int64") // 10**6
    return out[["timestamp", "open", "high", "low", "close", "volume"]]


async def collect(client, symbols: Iterable[str], interval: str = "4h",
                  bars: int = 400, data_dir: Optional[str] = None,
                  skip_stablecoins: bool = True) -> Dict[str, Any]:
    """Sembollerin guncel kline'larini ceker ve CSV arsivine yazar."""
    d = _data_dir(interval, data_dir)
    d.mkdir(parents=True, exist_ok=True)
    written: List[str] = []
    skipped: List[str] = []
    failed: List[str] = []
    for symbol in symbols:
        if skip_stablecoins and is_stablecoin_symbol(symbol):
            skipped.append(symbol)
            continue
        try:
            df = await client.get_klines(symbol, interval, int(bars))
            if df is None or df.empty or len(df) < 2:
                failed.append(symbol)
                continue
            path = d / f"{symbol}_{interval}.csv"
            _to_csv_frame(df).to_csv(path, index=False)
            written.append(symbol)
        except Exception:
            failed.append(symbol)
    return {"written": written, "skipped": skipped, "failed": failed,
            "interval": interval, "bars": int(bars), "path": str(d)}


async def backfill(client, symbols: Iterable[str], interval: str = "4h",
                   days: int = 30, data_dir: Optional[str] = None,
                   skip_stablecoins: bool = True) -> Dict[str, Any]:
    """Sembollerin gecmis verisini parcalar halinde cekip CSV arsivini tazeler.

    `get_klines(..., start_time=...)` destekleyen bir istemciyle eski veriye
    dogru yurur; tekrar eden bar'lar (drop_duplicates) ile ayiklanir.
    """
    d = _data_dir(interval, data_dir)
    d.mkdir(parents=True, exist_ok=True)
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - int(days) * 86_400_000
    period = _period_ms(interval)
    written: List[str] = []
    failed: List[str] = []
    for symbol in symbols:
        if skip_stablecoins and is_stablecoin_symbol(symbol):
            continue
        frames: List[pd.DataFrame] = []
        cursor = start_ms
        try:
            while cursor < now_ms:
                df = await client.get_klines(symbol, interval, _KLINES_LIMIT,
                                             start_time=cursor)
                if df is None or df.empty or len(df) < 2:
                    break
                frames.append(df)
                cursor = cursor + len(df) * period
            if not frames:
                failed.append(symbol)
                continue
            merged = pd.concat(frames)
            merged = merged[~merged.index.duplicated(keep="first")].sort_index()
            path = d / f"{symbol}_{interval}.csv"
            _to_csv_frame(merged).to_csv(path, index=False)
            written.append(symbol)
        except Exception:
            failed.append(symbol)
    return {"written": written, "failed": failed, "interval": interval,
            "days": int(days), "path": str(d)}
