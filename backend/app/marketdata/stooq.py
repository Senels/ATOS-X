"""Stooq makro veri kaynagi: DXY, VIX, SPX, altin, EUR/USD.

Stooq ucretsiz CSV endpoint'inden gunluk veri ceker (`dx.f`, `^vix`, `^spx`,
`gold.us`, `eurusd`). TTL cache (6 saat) ile tamponlanir; ag hatasinda eski
deger veya None doner — ajanlar eksik veride cekimser kalir.
"""
import io
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import pandas as pd
import urllib3

from app.marketdata.cache import TTLCache

http = urllib3.PoolManager()
_cache = TTLCache()

MACRO_SYMBOLS: Dict[str, str] = {
    "dxy": "dx.f",
    "vix": "^vix",
    "spx": "^spx",
    "gold": "gold.us",
    "eurusd": "eurusd",
}

_CACHE_TTL = 6 * 3600.0


def _fetch_stooq_csv(stooq_symbol: str, days: int = 120) -> Optional[pd.DataFrame]:
    d1 = (datetime.utcnow() - timedelta(days=days)).strftime("%Y%m%d")
    d2 = datetime.utcnow().strftime("%Y%m%d")
    url = f"https://stooq.com/q/d/l/?s={stooq_symbol}&i=d&d1={d1}&d2={d2}"
    try:
        resp = http.request("GET", url, timeout=15)
        if resp.status != 200 or not resp.data:
            return None
        text = resp.data.decode("utf-8", errors="replace")
        if "No data" in text or len(text) < 20:
            return None
        df = pd.read_csv(io.StringIO(text))
        if df.empty or "Date" not in df.columns:
            return None
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date")
        for col in ("Open", "High", "Low", "Close", "Volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df[["Close"]].dropna() if "Close" in df.columns else None
    except Exception:
        return None


def get_macro(name: str) -> Optional[Dict[str, Any]]:
    """Tek makro serinin son durumunu doner: son kapanis + N gun degisimi."""
    if name not in MACRO_SYMBOLS:
        return None
    return _cache.get_or_compute(name, _CACHE_TTL,
                                 lambda: _compute_macro(name, MACRO_SYMBOLS[name]))


def _compute_macro(name: str, stooq_symbol: str) -> Optional[Dict[str, Any]]:
    df = _fetch_stooq_csv(stooq_symbol)
    if df is None or len(df) < 2:
        return None
    close = df["Close"]
    last = float(close.iloc[-1])
    out = {"symbol": name, "price": round(last, 4), "date": str(close.index[-1].date())}
    for n in (1, 5, 20):
        if len(close) > n:
            prev = float(close.iloc[-1 - n])
            out[f"chg{n}d_pct"] = round((last / prev - 1) * 100.0, 3)
    return out


def refresh_all() -> Dict[str, Any]:
    """Tum makro sembollerini tazeler (ayar/dashboard icin)."""
    for name in MACRO_SYMBOLS:
        _cache.remove(name)
        get_macro(name)
    return {name: get_macro(name) for name in MACRO_SYMBOLS}


def macro_summary() -> Dict[str, Any]:
    return {name: get_macro(name) for name in MACRO_SYMBOLS}
