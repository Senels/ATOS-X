"""Arsiv tabanli AI model dogruluk degerlendirmesi.

Canli `_resolve_pending_predictions` ile birebir ayni semantik kullanilir:
BUY -> ileri `horizon` bar sonraki kapanis yukseldiyse hit; SELL -> dustuyse
hit; HOLD tahminleri degerlendirmeye katilmaz. Bu sayede canli feedback
dongusunun neye yakinsayacagi arsiv verisiyle hizlica olculebilir.

TensorFlow gerektirmez: `model`/`scaler` nesneleri enjekte edilir (testlerde
sahte nesneler kullanilabilir).
"""
from typing import Any, List

import numpy as np
import pandas as pd

from app.ai.features import build_features

DIRECTIONS = ["SELL", "HOLD", "BUY"]


def resolve_outcome(direction: str, p0: float, p1: float) -> str:
    """Canli cozumleme kurali: BUY p1>p0, SELL p1<p0, diger -> na."""
    if direction == "BUY":
        return "hit" if p1 > p0 else "miss"
    if direction == "SELL":
        return "hit" if p1 < p0 else "miss"
    return "na"


def evaluate_model(model: Any, scaler: Any, features: List[str],
                   symbols: dict, horizon: int = 12) -> pd.DataFrame:
    """Her sembol (sembol adi -> OHLCV DataFrame) icin bar-bazli tahmin + cozumleme.

    `model.predict(X, batch_size=..., verbose=0)` (softmax prob matrisi) ve
    `scaler.transform(X)` sozlesmesi kullanilir. Sonuc sutunlari: symbol,
    direction, confidence, outcome.
    """
    rows = []
    for symbol, df in symbols.items():
        if df is None or len(df) < horizon + 2:
            continue
        feats = build_features(df)
        if feats.empty:
            continue
        X = feats[features].fillna(0.0).replace([np.inf, -np.inf], 0.0)
        X = np.asarray(scaler.transform(X.to_numpy(dtype=np.float32)),
                       dtype=np.float32)
        probs = np.asarray(model.predict(X, batch_size=1024, verbose=0))
        idxs = probs.argmax(axis=1)
        close = df["close"].to_numpy(dtype=np.float64)
        for i in range(len(close) - horizon):
            d = DIRECTIONS[int(idxs[i])]
            if d == "HOLD":
                continue
            rows.append({
                "symbol": symbol,
                "direction": d,
                "confidence": float(probs[i, int(idxs[i])]),
                "outcome": resolve_outcome(d, float(close[i]),
                                           float(close[i + horizon])),
            })
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame, recent_bars: int = 200) -> dict:
    """Ozet istatistikler: genel/yon bazli/guncel (sembol basi son N bar)."""
    if df is None or df.empty:
        return {"samples": 0, "accuracy": 0.0, "hits": 0, "misses": 0,
                "by_direction": {}, "avg_conf_hit": 0.0, "avg_conf_miss": 0.0,
                "recent_accuracy": 0.0, "recent_samples": 0}
    hit = (df["outcome"] == "hit")
    hit_conf = df.loc[hit, "confidence"]
    miss_conf = df.loc[~hit, "confidence"]
    out = {
        "samples": int(len(df)),
        "accuracy": float(hit.mean()),
        "hits": int(hit.sum()),
        "misses": int((~hit).sum()),
        "by_direction": {},
        "avg_conf_hit": float(hit_conf.mean()) if len(hit_conf) else 0.0,
        "avg_conf_miss": float(miss_conf.mean()) if len(miss_conf) else 0.0,
    }
    for d in ["BUY", "SELL"]:
        sub = df[df["direction"] == d]
        if len(sub):
            out["by_direction"][d] = {
                "samples": int(len(sub)),
                "accuracy": float((sub["outcome"] == "hit").mean()),
                "avg_confidence": float(sub["confidence"].mean()),
            }
    recent = df.groupby("symbol", sort=False).tail(recent_bars)
    out["recent_accuracy"] = float((recent["outcome"] == "hit").mean())
    out["recent_samples"] = int(len(recent))
    return out
