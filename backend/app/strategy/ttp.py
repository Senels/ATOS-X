"""TTPTSL strateji motoru - Pine Script portunun TradeBotV23 uyumlu surumu.

Strateji: hizli/yavas SMA crossover ile giris, yuzde/ATR/RR TP, yuzde/ATR SL.
Girisler crossover (long) / crossunder (short) barinda uretilir; SL/TP o
bardaki kapanis ve ATR ile hesaplanir. Backtest/canli motoru (BacktestEngine
/ AutoTrader) pozisyon yonetimini (SL/TP/kapanis) devralir.

Cikti (analyze) `orders` DataFrame'i doner (TradeBotV23 ile ayni sozlesme):
    signal:  1 = long giris, -1 = short giris, 0 = bekle
    sl:      sinyal barindaki stop fiyati
    tp:      sinyal barindaki take-profit fiyati
    strength: sinyal barinda 1.0, diger barlarda 0.0

Parametreler `settings["ttp"]` bloğundan alinir (optimize_ttp.py unified
sonuclarina karsi kalibre edilmistir).
"""
from copy import deepcopy
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from app.strategy import settings as strat_settings


# ---------------------------------------------------------------------------
# Temel gostergeler (optimize_ttp.py ile birebir)
# ---------------------------------------------------------------------------
def _sma(arr: np.ndarray, period: int) -> np.ndarray:
    if period <= 0 or period > len(arr):
        return np.full(len(arr), np.nan)
    kernel = np.ones(period) / period
    valid = np.convolve(arr, kernel, mode="valid")
    out = np.full(len(arr), np.nan)
    out[period - 1:] = valid
    return out


