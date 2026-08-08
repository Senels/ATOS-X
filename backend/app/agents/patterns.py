"""Grafik desen motoru: pivot tabanli, repaint-free desen tespiti.

Pivot tanimi (tradebot_v24 ile uyumlu): bar i, `sw_len` onceki ve `sw_len`
sonraki barlarin low'undan dusukse pivot low (high icin simetrik). Desenler
yalnizca son kapanis barinda DOGRULANMIS olarak raporlanir — tamamlanmayan
desenler gec (repaint) sinyal vermez.

Desteklenen desenler: double top/bottom, head & shoulders, ascending/
descending/symmetric triangle, flag/pennant, pivot breakout.
Cikti: {"pattern", "direction", "confidence", "levels"} — pattern None ise
desen yok; direction "BUY"/"SELL" (sym. triangle'da None).
"""
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


def _pivots(df: pd.DataFrame, sw_len: int = 5) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    """Pivot high/low dizileri (None = pivot degil)."""
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    n = len(df)
    piv_high: List[Optional[float]] = [None] * n
    piv_low: List[Optional[float]] = [None] * n
    for i in range(sw_len, n - sw_len):
        if highs[i] >= highs[i - sw_len:i + sw_len + 1].max():
            piv_high[i] = float(highs[i])
        if lows[i] <= lows[i - sw_len:i + sw_len + 1].min():
            piv_low[i] = float(lows[i])
    return piv_high, piv_low


def _collect(pivots: List[Optional[float]]) -> List[Tuple[int, float]]:
    return [(i, v) for i, v in enumerate(pivots) if v is not None]


def _close_enough(a: float, b: float, tol: float = 0.015) -> bool:
    return abs(a - b) / max(abs(b), 1e-9) <= tol


def _swing(pts: List[Tuple[int, float]], k: int = 3) -> List[Tuple[int, float]]:
    """Pivot noktalarini yan yana olanlardan arindirir (max/min secimi)."""
    out: List[Tuple[int, float]] = []
    for idx, val in pts:
        if out and idx - out[-1][0] <= k:
            if val > out[-1][1]:
                out[-1] = (idx, val)
            continue
        out.append((idx, val))
    return out


def _double_top(hi: List[Tuple[int, float]], lo: List[Tuple[int, float]], close: float) -> Optional[Dict[str, Any]]:
    if len(hi) < 2 or len(lo) < 1:
        return None
    t1, t2 = hi[-2], hi[-1]
    if not _close_enough(t1[1], t2[1]) or t2[0] <= t1[0]:
        return None
    neckline = min(v for _, v in lo if t1[0] < _ < t2[0]) if any(t1[0] < i < t2[0] for i, _ in lo) else None
    if neckline is None:
        return None
    return {"pattern": "Double Top", "direction": "SELL", "confidence": 0.6,
            "levels": {"neckline": neckline, "left": t1[1], "right": t2[1]}}


def _double_bottom(hi: List[Tuple[int, float]], lo: List[Tuple[int, float]], close: float) -> Optional[Dict[str, Any]]:
    if len(lo) < 2 or len(hi) < 1:
        return None
    b1, b2 = lo[-2], lo[-1]
    if not _close_enough(b1[1], b2[1]) or b2[0] <= b1[0]:
        return None
    neckline = max(v for _, v in hi if b1[0] < _ < b2[0]) if any(b1[0] < i < b2[0] for i, _ in hi) else None
    if neckline is None:
        return None
    return {"pattern": "Double Bottom", "direction": "BUY", "confidence": 0.6,
            "levels": {"neckline": neckline, "left": b1[1], "right": b2[1]}}


def _head_shoulders(hi: List[Tuple[int, float]], lo: List[Tuple[int, float]], close: float) -> Optional[Dict[str, Any]]:
    if len(hi) < 3:
        return None
    l, m, r = hi[-3], hi[-2], hi[-1]
    if not (l[0] < m[0] < r[0] and m[1] > l[1] and m[1] > r[1]):
        return None
    if not _close_enough(l[1], r[1], tol=0.03):
        return None
    valleys = [v for i, v in lo if l[0] < i < r[0]]
    if len(valleys) < 1:
        return None
    neckline = min(valleys)
    return {"pattern": "Head & Shoulders", "direction": "SELL", "confidence": 0.65,
            "levels": {"neckline": neckline, "head": m[1], "left": l[1], "right": r[1]}}


