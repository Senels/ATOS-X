"""Decision Council: coklu sinyal oylamasi ve aciklanabilir karar.

v23 sinyali (birincil tetik), trend rejimi, momentum ve volatilite kapisi
bileserek BUY/SELL/HOLD karari + guven (confidence) uretir. `votes` listesi
kararin hangi kaynaklardan geldigini aciklar.

Oylama agirliklari: v23=1.0, trend=0.4, momentum=0.3 (net max 1.7).
- net >= 0.8 -> BUY, net <= -0.8 -> SELL, aksi HOLD.
- HIGH volatilite: her iki tarafa -0.3 ceza; EXTREME: hard veto (HOLD).

`primary_signal` verilirse (TTP modu) v23 hesaplanmaz; birincil oy o
sinyalin yonundedir (`source` adiyla oylar, varsayilan agirlik 1.0).
Boylece TTP sinyalleri council tarafindan v23 zorunlulugu olmadan
trend+momentum+volatilite ile degerlendirilir.
"""
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.strategy import settings as strat_settings
from app.strategy.coin_intel import coin_score
from app.strategy.market_intel import trend_regime, volatility_regime
from app.strategy.tradebot_v23 import TradeBotV23
from app.strategy.ttp import TtpTsl
from loguru import logger

_TREND_MAP = {"UP": "BUY", "DOWN": "SELL", "RANGE": None}
_WEIGHTS: Dict[str, float] = {"v23": 1.0, "trend": 0.4, "momentum": 0.3}
_AGREE_THRESHOLD = 0.8
_MAX_NET = 1.7
_VOL_PENALTY_HIGH = -0.3


def _vote(primary: str, trend: str, momentum_pct: float,
          volatility: str, source: str = "v23") -> Tuple[str, float, List[Dict[str, Any]]]:
    votes: List[Dict[str, Any]] = []
    buy = sell = 0.0

    if primary in ("BUY", "SELL"):
        votes.append({"source": source, "signal": primary, "weight": _WEIGHTS["v23"]})
        if primary == "BUY":
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


def decide(df: pd.DataFrame, settings: Optional[Dict[str, Any]] = None,
           primary_signal: Optional[Dict[str, Any]] = None,
           **kwargs) -> Dict[str, Any]:
    """DataFrame'den karar: birincil sinyal + trend + momentum + volatilite oylamasi.

    `primary_signal` verilirse (ornek `{"signal": "BUY", "source": "ttp"}`)
    v23 hesaplanmaz; verilen sinyal birincil oy olur. Verilmezse `settings`
    aktif stratejisine gore v23 (TradeBotV23) veya ttp (TtpTsl) birincil
    tetiktir — boylece endpoint/dashboard kararlari gercek kapinin aynisi olur.

    `symbol` keyword argumani verilirse ve `mtf_enabled=True` ise cok zaman
    dilimi oyu da dahil edilir.
    """
    cfg = settings if settings is not None else strat_settings.get_settings()
    if primary_signal:
        primary = primary_signal.get("signal", "HOLD")
        source = primary_signal.get("source", "strategy")
        v23 = None
        price = primary_signal.get("price")
        sl = primary_signal.get("sl")
        tp = primary_signal.get("tp")
    elif cfg.get("active_strategy") == "ttp":
        res = TtpTsl(cfg).analyze_full(df)
        orders = res.get("orders") if isinstance(res, dict) else None
        row = orders.iloc[-1] if orders is not None and len(orders) else {}
        sig_int = int(row.get("signal", 0))
        primary = "BUY" if sig_int == 1 else ("SELL" if sig_int == -1 else "HOLD")
        source = "ttp"
        v23 = None
        price = None
        sl = tp = None
        if not isinstance(row, dict):
            for key in ("sl", "tp"):
                val = row.get(key)
                if val is not None and not pd.isna(val):
                    if key == "sl":
                        sl = float(val)
                    else:
                        tp = float(val)
        price = float(df["close"].iloc[-1]) if len(df) else None
    else:
        bot = TradeBotV23(cfg)
        sig = bot.generate_signal(df)
        primary = sig.get("signal", "HOLD")
        source = "v23"
        v23 = primary
        price = sig.get("price")
        sl = sig.get("sl")
        tp = sig.get("tp")
    trend = trend_regime(df)["regime"]
    sc = coin_score(df)
    mom = sc.get("momentum_pct", 0.0)
    vol = volatility_regime(df)["regime"]

    verdict, confidence, votes = _vote(primary, trend, mom, vol, source=source)

    if verdict != "HOLD":
        agreeing = [v["source"] for v in votes if v["signal"] == verdict]
        reason = "+".join(agreeing) + " " + verdict
    elif vol == "EXTREME":
        reason = "Volatilite kapisi (EXTREME ATR)"
    else:
        reason = "Yetersiz uzlasma"

    # MTF oyu (mtf_enabled aktifse ve CSV verisi varsa)
    mtf_result = None
    if cfg.get("mtf_enabled"):
        try:
            from app.strategy.multi_tf import get_mtf_context, mtf_vote
            intervals = cfg.get("mtf_intervals", ["4h", "1h"])
            weights = cfg.get("mtf_weights")
            # symbol bilgisi yoksa atliyoruz; caller tarafindan saglanmali
            symbol = kwargs.get("symbol") if kwargs else None
            if symbol:
                dfs = get_mtf_context(symbol, intervals)
                if dfs:
                    mtf_result = mtf_vote(dfs, cfg, weights)
                    mtf_verdict = mtf_result.get("verdict", "HOLD")
                    mtf_conf = float(mtf_result.get("confidence", 0.0))
                    if mtf_verdict in ("BUY", "SELL"):
                        votes.append({
                            "source": "mtf",
                            "signal": mtf_verdict,
                            "weight": 0.5 * mtf_conf,
                        })
        except Exception as e:
            logger.debug(f"multi-timeframe verdict hesaplanamadi: {e}")

    return {
        "verdict": verdict,
        "confidence": confidence,
        "votes": votes,
        "reason": reason,
        "components": {"v23": v23, "strategy": source, "trend": trend,
                       "momentum_pct": mom, "volatility": vol},
        "price": price,
        "sl": sl,
        "tp": tp,
        "mtf": mtf_result,
    }
