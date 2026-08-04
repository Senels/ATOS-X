"""Coin Intelligence: canli momentum/score motoru.

Sembol secimi icin kline DataFrame'inden bilesik skor uretir; momentum,
trend rejimi ve volatilite cezasini birlestirir. Cikti dashboard skor
goruntulemesine ve ilerleyen batch'lerde otomatik sembol secimine girdi olur.
"""
from typing import Any, Dict, Tuple

import pandas as pd

from app.strategy.market_intel import trend_regime, volatility_regime

_MOM_WEIGHTS: Tuple[float, float, float, float] = (0.4, 0.3, 0.2, 0.1)  # r20, r10, r5, r1
_TREND_SCORE = {"UP": 1.0, "RANGE": 0.0, "DOWN": -1.0}
_VOL_PENALTY = {"LOW": 0.0, "NORMAL": 0.0, "HIGH": -0.5, "EXTREME": -1.0}


def coin_score(df: pd.DataFrame, mom_weights: Tuple[float, float, float, float] = _MOM_WEIGHTS) -> Dict[str, Any]:
    """Bir sembol icin bilesik skor: momentum + trend + volatilite cezasi."""
    close = df["close"]
    if len(close) < 25:
        return {"score": 0.0, "reason": "yetersiz veri"}
    last = close.iloc[-1]
    r1 = (last / close.iloc[-2] - 1) * 100.0
    r5 = (last / close.iloc[-6] - 1) * 100.0
    r10 = (last / close.iloc[-11] - 1) * 100.0
    r20 = (last / close.iloc[-21] - 1) * 100.0
    mom = mom_weights[0] * r20 + mom_weights[1] * r10 + mom_weights[2] * r5 + mom_weights[3] * r1
    trend = trend_regime(df)
    t_score = _TREND_SCORE[trend["regime"]]
    vol = volatility_regime(df)
    v_pen = _VOL_PENALTY[vol["regime"]]
    score = round(float(mom + 2.0 * t_score + v_pen), 4)
    return {
        "score": score,
        "momentum_pct": round(float(mom), 4),
        "r1_pct": round(float(r1), 4),
        "r5_pct": round(float(r5), 4),
        "r10_pct": round(float(r10), 4),
        "r20_pct": round(float(r20), 4),
        "trend": trend["regime"],
        "trend_score": t_score,
        "atr_pct": vol["atr_pct"],
        "volatility": vol["regime"],
        "vol_penalty": v_pen,
    }
