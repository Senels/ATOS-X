"""Korelasyon motoru: semboller arasi Pearson korelasyon matrisi.

`klines_map` paylasilan kline deposundan top-N sembolun kapanislarini ortak
zamansal pencereye dizer, log-getiri korelasyonunu hesaplar. Sonuc:
- matrix: {sym: {other: corr}} (yuvarlanmis, JSON uyumlu)
- market_avg: sembolun piyasa ortalamasiyla korelasyonu (beta benzeri)
- avg_level: tum matris ortalamasi (portfoy cesitlendirme kalitesi)

Tier 2: ~30 dk'da bir hesaplanir ve cache'lenir.
"""
from typing import Any, Dict, List

import pandas as pd


def correlation_report(klines_map: Dict[str, pd.DataFrame], symbols: List[str],
                       lookback: int = 90, top_n: int = 40) -> Dict[str, Any]:
    valid = [s for s in symbols if s in klines_map and klines_map[s] is not None
             and len(klines_map[s]) >= lookback // 3]
    valid = valid[:top_n]
    if len(valid) < 5:
        return {"matrix": {}, "market_avg": {}, "avg_level": 0.0, "symbols": []}
    closes = {s: klines_map[s]["close"].tail(lookback) for s in valid}
    frame = pd.DataFrame(closes)
    ret = frame.pct_change()
    if ret.shape[0] < 20:
        return {"matrix": {}, "market_avg": {}, "avg_level": 0.0, "symbols": []}
    corr = ret.corr()
    matrix = {}
    market_avg = {}
    level_sum, level_cnt = 0.0, 0
    for s1 in valid:
        row = {}
        for s2 in valid:
            if s1 == s2:
                continue
            v = corr.loc[s1, s2]
            if pd.isna(v):
                continue
            c = round(float(v), 3)
            row[s2] = c
            level_sum += abs(c)
            level_cnt += 1
        market_avg[s1] = round(sum(abs(v) for v in row.values()) / max(len(row), 1), 3)
        matrix[s1] = row
    avg = round(level_sum / max(level_cnt, 1), 3)
    return {"matrix": matrix, "market_avg": market_avg, "avg_level": avg, "symbols": valid}