def _inverse_hs(hi: List[Tuple[int, float]], lo: List[Tuple[int, float]], close: float) -> Optional[Dict[str, Any]]:
    if len(lo) < 3:
        return None
    l, m, r = lo[-3], lo[-2], lo[-1]
    if not (l[0] < m[0] < r[0] and m[1] < l[1] and m[1] < r[1]):
        return None
    if not _close_enough(l[1], r[1], tol=0.03):
        return None
    peaks = [v for i, v in hi if l[0] < i < r[0]]
    if len(peaks) < 1:
        return None
    neckline = max(peaks)
    return {"pattern": "Inverse H&S", "direction": "BUY", "confidence": 0.65,
            "levels": {"neckline": neckline, "head": m[1], "left": l[1], "right": r[1]}}


def _triangles(hi: List[Tuple[int, float]], lo: List[Tuple[int, float]], close: float) -> Optional[Dict[str, Any]]:
    if len(hi) < 3 or len(lo) < 3:
        return None
    h1, h2, h3 = hi[-3], hi[-2], hi[-1]
    l1, l2, l3 = lo[-3], lo[-2], lo[-1]
    resist = [h1[1], h2[1], h3[1]]
    support = [l1[1], l2[1], l3[1]]
    r_flat = max(resist) - min(resist) <= max(resist) * 0.02
    s_flat = max(support) - min(support) <= max(support) * 0.02
    r_rising = support[-1] > support[0] and support[1] >= support[0] - 1e-12
    s_falling = resist[-1] < resist[0] and resist[1] <= resist[0] + 1e-12
    if r_flat and r_rising:
        return {"pattern": "Ascending Triangle", "direction": "BUY", "confidence": 0.55,
                "levels": {"resistance": max(resist), "support": support[-1]}}
    if s_flat and s_falling:
        return {"pattern": "Descending Triangle", "direction": "SELL", "confidence": 0.55,
                "levels": {"support": min(support), "resistance": resist[-1]}}
    if (max(resist) - min(resist)) <= max(resist) * 0.06 and (max(support) - min(support)) <= max(support) * 0.06:
        return {"pattern": "Symmetric Triangle", "direction": None, "confidence": 0.4,
                "levels": {"resistance": max(resist), "support": min(support)}}
    return None


def _flag(df: pd.DataFrame, close: float) -> Optional[Dict[str, Any]]:
    if len(df) < 25:
        return None
    c = df["close"].to_numpy()
    thrust = (c[-8] / c[-13] - 1) if len(c) > 13 else 0.0
    if abs(thrust) < 0.06:
        return None
    consol = (max(c[-5:]) - min(c[-5:])) / c[-5] if len(c) >= 5 else 0.0
    if consol > abs(thrust) * 0.6:
        return None
    direction = "BUY" if thrust > 0 else "SELL"
    return {"pattern": "Flag", "direction": direction, "confidence": 0.5,
            "levels": {"thrust_pct": round(thrust * 100, 2), "consol_pct": round(consol * 100, 2)}}


def _breakout(hi: List[Tuple[int, float]], lo: List[Tuple[int, float]], close: float) -> Optional[Dict[str, Any]]:
    recent_hi = [v for _, v in hi[-4:]]
    recent_lo = [v for _, v in lo[-4:]]
    if not recent_hi or not recent_lo:
        return None
    if close > max(recent_hi) * 1.003:
        return {"pattern": "Pivot Breakout", "direction": "BUY", "confidence": 0.55,
                "levels": {"resistance": max(recent_hi)}}
    if close < min(recent_lo) * 0.997:
        return {"pattern": "Pivot Breakout", "direction": "SELL", "confidence": 0.55,
                "levels": {"support": min(recent_lo)}}
    return None


def detect_patterns(df: pd.DataFrame, sw_len: int = 5) -> Dict[str, Any]:
    """Son bar icin tespit edilen deseni doner (None = yok)."""
    if df is None or len(df) < 30:
        return {"pattern": None, "direction": None, "confidence": 0.0, "levels": {}}
    close = float(df["close"].iloc[-1])
    piv_high, piv_low = _pivots(df, sw_len)
    hi = _swing(_collect(piv_high))
    lo = _swing(_collect(piv_low))
    checks = [
        _double_top(hi, lo, close),
        _double_bottom(hi, lo, close),
        _head_shoulders(hi, lo, close),
        _inverse_hs(hi, lo, close),
        _triangles(hi, lo, close),
        _flag(df, close),
        _breakout(hi, lo, close),
    ]
    for res in checks:
        if res is not None:
            return res
    return {"pattern": None, "direction": None, "confidence": 0.0, "levels": {}}
