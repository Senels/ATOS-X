"""Market Intelligence: rejim, volatilite ve likidite tespiti.

Kline DataFrame'inden (open/high/low/close/volume) deterministik rejim ve
volatilite sinyalleri uretir. Cikti risk boyutlandirma ve karar katmanlarina
(Decision Council) girdi olur. Tum hesap vektorize ve geriye bakma (lookahead)
icermez.
"""
from typing import Any, Dict

import pandas as pd

from app.strategy.tradebot_v23 import atr, ema


def atr_pct(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """ATR'nin fiyata orani (%)."""
    return atr(df, n) / df["close"] * 100.0


def volatility_regime(df: pd.DataFrame, lookback: int = 100, n: int = 14) -> Dict[str, Any]:
    """ATR%'nin son `lookback` bar icindeki yuzdelik dilimine gore siniflandirir.

    Regimler: LOW (<%30), NORMAL (%30-70), HIGH (%70-90), EXTREME (>=%90).
    """
    s = atr_pct(df, n)
    hist = s.dropna()
    if len(hist) < 2:
        return {"atr_pct": round(float(s.iloc[-1]), 4), "percentile": 50.0, "regime": "NORMAL"}
    cur = hist.iloc[-1]
    past = hist.iloc[-lookback - 1:-1] if len(hist) > lookback + 1 else hist.iloc[:-1]
    if len(past) < 2 or float(past.std()) < 1e-12:
        return {"atr_pct": round(float(cur), 4), "percentile": 50.0, "regime": "NORMAL"}
    pct = float((past <= cur).mean() * 100.0)
    if pct >= 90:
        label = "EXTREME"
    elif pct >= 70:
        label = "HIGH"
    elif pct >= 30:
        label = "NORMAL"
    else:
        label = "LOW"
    return {
        "atr_pct": round(float(cur), 4),
        "percentile": round(pct, 1),
        "regime": label,
    }


def trend_regime(df: pd.DataFrame, fast: int = 21, slow: int = 50, lookback: int = 100) -> Dict[str, Any]:
    """EMA hizasina ve egimine gore trend rejimi (UP / DOWN / RANGE)."""
    f = ema(df["close"], fast)
    s = ema(df["close"], slow)
    last = df["close"].iloc[-1]
    slope = (f.iloc[-1] - f.iloc[-lookback]) / f.iloc[-lookback] * 100.0 if len(f) > lookback else 0.0
    if f.iloc[-1] > s.iloc[-1] and slope > 0.0:
        label = "UP"
    elif f.iloc[-1] < s.iloc[-1] and slope < 0.0:
        label = "DOWN"
    else:
        label = "RANGE"
    return {
        "price": round(float(last), 4),
        "fast_ema": round(float(f.iloc[-1]), 4),
        "slow_ema": round(float(s.iloc[-1]), 4),
        "slope_pct": round(float(slope), 4),
        "regime": label,
    }


def liquidity(df: pd.DataFrame, vol_lookback: int = 100, avg: int = 20) -> Dict[str, Any]:
    """Likidite gostergesi: ortalama hacim ve son donem z-skoru."""
    v = df["volume"]
    ma = v.rolling(avg).mean().iloc[-1]
    hist = v.dropna().tail(vol_lookback)
    z = float((ma - hist.mean()) / hist.std()) if len(hist) > 2 and hist.std() > 0 else 0.0
    return {"vol_ma": round(float(ma), 4), "zscore": round(float(z), 2)}


def analyze(df: pd.DataFrame) -> Dict[str, Any]:
    """Tek DataFrame icin rejim + volatilite + likidite ozeti."""
    return {
        "volatility": volatility_regime(df),
        "trend": trend_regime(df),
        "liquidity": liquidity(df),
    }
