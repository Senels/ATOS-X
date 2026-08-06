"""Backtest AI kapisi simulasyonu.

Stratejinin urettigi sinyallere AI yon tahmini kapisini geriye donuk
uygular: her sinyal bari icin predictor ile yon+guven hesaplanir, AI yonu
sinyalle uyusmuyorsa veya guven `threshold` altindaysa o sinyal engellenir
(`ai_blocks` maskesi). Motor iki kez calistirilir — temiz ve AI filtreli —
sonuclar karsilastirilir. Ayrica engellenen vs gecen sinyallerin bar-bazli
isabet orani raporlanir (canli cozumleme semantigi, horizon bar sonrasi).

TensorFlow bu modulde gerekmez: predictor (model/scaler/features) enjekte
edilir.
"""
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from app.ai.evaluate import DIRECTIONS, resolve_outcome
from app.ai.features import build_features


def ai_blocked_mask(predictor: Any, df: pd.DataFrame, signal_arr,
                    threshold: float = 0.55) -> np.ndarray:
    """Her bar icin AI kapisi karari: True = sinyal engellenir.

    Sinyal dizisi (orders["signal"]) ile ayni hizada bool maskesi dondurur.
    AI yonu sinyal yonuyle uyusmuyorsa (HOLD dahil) veya guven esigin
    altindaysa engeller. Sinyal olmayan barlar asla engellenmez.
    """
    sig = np.asarray(signal_arr)
    n = len(df)
    mask = np.zeros(n, dtype=bool)
    if n < 2 or not np.any(sig != 0):
        return mask
    feats = build_features(df)
    if feats.empty:
        return mask
    X = feats[predictor.features].fillna(0.0).replace([np.inf, -np.inf], 0.0)
    X = np.asarray(predictor.scaler.transform(X.to_numpy(dtype=np.float32)),
                   dtype=np.float32)
    probs = np.asarray(predictor.model.predict(X, batch_size=1024, verbose=0))
    idx = probs.argmax(axis=1)
    dirs = np.array(DIRECTIONS)[idx]
    conf = probs[np.arange(n), idx]
    for i in range(n):
        if sig[i] == 0:
            continue
        d = dirs[i]
        if d == "HOLD" or float(conf[i]) < threshold:
            mask[i] = True
            continue
        if (d == "BUY") != (sig[i] > 0):
            mask[i] = True
    return mask


def signal_accuracy(df: pd.DataFrame, signal_arr, mask: np.ndarray,
                    horizon: int = 12) -> Dict[str, Any]:
    """Sinyal barlarinin bar-bazli isabet istatistikleri.

    Engellenen vs gecen sinyallerin hit/miss dagilimi (canli cozumleme
    kurallari: BUY -> +horizon bar yukselis hit; SELL -> dusus hit).
    Son `horizon` bar cozulemez, atlanir.
    """
    sig = np.asarray(signal_arr)
    mask = np.asarray(mask, dtype=bool)
    close = df["close"].to_numpy(dtype=np.float64)
    out = {"signals": 0, "blocked": 0, "passed": 0,
           "blocked_hits": 0, "blocked_misses": 0,
           "passed_hits": 0, "passed_misses": 0,
           "blocked_accuracy": 0.0, "passed_accuracy": 0.0}
    n = len(df)
    for i in range(n - horizon):
        if sig[i] == 0:
            continue
        out["signals"] += 1
        d = "BUY" if sig[i] > 0 else "SELL"
        outcome = resolve_outcome(d, float(close[i]), float(close[i + horizon]))
        if outcome not in ("hit", "miss"):
            continue
        if mask[i]:
            out["blocked"] += 1
            if outcome == "hit":
                out["blocked_hits"] += 1
            else:
                out["blocked_misses"] += 1
        else:
            out["passed"] += 1
            if outcome == "hit":
                out["passed_hits"] += 1
            else:
                out["passed_misses"] += 1
    b = out["blocked_hits"] + out["blocked_misses"]
    p = out["passed_hits"] + out["passed_misses"]
    out["blocked_accuracy"] = out["blocked_hits"] / b if b else 0.0
    out["passed_accuracy"] = out["passed_hits"] / p if p else 0.0
    return out


