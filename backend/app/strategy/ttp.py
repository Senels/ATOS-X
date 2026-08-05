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


def _trail_offset(base: float, open_atr: float, p: dict) -> float:
    if p["dist_method"] == "perc":
        return base * p["dist_perc"]
    return p["dist_atr_mul"] * open_atr


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

    # -- tam durum makinesi (optimize_ttp.py ile birebir) --------------------
    def _indicators(self, df: pd.DataFrame, p: dict):
        close = df["close"].to_numpy()
        fast = _sma(close, int(p["fast_ma_len"]))
        slow = _sma(close, int(p["slow_ma_len"]))
        atr = _atr_wilder(df["high"].to_numpy(), df["low"].to_numpy(), close, int(p["atr_len"]))
        cross_up, cross_dn = _cross_flags(fast, slow)
        return close, fast, slow, atr, cross_up, cross_dn

    def analyze_full(self, df: pd.DataFrame) -> Dict[str, Any]:
        """TTPTSL tam durum makinesi (optimize_ttp.py run_backtest ile birebir).

        `analyze` gibi crossover barinda giris sinyali uretir; ek olarak her
        bar icin pozisyondayken tasinan (trailing/breakeven uygulanmis) sl/tp
        ve cikis direktiflerini dondurur. `orders` kolonlari:

            signal:     1 = long giris, -1 = short giris, 0 = bekle
            sl/tp:      pozisyon aktifken ilgili barin guncel SL/TP'si (dolu)
            strength:   sinyal barinda 1.0, diger barlarda 0.0
            in_position: pozisyon o bar boyunca acik mi
            exit:       '' | 'sl' | 'tp_partial' | 'trail_tp' | 'reversal'
            exit_qty_pct: o bar cikisinda kapatilacak miktar orani
            exit_price: cikis fiyati (tp_partial icin TP, trail_tp icin trail,
                        reversal icin kapanis, sl icin SL)
        """
        df = self._prepare(df)
        p = self._params()
        close, fast, slow, atr, cross_up, cross_dn = self._indicators(df, p)
        high = df["high"].to_numpy()
        low = df["low"].to_numpy()
        warmup = max(int(p["fast_ma_len"]), int(p["slow_ma_len"]), int(p["atr_len"])) + 1
        n = len(df)

        signal = np.zeros(n, dtype=int)
        sl = np.full(n, np.nan)
        tp = np.full(n, np.nan)
        strength = np.zeros(n)
        in_pos = np.zeros(n, dtype=bool)
        exit_r = np.array([""] * n, dtype=object)
        exit_qty = np.zeros(n)
        exit_price = np.full(n, np.nan)

        active = False
        direction = 0
        entry = 0.0
        open_atr = 0.0
        cur_sl = 0.0
        cur_tp = 0.0
        qty = 1.0
        trailing_sl = False
        tp_hit = False
        tp_trailing = False
        trail_exit = 0.0
        be_active = False

        def _enter(side: int) -> None:
            nonlocal active, direction, entry, open_atr, cur_sl, cur_tp, qty
            nonlocal trailing_sl, tp_hit, tp_trailing, be_active
            active, direction = True, side
            entry = close[i]
            open_atr = atr[i] if np.isfinite(atr[i]) else 0.0
            if side == 1:
                cur_sl = _long_sl(entry, open_atr, p)
                cur_tp = _long_tp(close[i], _long_sl(close[i], open_atr, p), open_atr, p)
            else:
                cur_sl = _short_sl(entry, open_atr, p)
                cur_tp = _short_tp(close[i], _short_sl(close[i], open_atr, p), open_atr, p)
            qty = 1.0
            trailing_sl = p["sl_trail_mode"] == "ON"
            tp_hit = False
            tp_trailing = False
            be_active = False
            signal[i], strength[i] = side, 1.0
            sl[i], tp[i], in_pos[i] = cur_sl, cur_tp, True

        for i in range(warmup, n):
            if not active:
                if cross_up[i]:
                    _enter(1)
                elif cross_dn[i]:
                    _enter(-1)
                continue

            sl[i], tp[i], in_pos[i] = cur_sl, cur_tp, True

            if direction == 1:
                sl_hit = low[i] <= cur_sl
                tp_hit_now = (not tp_hit) and high[i] >= cur_tp
                trail_hit = tp_trailing and low[i] <= trail_exit
                reversal = cross_dn[i]
            else:
                sl_hit = high[i] >= cur_sl
                tp_hit_now = (not tp_hit) and low[i] <= cur_tp
                trail_hit = tp_trailing and high[i] >= trail_exit
                reversal = cross_up[i]

            if sl_hit:
                exit_r[i], exit_price[i], exit_qty[i] = "sl", cur_sl, qty
                active, qty = False, 0.0
            elif tp_hit_now:
                if p["tp_trail_enabled"]:
                    tp_hit = True
                    tp_trailing = True
                    trail_exit = cur_tp
                else:
                    exit_q = qty * p["tp_qty_pct"]
                    exit_r[i], exit_price[i], exit_qty[i] = "tp_partial", cur_tp, exit_q
                    qty -= exit_q
                    tp_hit = True
                    if qty < 1e-12:
                        active = False
                        continue
                if p["sl_trail_mode"] == "TP":
                    trailing_sl = True
                if p["be_enabled"]:
                    be_active = True
            elif trail_hit:
                exit_r[i], exit_price[i], exit_qty[i] = "trail_tp", trail_exit, qty
                active, qty = False, 0.0
            elif reversal:
                exit_r[i], exit_price[i], exit_qty[i] = "reversal", close[i], qty
                active, qty = False, 0.0

            if not active:
                continue

            base = high[i] if direction == 1 else low[i]
            if not trailing_sl:
                base = entry
            new_sl = _long_sl(base, open_atr, p) if direction == 1 else _short_sl(base, open_atr, p)
            cur_sl = max(cur_sl, new_sl) if direction == 1 else min(cur_sl, new_sl)
            if be_active:
                cur_sl = max(cur_sl, entry) if direction == 1 else min(cur_sl, entry)
            if tp_trailing:
                dist = _trail_offset(base, open_atr, p)
                trail_exit = max(trail_exit, high[i] - dist) if direction == 1 else min(trail_exit, low[i] + dist)

        orders = pd.DataFrame({
            "signal": signal, "sl": sl, "tp": tp, "strength": strength,
            "in_position": in_pos, "exit": exit_r, "exit_qty_pct": exit_qty,
            "exit_price": exit_price,
        }, index=df.index)
        return {
            "orders": orders,
            "fast_ma": pd.Series(fast, index=df.index),
            "slow_ma": pd.Series(slow, index=df.index),
            "cross_up": pd.Series(cross_up, index=df.index),
            "cross_dn": pd.Series(cross_dn, index=df.index),
        }

    def manage(self, df: pd.DataFrame, entry_ts, entry_price: float, side: str,
               qty: float, tp_already_hit: bool = False) -> Dict[str, Any]:
        """Canli pozisyon yonetimi: giristen itibaren durum makinesini calistirir.

        `entry_ts` giris barinin zaman damgasi, `side` "BUY"/"SELL", `qty`
        kalan miktar. `tp_already_hit` kismi TP'nin daha once yapildigini
        bildirir (tekrar kismi kapatmayi onler).

        Donen:
            active:        strateji hala pozisyonda mi
            sl/tp:         guncel SL/TP (active ise)
            exit:          '' | 'sl' | 'tp_partial' | 'trail_tp' | 'reversal'
            exit_price:    cikis fiyati (exit varsa)
            exit_qty_pct:  kapatilacak miktar orani (tp_partial icin <1 olabilir)
            exit_bar_idx:  direktifin gerceklestigi bar indeksi
        """
        df = self._prepare(df)
        p = self._params()
        close, fast, slow, atr, cross_up, cross_dn = self._indicators(df, p)
        high = df["high"].to_numpy()
        low = df["low"].to_numpy()
        n = len(df)

        ts = pd.Timestamp(entry_ts)
        if df.index.tz is not None and ts.tzinfo is None:
            ts = ts.tz_localize(df.index.tz)
        elif df.index.tz is None and ts.tzinfo is not None:
            ts = ts.tz_convert(None)
        if ts in df.index:
            start = df.index.get_loc(ts)
        else:
            idx = df.index.get_indexer([ts], method="nearest")[0]
            start = max(int(idx), 0)
        start = min(start, n - 1)

        direction = 1 if side == "BUY" else -1
        entry = float(entry_price)
        open_atr = atr[start]
        if not np.isfinite(open_atr):
            open_atr = atr[max(start - 1, 0)] if n > 1 else 0.0
            if not np.isfinite(open_atr):
                open_atr = 0.0
        if direction == 1:
            cur_sl = _long_sl(close[start], open_atr, p)
            cur_tp = _long_tp(close[start], _long_sl(close[start], open_atr, p), open_atr, p)
        else:
            cur_sl = _short_sl(close[start], open_atr, p)
            cur_tp = _short_tp(close[start], _short_sl(close[start], open_atr, p), open_atr, p)

        trailing_sl = p["sl_trail_mode"] == "ON"
        tp_hit = False
        tp_trailing = False
        trail_exit = 0.0
        be_active = False
        qty_rem = float(qty)

        res = {"active": True, "sl": cur_sl, "tp": cur_tp, "exit": "",
               "exit_price": None, "exit_qty_pct": 0.0, "exit_bar_idx": None}

        for i in range(start + 1, n):
            if direction == 1:
                sl_hit = low[i] <= cur_sl
                tp_hit_now = (not tp_hit) and high[i] >= cur_tp
                trail_hit = tp_trailing and low[i] <= trail_exit
                reversal = cross_dn[i]
            else:
                sl_hit = high[i] >= cur_sl
                tp_hit_now = (not tp_hit) and low[i] <= cur_tp
                trail_hit = tp_trailing and high[i] >= trail_exit
                reversal = cross_up[i]

            if sl_hit:
                return {"active": False, "sl": cur_sl, "tp": cur_tp, "exit": "sl",
                        "exit_price": cur_sl, "exit_qty_pct": 1.0, "exit_bar_idx": i}
            if tp_hit_now:
                tp_hit = True
                if not tp_already_hit and not p["tp_trail_enabled"]:
                    exit_q = qty_rem * p["tp_qty_pct"]
                    qty_rem -= exit_q
                    res = {"active": True, "sl": cur_sl, "tp": cur_tp, "exit": "tp_partial",
                           "exit_price": cur_tp, "exit_qty_pct": p["tp_qty_pct"],
                           "exit_bar_idx": i}
                    if qty_rem < 1e-12:
                        res["active"] = False
                        res["exit_qty_pct"] = 1.0
                        return res
                    return res  # kismi TP: hemen geri don, kalan `tp_already_hit` ile takip edilir
                if p["tp_trail_enabled"]:
                    tp_trailing = True
                    trail_exit = cur_tp
                if p["sl_trail_mode"] == "TP":
                    trailing_sl = True
                if p["be_enabled"]:
                    be_active = True
            elif trail_hit:
                return {"active": False, "sl": cur_sl, "tp": cur_tp, "exit": "trail_tp",
                        "exit_price": trail_exit, "exit_qty_pct": 1.0, "exit_bar_idx": i}
            elif reversal:
                return {"active": False, "sl": cur_sl, "tp": cur_tp, "exit": "reversal",
                        "exit_price": float(close[i]), "exit_qty_pct": 1.0, "exit_bar_idx": i}

            base = high[i] if direction == 1 else low[i]
            if not trailing_sl:
                base = entry
            new_sl = _long_sl(base, open_atr, p) if direction == 1 else _short_sl(base, open_atr, p)
            cur_sl = max(cur_sl, new_sl) if direction == 1 else min(cur_sl, new_sl)
            if be_active:
                cur_sl = max(cur_sl, entry) if direction == 1 else min(cur_sl, entry)
            if tp_trailing:
                dist = _trail_offset(base, open_atr, p)
                trail_exit = max(trail_exit, high[i] - dist) if direction == 1 else min(trail_exit, low[i] + dist)

        res["sl"] = cur_sl
        res["tp"] = cur_tp
        return res

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
