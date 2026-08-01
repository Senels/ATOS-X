import numpy as np
import pandas as pd
from config import Config

def calculate_scores(df_dict):
    results = {}
    for symbol, df in df_dict.items():
        if df is None or len(df) < 200:
            continue
        last = df.iloc[-1]
        score = _ensemble_score(last)
        results[symbol] = {
            "score": score,
            "signal": "LONG" if score >= Config.SCORE_ENTRY_THRESHOLD else ("SHORT" if score <= -Config.SCORE_ENTRY_THRESHOLD else "NONE"),
            "price": last["close"],
            "atr": last["atr"],
            "rsi": last["rsi"],
            "timestamp": last["timestamp"]
        }
    return results

def _ensemble_score(row):
    score = 0.0

    rsi = row["rsi"]
    if rsi < 30:
        score += 2.0
    elif rsi < 45:
        score += 1.0
    elif rsi > 70:
        score -= 2.0
    elif rsi > 55:
        score -= 1.0

    macd_hist = row["macd_hist"]
    if macd_hist > 0:
        score += 1.5
    else:
        score -= 1.5

    macd = row["macd"]
    macd_signal = row["macd_signal"]
    prev_hist = np.nan
    if macd > macd_signal:
        score += 0.5
    else:
        score -= 0.5

    price = row["close"]
    ema_fast = row["ema_fast"]
    ema_slow = row["ema_slow"]
    if price > ema_fast > ema_slow:
        score += 2.0
    elif price < ema_fast < ema_slow:
        score -= 2.0
    elif price > ema_slow:
        score += 0.5
    else:
        score -= 0.5

    vol_ratio = row["vol_ratio"]
    if vol_ratio > Config.VOL_MULTIPLIER:
        score += 1.5 * (1 if score > 0 else -1)
    elif vol_ratio < 0.5:
        score -= 1.0

    return round(score, 2)