def _atr_wilder(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    prev_close = np.roll(close, 1)
    prev_close[0] = np.nan
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    return pd.Series(tr).ewm(alpha=1.0 / period, adjust=False).mean().to_numpy()


def _cross_flags(fast: np.ndarray, slow: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    up = (fast > slow) & ~np.isnan(fast) & ~np.isnan(slow)
    down = (fast < slow) & ~np.isnan(fast) & ~np.isnan(slow)
    cross_up = up & ~np.roll(up, 1)
    cross_dn = down & ~np.roll(down, 1)
    cross_up[0] = False
    cross_dn[0] = False
    return cross_up, cross_dn


def _long_sl(base: float, open_atr: float, p: dict) -> float:
    if p["sl_method"] == "perc":
        return base * (1 - p["sl_long_perc"])
    return base - p["sl_long_atr_mul"] * open_atr


def _short_sl(base: float, open_atr: float, p: dict) -> float:
    if p["sl_method"] == "perc":
        return base * (1 + p["sl_short_perc"])
    return base + p["sl_short_atr_mul"] * open_atr


def _long_tp(close: float, sl: float, open_atr: float, p: dict) -> float:
    if p["tp_method"] == "perc":
        return close * (1 + p["tp_long_perc"])
    if p["tp_method"] == "atr":
        return close + p["tp_long_atr_mul"] * open_atr
    return close + p["tp_long_rr"] * (close - sl)


def _short_tp(close: float, sl: float, open_atr: float, p: dict) -> float:
    if p["tp_method"] == "perc":
        return close * (1 - p["tp_short_perc"])
    if p["tp_method"] == "atr":
        return close - p["tp_short_atr_mul"] * open_atr
    return close - p["tp_short_rr"] * (sl - close)


# ---------------------------------------------------------------------------
# Strateji sinifi
# ---------------------------------------------------------------------------
class TtpTsl:
    def __init__(self, settings: Optional[Dict[str, Any]] = None):
        self.settings = strat_settings.default_settings()
        if settings:
            self.update_settings(settings)

    # -- yonetim ------------------------------------------------------------
    def update_settings(self, patch: Dict[str, Any]) -> None:
        for key, value in patch.items():
            if key in self.settings:
                if isinstance(self.settings[key], dict) and isinstance(value, dict):
                    self.settings[key].update(value)
                else:
                    self.settings[key] = value

    def get_settings(self) -> Dict[str, Any]:
        return deepcopy(self.settings)

    def _params(self) -> dict:
        return dict(self.settings.get("ttp", {}))

    # -- analiz -------------------------------------------------------------
    def analyze(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Tum bar serisi icin sinyal + SL/TP uretir. Ileriye donuk sizinti yok.

        Sinyal crossover/crossunder barinin KAPANISINDA gecerlidir; backtest
        bir sonraki barin acilisinda uygular (TradeBotV23 ile ayni sozlesme).
        """
        df = self._prepare(df)
        p = self._params()
        fast_len = int(p["fast_ma_len"])
        slow_len = int(p["slow_ma_len"])
        atr_len = int(p["atr_len"])

        close = df["close"].to_numpy()
        fast = _sma(close, fast_len)
        slow = _sma(close, slow_len)
        atr = _atr_wilder(df["high"].to_numpy(), df["low"].to_numpy(), close, atr_len)
        cross_up, cross_dn = _cross_flags(fast, slow)

        warmup = max(fast_len, slow_len, atr_len) + 1
        n = len(df)
        signal = np.zeros(n, dtype=int)
        sl = np.full(n, np.nan)
        tp = np.full(n, np.nan)
        strength = np.zeros(n)

        for i in range(warmup, n):
            oa = atr[i]
            if cross_up[i]:
                signal[i] = 1
                if np.isfinite(oa):
                    sl[i] = _long_sl(close[i], oa, p)
                    tp[i] = _long_tp(close[i], _long_sl(close[i], oa, p), oa, p)
                    strength[i] = 1.0
            elif cross_dn[i]:
                signal[i] = -1
                if np.isfinite(oa):
                    sl[i] = _short_sl(close[i], oa, p)
                    tp[i] = _short_tp(close[i], _short_sl(close[i], oa, p), oa, p)
                    strength[i] = 1.0

        orders = pd.DataFrame(
            {"signal": signal, "sl": sl, "tp": tp, "strength": strength},
            index=df.index,
        )
        return {
            "orders": orders,
            "fast_ma": pd.Series(fast, index=df.index),
            "slow_ma": pd.Series(slow, index=df.index),
            "cross_up": pd.Series(cross_up, index=df.index),
            "cross_dn": pd.Series(cross_dn, index=df.index),
        }

    def generate_signal(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Canli kullanim: son barin sinyali + SL/TP + aciklama + guc."""
        if df is None or len(df) < 30:
            return {"signal": "HOLD", "reason": "Yetersiz veri", "price": None}
        result = self.analyze(df)
        orders = result["orders"]
        last = orders.iloc[-1]
        price = float(df["close"].iloc[-1])
        sig = int(last["signal"])
        sl = last["sl"]
        tp = last["tp"]

        if sig == 1 and not (pd.isna(sl)):
            return {"signal": "BUY", "price": price, "sl": float(sl), "tp": float(tp),
                    "reason": "TTPTSL: MA yukari crossover", "indicator": "TTPTSL",
                    "strength": 1.0}
        if sig == -1 and not (pd.isna(sl)):
            return {"signal": "SELL", "price": price, "sl": float(sl), "tp": float(tp),
                    "reason": "TTPTSL: MA asagi crossunder", "indicator": "TTPTSL",
                    "strength": 1.0}
        return {"signal": "HOLD", "price": price, "sl": None, "tp": None,
                "reason": "Aktif sinyal yok", "indicator": "TTPTSL", "strength": 0.0}

    # -- yardimci -----------------------------------------------------------
    def _prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        need = ["open", "high", "low", "close", "volume"]
        for col in need:
            if col not in df.columns:
                raise ValueError(f"Veride '{col}' sutunu yok: {list(df.columns)}")
        out = df[need].copy()
        for col in need:
            out[col] = pd.to_numeric(out[col], errors="coerce").astype(float)
        return out
