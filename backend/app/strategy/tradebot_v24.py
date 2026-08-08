"""ATOS-X v24 Lite — TradingView Pine v6 (`v24_lite.pine`) Python karsiligi.

Temizlenmis, overfit'siz strateji: EMA 50/200 trend filtresi + RSI 55/45
yon filtreleri + swing pivot SL (repaint-free) / ATR fallback + RR 1.8 TP.

Pine semantigi:
  - `ta.pivotlow(low, lb, rb)`: pivot formasyonu rb bar sonra kesinlesir;
    bu yuzden pivot degeri `rb` bar oteleinip ffill edilir (repaint yok).
  - `strategy.entry` kapanis barinda karar verir (process_orders_on_close).
  - SL: son pivot fiyat (anlamliysa), yoksa ATR fallback; gecersizse giris yok.
"""
from copy import deepcopy
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from app.strategy import settings as strat_settings
from app.strategy.tradebot_v23 import atr, ema, rsi


def pivot_low(series: pd.Series, lb: int, rb: int) -> np.ndarray:
    """Pine ta.pivotlow: i barinda low[i], onceki lb ve sonraki rb barin
    mininden kucukse pivot. Degerler i'ye atanir (kesinlesme rb sonra)."""
    arr = series.to_numpy(dtype=np.float64)
    n = len(arr)
    out = np.full(n, np.nan)
    if n < lb + rb + 1:
        return out
    left_min = pd.Series(arr).rolling(lb).min().shift(1).to_numpy()
    right_min = pd.Series(arr).rolling(rb).min().shift(-rb).to_numpy()
    is_pivot = (arr < left_min) & (arr < right_min) & ~np.isnan(arr)
    out[is_pivot] = arr[is_pivot]
    return out


def pivot_high(series: pd.Series, lb: int, rb: int) -> np.ndarray:
    """Pine ta.pivothigh: max karsilastirmasi."""
    arr = series.to_numpy(dtype=np.float64)
    n = len(arr)
    out = np.full(n, np.nan)
    if n < lb + rb + 1:
        return out
    left_max = pd.Series(arr).rolling(lb).max().shift(1).to_numpy()
    right_max = pd.Series(arr).rolling(rb).max().shift(-rb).to_numpy()
    is_pivot = (arr > left_max) & (arr > right_max) & ~np.isnan(arr)
    out[is_pivot] = arr[is_pivot]
    return out


class TradeBotV24:
    def __init__(self, settings: Optional[Dict[str, Any]] = None):
        self.settings = strat_settings.default_settings()
        if settings:
            self.update_settings(settings)

    def update_settings(self, patch: Dict[str, Any]) -> None:
        for key, value in patch.items():
            if key in self.settings:
                if isinstance(self.settings[key], dict) and isinstance(value, dict):
                    self.settings[key].update(value)
                else:
                    self.settings[key] = value

    def get_settings(self) -> Dict[str, Any]:
        return deepcopy(self.settings)

    # -- analiz -------------------------------------------------------------
    def analyze(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Tum bar serisi icin sinyal + SL/TP (engine ile uyumlu format).

        signal: 1 (long) / -1 (short) / 0; sl/tp: fiyat seviyeleri.
        Sinyal barinin KAPANISINDA gecerlidir; backtest sonraki bar acilisinda
        uygular (engine sorumlulugu).
        """
        df = self._prepare(df)
        s = self.settings
        v = s.get("v24", {}) or {}
        ema_fast = int(v.get("ema_fast", 50))
        ema_slow = int(v.get("ema_slow", 200))
        rsi_len = int(v.get("rsi_len", 14))
        rsi_long = float(v.get("rsi_long", 55))
        rsi_short = float(v.get("rsi_short", 45))
        rr = float(v.get("rr_ratio", s.get("rr_ratio", 1.8)))
        sw_len = int(v.get("sl_lookback", s.get("sl_lookback", 5)))
        atr_mult = float(v.get("atr_mult", s.get("atr_mult", 1.5)))

        close = df["close"]
        high = df["high"]
        low = df["low"]

        emaF = ema(close, ema_fast)
        emaS = ema(close, ema_slow)
        rsi_v = rsi(close, rsi_len)
        atr_v = atr(df, 14)

        # Repaint-free pivot: kesinlesme sw_len bar sonra (var degisken ffill'i)
        last_low = pd.Series(pivot_low(low, sw_len, sw_len), index=df.index).shift(sw_len).ffill()
        last_high = pd.Series(pivot_high(high, sw_len, sw_len), index=df.index).shift(sw_len).ffill()

        # SL: pivot anlamliysa pivot, degilse ATR fallback (Pine ternary)
        fallback_long = close - atr_v * atr_mult
        long_sl = last_low.where(last_low.notna() & (last_low < close), fallback_long)
        fallback_short = close + atr_v * atr_mult
        short_sl = last_high.where(last_high.notna() & (last_high > close), fallback_short)

        long_cond = (close > emaF) & (emaF > emaS) & (rsi_v > rsi_long)
        short_cond = (close < emaF) & (emaF < emaS) & (rsi_v < rsi_short)

        # Pine `sl_dist > 0` kosulu (gecersiz SL'de giris yok)
        long_ok = long_cond & (long_sl < close)
        short_ok = short_cond & (short_sl > close)

        long_tp = close + (close - long_sl) * rr
        short_tp = close - (short_sl - close) * rr

        signal = np.where(long_ok.to_numpy(), 1,
                          np.where(short_ok.to_numpy(), -1, 0)).astype(int)

        orders = pd.DataFrame(
            {
                "signal": signal,
                "sl": long_sl.where(long_ok, short_sl.where(short_ok, np.nan)).to_numpy(),
                "tp": long_tp.where(long_ok, short_tp.where(short_ok, np.nan)).to_numpy(),
                "strength": np.where(signal != 0, 1.0, 0.0),
            },
            index=df.index,
        )

        return {
            "orders": orders,
            "long_cond": long_ok,
            "short_cond": short_ok,
            "ema_fast": emaF,
            "ema_slow": emaS,
            "rsi": rsi_v,
        }

    def generate_signal(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Canli kullanim: son barin sinyali + SL/TP + aciklama."""
        if df is None or len(df) < 30:
            return {"signal": "HOLD", "reason": "Yetersiz veri", "price": None}
        result = self.analyze(df)
        last = result["orders"].iloc[-1]
        price = float(df["close"].iloc[-1])
        sig = int(last["signal"])
        sl = last["sl"]
        tp = last["tp"]

        if sig == 1 and not (pd.isna(sl)):
            return {"signal": "BUY", "price": price, "sl": float(sl), "tp": float(tp),
                    "reason": "v24: EMA50>EMA200 + RSI>55", "indicator": "v24 Lite",
                    "strength": 1.0}
        if sig == -1 and not (pd.isna(sl)):
            return {"signal": "SELL", "price": price, "sl": float(sl), "tp": float(tp),
                    "reason": "v24: EMA50<EMA200 + RSI<45", "indicator": "v24 Lite",
                    "strength": 1.0}
        return {"signal": "HOLD", "price": price, "sl": None, "tp": None,
                "reason": "Aktif sinyal yok", "indicator": "v24 Lite",
                "strength": 0.0}

    # -- yardimci -----------------------------------------------------------
    def _prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        need = ["open", "high", "low", "close", "volume"]
        for col in need:
            if col not in df.columns:
                raise ValueError(f"Veride '{col}' sutunu yok: {list(df.columns)}")
        return df[need].copy()
