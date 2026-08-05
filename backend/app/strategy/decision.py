"""Decision Council: coklu sinyal oylamasi ve aciklanabilir karar.

v23 sinyali (birincil tetik), trend rejimi, momentum ve volatilite kapisi
bileserek BUY/SELL/HOLD karari + guven (confidence) uretir. `votes` listesi
kararin hangi kaynaklardan geldigini aciklar.

Oylama agirliklari: v23=1.0, trend=0.4, momentum=0.3 (net max 1.7).
- net >= 0.8 -> BUY, net <= -0.8 -> SELL, aksi HOLD.
- HIGH volatilite: her iki tarafa -0.3 ceza; EXTREME: hard veto (HOLD).
"""
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.strategy import settings as strat_settings
from app.strategy.coin_intel import coin_score
from app.strategy.market_intel import trend_regime, volatility_regime
from app.strategy.tradebot_v23 import TradeBotV23

_TREND_MAP = {"UP": "BUY", "DOWN": "SELL", "RANGE": None}
_WEIGHTS: Dict[str, float] = {"v23": 1.0, "trend": 0.4, "momentum": 0.3}
_AGREE_THRESHOLD = 0.8
_MAX_NET = 1.7
_VOL_PENALTY_HIGH = -0.3


def _vote(v23: str, trend: str, momentum_pct: float,
          volatility: str) -> Tuple[str, float, List[Dict[str, Any]]]:
    votes: List[Dict[str, Any]] = []
    buy = sell = 0.0

    if v23 in ("BUY", "SELL"):
        votes.append({"source": "v23", "signal": v23, "weight": _WEIGHTS["v23"]})
        if v23 == "BUY":
            buy += _WEIGHTS["v23"]
        else:
            sell += _WEIGHTS["v23"]

    t = _TREND_MAP.get(trend)
    if t:
        votes.append({"source": "trend", "signal": t, "weight": _WEIGHTS["trend"]})
        if t == "BUY":
            buy += _WEIGHTS["trend"]
        else:
            sell += _WEIGHTS["trend"]

    m = "BUY" if momentum_pct > 0.1 else ("SELL" if momentum_pct < -0.1 else None)
    if m:
        votes.append({"source": "momentum", "signal": m, "weight": _WEIGHTS["momentum"]})
        if m == "BUY":
            buy += _WEIGHTS["momentum"]
        else:
            sell += _WEIGHTS["momentum"]

    if volatility == "EXTREME":
        votes.append({"source": "volatility", "signal": "HOLD", "weight": -1.0})
        return "HOLD", 0.0, votes

    if volatility == "HIGH":
        votes.append({"source": "volatility", "signal": "HOLD", "weight": _VOL_PENALTY_HIGH})
        buy = max(0.0, buy + _VOL_PENALTY_HIGH)
        sell = max(0.0, sell + _VOL_PENALTY_HIGH)

    net = buy - sell
    if net >= _AGREE_THRESHOLD:
        verdict = "BUY"
    elif net <= -_AGREE_THRESHOLD:
        verdict = "SELL"
    else:
        verdict = "HOLD"
    confidence = round(min(abs(net) / _MAX_NET, 1.0), 2)
    return verdict, confidence, votes


def decide(df: pd.DataFrame, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """DataFrame'den karar: v23 + trend + momentum + volatilite oylamasi."""
    cfg = settings if settings is not None else strat_settings.get_settings()
    bot = TradeBotV23(cfg)
    sig = bot.generate_signal(df)
    v23 = sig.get("signal", "HOLD")
    trend = trend_regime(df)["regime"]
    sc = coin_score(df)
    mom = sc.get("momentum_pct", 0.0)
    vol = volatility_regime(df)["regime"]

    verdict, confidence, votes = _vote(v23, trend, mom, vol)

    if verdict != "HOLD":
        agreeing = [v["source"] for v in votes if v["signal"] == verdict]
        reason = "+".join(agreeing) + " " + verdict
    elif vol == "EXTREME":
        reason = "Volatilite kapisi (EXTREME ATR)"
    else:
        reason = "Yetersiz uzlasma"

    return {
        "verdict": verdict,
        "confidence": confidence,
        "votes": votes,
        "reason": reason,
        "components": {"v23": v23, "trend": trend,
                       "momentum_pct": mom, "volatility": vol},
        "price": sig.get("price"),
        "sl": sig.get("sl"),
        "tp": sig.get("tp"),
    }
