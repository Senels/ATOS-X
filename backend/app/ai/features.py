"""AI ozellik motoru: 4h OHLCV'den model giris vektorleri.

Her tamamlanmis bar icin ileriye donuk sizinti olmadan (yalnizca t ve oncesi
bilgisiyle) sayisal ozellikler uretilir. `build_features` satir bazli
(her bar bir ornek) ~24 ozellik dondurur; TensorFlow katmanli ag bunu
yond tahmini icin kullanir.
"""
from typing import List, Tuple

import numpy as np
import pandas as pd

from app.strategy.tradebot_v23 import (
    atr,
    chandelier_exit,
    ema,
    macd,
    range_filter,
    rqk,
    rsi,
    stochastic,
)

FEATURE_NAMES: List[str] = [
    "r1", "r5", "r10", "r20",
    "atr_pct", "rsi", "macd_hist_pct",
    "ema9_r", "ema21_r", "ema55_r",
    "rqk_spread", "rf_state",
    "stoch_kd", "chand_dist",
    "vol_z", "ret_std20", "dn_pos20", "hl_range", "mom5",
    "vol_regime", "ema100_r", "vol_mom", "bb_pos",
]


def _atr_pct(df: pd.DataFrame) -> pd.Series:
    return atr(df, 14) / df["close"]


def _rqk_spread_state(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    up, down = range_filter(df, 3, 2.5)
    return (df["close"] - rqk(df["close"], 5, 8)) / df["close"], up.astype(float)


def build_features(df: pd.DataFrame, min_rows: int = 60) -> pd.DataFrame:
    """Her bar icin ozellik matrisi. `min_rows` alti veride bos matris doner."""
    if df is None or len(df) < min_rows:
        return pd.DataFrame(index=df.index if df is not None else None)
    close = df["close"]
    out = pd.DataFrame(index=df.index)
    out["r1"] = close.pct_change(1)
    out["r5"] = close.pct_change(5)
    out["r10"] = close.pct_change(10)
    out["r20"] = close.pct_change(20)
    out["atr_pct"] = _atr_pct(df)
    out["rsi"] = rsi(close, 14) / 100.0
    _, _, hist = macd(close)
    out["macd_hist_pct"] = hist / close
    e9, e21, e55 = ema(close, 9), ema(close, 21), ema(close, 55)
    out["ema9_r"] = e9 / close
    out["ema21_r"] = e21 / close
    out["ema55_r"] = e55 / close
    spread, state = _rqk_spread_state(df)
    out["rqk_spread"] = spread
    out["rf_state"] = state
    k, d = stochastic(df, 14, 3)
    out["stoch_kd"] = (k - d) / 100.0
    long_exit, _ = chandelier_exit(df)
    out["chand_dist"] = (close - long_exit) / close
    logv = np.log1p(df["volume"])
    vol20 = logv.rolling(20).mean()
    vol_std = logv.rolling(20).std()
    out["vol_z"] = (logv - vol20) / (vol_std + 1e-9)
    out["ret_std20"] = close.pct_change().rolling(20).std()
    roll_lo = close.rolling(20).min()
    roll_hi = close.rolling(20).max()
    out["dn_pos20"] = (close - roll_lo) / (roll_hi - roll_lo + 1e-9)
    out["hl_range"] = (df["high"] - df["low"]) / close
    out["mom5"] = close.pct_change(5) - close.pct_change(1)
    ap = out["atr_pct"]
    out["vol_regime"] = (ap - ap.rolling(20).mean()) / (ap.rolling(20).std() + 1e-9)
    out["ema100_r"] = ema(close, 100) / close
    logv5 = logv.rolling(5).mean()
    out["vol_mom"] = logv5 - vol20
    sma20 = close.rolling(20).mean()
    out["bb_pos"] = (close - sma20) / (2 * close.rolling(20).std() + 1e-9)
    return out[FEATURE_NAMES]


def last_feature_vector(df: pd.DataFrame) -> List[float]:
    """Son tamamlanmis barin ozellik vektorunu dondurur (bos olabilir)."""
    feats = build_features(df)
    if feats.empty:
        return []
    return [float(x) for x in feats.iloc[-1].tolist()]