def simulate(predictor: Any, df: pd.DataFrame, orders: pd.DataFrame,
             interval: str = "4h", threshold: float = 0.55,
             engine_cls: Any = None, engine_kwargs: Optional[dict] = None,
             horizon: int = 12) -> Dict[str, Any]:
    """Motoru temiz ve AI filtreli calistirip sonuclari karsilastirir.

    `engine_cls` (varsayilan BacktestEngine) ve `engine_kwargs` enjekte
    edilebilir (testler icin). Donus: baseline/with_ai metrikleri +
    sinyal istatistikleri.
    """
    if engine_cls is None:
        from app.backtest.engine import BacktestEngine
        engine_cls = BacktestEngine
    engine_kwargs = engine_kwargs or {}
    sig = orders["signal"].to_numpy(int)
    base = engine_cls(**engine_kwargs).run(df, orders, interval)
    mask = ai_blocked_mask(predictor, df, sig, threshold)
    ai_res = engine_cls(**engine_kwargs).run(df, orders, interval, ai_blocks=mask)
    return {
        "baseline": base,
        "with_ai": ai_res,
        "ai_blocks": mask,
        "signal_stats": signal_accuracy(df, sig, mask, horizon),
    }


def summarize_scan(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Cok sembollu tarama sonuclarini toplar (scriptler icin).

    Her satir: `signal_stats` (sinyal isabet) + `base_trades`/`ai_trades`/
    `base_wins`/`ai_wins`/`base_net`/`ai_net` (motor metrikleri).
    """
    agg = {
        "symbols": len(rows),
        "signals": 0, "blocked": 0, "passed": 0,
        "blocked_hits": 0, "blocked_misses": 0,
        "passed_hits": 0, "passed_misses": 0,
        "base_trades": 0, "ai_trades": 0,
        "base_wins": 0, "ai_wins": 0,
        "base_net": 0.0, "ai_net": 0.0,
    }
    for r in rows:
        ss = r.get("signal_stats") or {}
        agg["signals"] += int(ss.get("signals", 0))
        agg["blocked"] += int(ss.get("blocked", 0))
        agg["passed"] += int(ss.get("passed", 0))
        agg["blocked_hits"] += int(ss.get("blocked_hits", 0))
        agg["blocked_misses"] += int(ss.get("blocked_misses", 0))
        agg["passed_hits"] += int(ss.get("passed_hits", 0))
        agg["passed_misses"] += int(ss.get("passed_misses", 0))
        agg["base_trades"] += int(r.get("base_trades", 0))
        agg["ai_trades"] += int(r.get("ai_trades", 0))
        agg["base_wins"] += int(r.get("base_wins", 0))
        agg["ai_wins"] += int(r.get("ai_wins", 0))
        agg["base_net"] += float(r.get("base_net", 0.0) or 0.0)
        agg["ai_net"] += float(r.get("ai_net", 0.0) or 0.0)
    bt = agg["base_trades"]
    at = agg["ai_trades"]
    agg["base_win_rate"] = agg["base_wins"] / bt * 100 if bt else 0.0
    agg["ai_win_rate"] = agg["ai_wins"] / at * 100 if at else 0.0
    agg["blocked_accuracy"] = agg["blocked_hits"] / (agg["blocked_hits"] + agg["blocked_misses"]) \
        if (agg["blocked_hits"] + agg["blocked_misses"]) else 0.0
    agg["passed_accuracy"] = agg["passed_hits"] / (agg["passed_hits"] + agg["passed_misses"]) \
        if (agg["passed_hits"] + agg["passed_misses"]) else 0.0
    return agg
