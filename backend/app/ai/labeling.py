"""AI etiketleme: ileriye donuk getiriye gore sinif etiketleri.

`make_labels`, t barindaki yonu t+horizon kapanisi ile siniflar:
- ileri getiri > esik  -> +1 (LONG / BUY)
- ileri getiri < -esik -> -1 (SHORT / SELL)
- aksi                  0  (HOLD)

Esik, degisken volatiliteye gore barin kendi ATR%'si ile belirlenir;
sabit yuzde esigi cesitli coinler arasinda ayni sinif dengesini vermez.
"""
from typing import List

import numpy as np
import pandas as pd

from app.strategy.tradebot_v23 import atr

_CLASSES: List[str] = ["SELL", "HOLD", "BUY"]


def make_labels(df: pd.DataFrame, horizon: int = 12, atr_mult: float = 1.0) -> pd.Series:
    """t+horizon getirisine gore -1/0/+1 etiket serisi (ilk/donemlerde NaN)."""
    if df is None or len(df) < horizon + 1:
        return pd.Series(dtype=float)
    close = df["close"]
    fwd = close.shift(-horizon) / close - 1.0
    thr = atr(df, 14) / close * atr_mult
    labels = np.where(
        fwd > thr, 1.0,
        np.where(fwd < -thr, -1.0, 0.0),
    )
    return pd.Series(labels, index=df.index).replace(np.nan, 0.0)


def class_name(label: float) -> str:
    return _CLASSES[int(label) + 1]


def class_balance(y: np.ndarray) -> dict:
    counts = np.bincount((y + 1).astype(int), minlength=3)
    return {"SELL": int(counts[0]), "HOLD": int(counts[1]), "BUY": int(counts[2])}
