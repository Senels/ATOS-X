"""TradeBot v23 strateji motoru - TradingView Pine v23 portunun vektorize surumu.

Tum hesaplamalar bar bazinda (tum seri) yapilir; geriye bakma (lookahead) yoktur.
Sinyal, bari kapattiktan sonra uretilir ve backtest motoru bir sonraki barin
acilisiyla girer (Pine varsayilan davranisi).

Cikti (analyze) `orders` DataFrame'i doner:
    signal:  1 = long giris, -1 = short giris, 0 = bekle
    sl:      sinyal barindaki stop fiyati
    tp:      sinyal barindaki take-profit fiyati
"""
from copy import deepcopy
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from app.strategy import settings as strat_settings


# ---------------------------------------------------------------------------
# Vektorize temel gostergeler (Pine semantigi)
# ---------------------------------------------------------------------------
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def rma(s: pd.Series, n: int) -> pd.Series:
    """Wilder smoothing (Pine ta.rma)."""
    return s.ewm(alpha=1.0 / n, adjust=False).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat(
        [(df["high"] - df["low"]), (df["high"] - pc).abs(), (df["low"] - pc).abs()],
        axis=1,
    ).max(axis=1)
    return tr


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return rma(true_range(df), n)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = rma(gain, n)
    avg_loss = rma(loss, n)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.fillna(100)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    f = ema(close, fast)
    s = ema(close, slow)
    m = f - s
    sg = m.ewm(span=signal, adjust=False).mean()
    return m, sg, m - sg


def stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> Tuple[pd.Series, pd.Series]:
    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    rng = (high_max - low_min)
    k = 100 * (df["close"] - low_min) / rng.where(rng > 0)
    d = k.rolling(d_period).mean()
    return k, d


def ichimoku(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series, pd.Series]:
    tenkan = (df["high"].rolling(9).max() + df["low"].rolling(9).min()) / 2
    kijun = (df["high"].rolling(26).max() + df["low"].rolling(26).min()) / 2
    senkou_a = (tenkan + kijun) / 2
    return senkou_a, tenkan, kijun


def supertrend(df: pd.DataFrame, period: int = 10, mult: float = 3.0) -> Tuple[pd.Series, pd.Series]:
    """Pine Supertrend: (super_trend_cizgisi, yon) - yon 1 = up, -1 = down."""
    hl2 = (df["high"] + df["low"]) / 2
    a = atr(df, period)
    upper = hl2 - mult * a
    lower = hl2 + mult * a
    n = len(df)
    trend = np.ones(n, dtype=int)
    st = np.full(n, np.nan)
    fup = upper.to_numpy(copy=True)
    fdn = lower.to_numpy(copy=True)
    u = upper.to_numpy()
    lo = lower.to_numpy()
    c = df["close"].to_numpy()
    for i in range(1, n):
        fup[i] = u[i] if (u[i] < fup[i - 1] or c[i - 1] > fup[i - 1]) else fup[i - 1]
        fdn[i] = lo[i] if (lo[i] > fdn[i - 1] or c[i - 1] < fdn[i - 1]) else fdn[i - 1]
        if trend[i - 1] == 1:
            if c[i] < fdn[i]:
                trend[i] = -1
                st[i] = fup[i]
            else:
                trend[i] = 1
                st[i] = fdn[i]
        else:
            if c[i] > fup[i]:
                trend[i] = 1
                st[i] = fdn[i]
            else:
                trend[i] = -1
                st[i] = fup[i]
    return pd.Series(st, index=df.index), pd.Series(trend, index=df.index)


def halftrend(df: pd.DataFrame, amplitude: float = 2.0, deviation: float = 2.0) -> pd.Series:
    """HalfTrend yaklasik portu: yon serisi (1 up / -1 down)."""
    hl2 = (df["high"] + df["low"]) / 2
    a = atr(df, 2) * deviation
    n = len(df)
    trend = np.ones(n, dtype=int)
    atr_high = np.full(n, np.nan)
    atr_low = np.full(n, np.nan)
    h = df["high"].to_numpy()
    lo = df["low"].to_numpy()
    h2 = hl2.to_numpy()
    av = a.to_numpy()
    for i in range(1, n):
        cand_hi = h[i] - av[i]
        cand_lo = lo[i] + av[i]
        atr_high[i] = cand_hi if (cand_hi > atr_high[i - 1]) else (h[i] if h[i] > atr_high[i - 1] else atr_high[i - 1])
        atr_low[i] = cand_lo if (cand_lo < atr_low[i - 1]) else (lo[i] if lo[i] < atr_low[i - 1] else atr_low[i - 1])
        if trend[i - 1] == -1 and h2[i] > atr_high[i]:
            trend[i] = 1
        elif trend[i - 1] == 1 and h2[i] < atr_low[i]:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1]
    return pd.Series(trend, index=df.index)


def chandelier_exit(df: pd.DataFrame, period: int = 22, mult: float = 3.0) -> Tuple[pd.Series, pd.Series]:
    a = atr(df, period)
    long_exit = df["high"].rolling(period).max() - mult * a
    short_exit = df["low"].rolling(period).min() + mult * a
    return long_exit, short_exit


def ssl_channel(df: pd.DataFrame, period: int = 10) -> Tuple[pd.Series, pd.Series]:
    sma_high = df["high"].rolling(period).mean()
    sma_low = df["low"].rolling(period).mean()
    close = df["close"]
    hlv = np.where(close > sma_high, 1, np.where(close < sma_low, -1, 0))
    ssl_down = pd.Series(np.where(hlv < 0, sma_high, sma_low), index=df.index)
    ssl_up = pd.Series(np.where(hlv < 0, sma_low, sma_high), index=df.index)
    return ssl_up, ssl_down


def vwap(df: pd.DataFrame) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"].replace(0, np.nan).fillna(1e-12)
    cum_vp = (tp * vol).cumsum()
    cum_v = vol.cumsum()
    return cum_vp / cum_v


def range_filter(df: pd.DataFrame, length: int = 3, mult: float = 2.5) -> Tuple[pd.Series, pd.Series]:
    """LuxAlgo Range Filter portu: (filt_yukari, filt_asagi) bool serileri."""
    src = (df["high"] + df["low"] + df["close"]) / 3
    wper = length * 2 - 1
    avrng = ema((src - src.shift(1)).abs(), length)
    smooth = ema(avrng, wper) * mult
    r = smooth.to_numpy()
    x = src.to_numpy()
    n = len(x)
    filt = np.empty(n)
    filt[0] = x[0]
    for i in range(1, n):
        prev = filt[i - 1]
        if x[i] > prev:
            cand = x[i] - r[i]
            filt[i] = prev if cand < prev else cand
        else:
            cand = x[i] + r[i]
            filt[i] = prev if cand > prev else cand

    up = np.zeros(n, dtype=int)
    down = np.zeros(n, dtype=int)
    for i in range(1, n):
        if filt[i] > filt[i - 1]:
            up[i] = up[i - 1] + 1
            down[i] = 0
        elif filt[i] < filt[i - 1]:
            down[i] = down[i - 1] + 1
            up[i] = 0
        else:
            up[i] = up[i - 1]
            down[i] = down[i - 1]

    filt_s = pd.Series(filt, index=df.index)
    rf_up = (src > filt_s) & (pd.Series(up, index=df.index) > 0)
    rf_down = (src < filt_s) & (pd.Series(down, index=df.index) > 0)
    return rf_up, rf_down


def rqk(close: pd.Series, period: int = 5, r: float = 8.0) -> pd.Series:
    """Rational Quadratic Kernel filtre (jdehorty portu)."""
    i = np.arange(period)
    w = (1 + (i * i + i) / (2 * r)) ** (-r)
    kernel = w[::-1]  # en eski bar en dusuk agirlik
    return close.rolling(period).apply(lambda x: np.dot(x, kernel) / kernel.sum(), raw=True)


def consecutive_count(cond: pd.Series, cap: int = 100) -> np.ndarray:
    """Ard arda True olan bar sayisi (sinirli)."""
    arr = cond.fillna(False).to_numpy(dtype=bool)
    n = len(arr)
    out = np.zeros(n, dtype=int)
    c = 0
    for i in range(n):
        if arr[i]:
            c += 1
            if c > cap:
                c = cap
        else:
            c = 0
        out[i] = c
    return out


def last_swing_low(low: pd.Series, length: int) -> pd.Series:
    """En son dogrulanmis pivot dip fiyati. Pivot p'de p+length'de onaylanir."""
    n = len(low)
    vals = pd.Series(np.nan, index=low.index)
    if n < 2 * length + 1:
        return vals
    L = length
    lows = low.to_numpy()
    roll_min = low.rolling(2 * L + 1, center=True, min_periods=2 * L + 1).min()
    is_pivot = low <= roll_min
    pv = is_pivot.to_numpy(dtype=bool)
    for p in range(L, n - L):
        if pv[p]:
            vals.iloc[p + L] = lows[p]
    return vals.ffill()


def last_swing_high(high: pd.Series, length: int) -> pd.Series:
    n = len(high)
    vals = pd.Series(np.nan, index=high.index)
    if n < 2 * length + 1:
        return vals
    L = length
    highs = high.to_numpy()
    roll_max = high.rolling(2 * L + 1, center=True, min_periods=2 * L + 1).max()
    is_pivot = high >= roll_max
    pv = is_pivot.to_numpy(dtype=bool)
    for p in range(L, n - L):
        if pv[p]:
            vals.iloc[p + L] = highs[p]
    return vals.ffill()


# ---------------------------------------------------------------------------
# Strateji sinifi
# ---------------------------------------------------------------------------
class TradeBotV23:
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

    # -- analiz -------------------------------------------------------------
    def analyze(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Tum bar serisi icin sinyal + SL/TP uretir. Ileriye donuk sizinti yok.

        signal barinin KAPANISINDA gecerlidir; backtest bir sonraki barin
        acilisinda uygulamalidir.
        """
        df = self._prepare(df)
        s = self.settings
        expiry = int(s["signal_expiry"])
        alternate = bool(s["alternate_signal"])
        rr = float(s["rr_ratio"])
        sw_len = int(s["sl_lookback"])

        # 1) Leading indicator yonleri
        leading_long, leading_short = self._leading(df)

        # 2) Sinyal expiry: leading kosul ilk N bar icerisinde gecerli
        ll_count = pd.Series(consecutive_count(leading_long), index=df.index)
        ls_count = pd.Series(consecutive_count(leading_short), index=df.index)
        long_cond = (leading_long & (ll_count <= expiry)).fillna(False)
        short_cond = (leading_short & (ls_count <= expiry)).fillna(False)

        # 3) Konfirmasyonlar
        conf_long, conf_short, long_count, short_count = self._confirmations(df)
        long_cond = long_cond & conf_long
        short_cond = short_cond & conf_short

        # 4) CondIni (alternate sinyal icin gecmis durum)
        cond_ini = np.where(long_cond.to_numpy(), 1, np.where(short_cond.to_numpy(), -1, np.nan))
        cond_ini_s = pd.Series(cond_ini, index=df.index).ffill()
        prev_cond = cond_ini_s.shift(1)

        if alternate:
            long_sig = (long_cond & (prev_cond != 1)).fillna(False)
            short_sig = (short_cond & (prev_cond != -1)).fillna(False)
        else:
            long_sig = long_cond
            short_sig = short_cond

        # 5) SL / TP
        sl, tp = self._sl_tp(df, long_sig, short_sig, rr, sw_len)

        signal = np.where(long_sig.to_numpy(), 1, np.where(short_sig.to_numpy(), -1, 0)).astype(int)

        # Sinyal gucu (canli generate_signal ile ayni formül): aktif konfirmasyon orani
        enabled = [k for k, v in self.settings["confirmations"].items() if v]
        n_total = max(len(enabled), 1)
        strength = np.where(
            long_sig.to_numpy(),
            long_count.fillna(0).to_numpy() / n_total,
            np.where(
                short_sig.to_numpy(),
                short_count.fillna(0).to_numpy() / n_total,
                0.0,
            ),
        )

        orders = pd.DataFrame(
            {"signal": signal, "sl": sl, "tp": tp, "strength": strength},
            index=df.index,
        )

        return {
            "orders": orders,
            "leading_long": leading_long,
            "leading_short": leading_short,
            "conf_long": conf_long,
            "conf_short": conf_short,
            "long_cond": long_cond,
            "short_cond": short_cond,
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

        name = self.settings["leading_indicator"]
        _, _, long_count, short_count = self._confirmations(df)
        enabled = [k for k, v in self.settings["confirmations"].items() if v]
        n_total = max(len(enabled), 1)
        n_active = int(long_count.iloc[-1]) if sig == 1 else (
            int(short_count.iloc[-1]) if sig == -1 else 0)
        strength = round(n_active / n_total, 2) if sig != 0 else 0.0

        if sig == 1 and not (pd.isna(sl)):
            return {"signal": "BUY", "price": price, "sl": float(sl), "tp": float(tp),
                    "reason": f"v23: {name} yukari + konfirmasyon", "indicator": name,
                    "strength": strength}
        if sig == -1 and not (pd.isna(sl)):
            return {"signal": "SELL", "price": price, "sl": float(sl), "tp": float(tp),
                    "reason": f"v23: {name} asagi + konfirmasyon", "indicator": name,
                    "strength": strength}
        return {"signal": "HOLD", "price": price, "sl": None, "tp": None,
                "reason": "Aktif sinyal yok", "indicator": name,
                "strength": 0.0}

    # -- yardimcilar --------------------------------------------------------
    def _prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        need = ["open", "high", "low", "close", "volume"]
        for col in need:
            if col not in df.columns:
                raise ValueError(f"Veride '{col}' sutunu yok: {list(df.columns)}")
        out = df[need].copy()
        for col in need:
            out[col] = pd.to_numeric(out[col], errors="coerce").astype(float)
        return out

    def _leading(self, df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
        name = self.settings["leading_indicator"]
        close = df["close"]
        s = self.settings

        if name == "Range Filter":
            return range_filter(df, int(s["rangefilt_length"]), float(s["range_filt_mult"]))
        if name == "RQK":
            f = rqk(close, 5, 8)
            return close > f, close < f
        if name == "Supertrend":
            _, trend = supertrend(df, 10, 3)
            return trend == 1, trend == -1
        if name == "MACD":
            m, sg, _ = macd(close)
            return m > sg, m < sg
        if name == "RSI":
            r = rsi(close)
            return r < 30, r > 70
        if name == "Stochastic":
            k, d = stochastic(df)
            return (k < 20) & (k > d), (k > 80) & (k < d)
        if name == "2 EMA Cross":
            e50 = ema(close, 50)
            e200 = ema(close, 200)
            return e50 > e200, e50 < e200
        if name == "3 EMA Cross":
            e9 = ema(close, 9)
            e21 = ema(close, 21)
            e55 = ema(close, 55)
            return (e9 > e21) & (e21 > e55), (e9 < e21) & (e21 < e55)
        if name == "Ichimoku":
            senkou_a, _, _ = ichimoku(df)
            return close > senkou_a, close < senkou_a
        if name == "SSL Channel":
            up, down = ssl_channel(df)
            return up > down, up < down
        if name == "VWAP":
            v = vwap(df)
            return close > v, close < v
        # Bilinmeyen -> varsayilan Range Filter
        return range_filter(df, int(s["rangefilt_length"]), float(s["range_filt_mult"]))

    def _confirmations(self, df: pd.DataFrame) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
        conf = self.settings["confirmations"]
        close = df["close"]
        long_conds, short_conds = [], []

        def _add(long_c, short_c):
            long_conds.append(long_c)
            short_conds.append(short_c)

        if conf.get("rqk"):
            f = rqk(close, 5, 8)
            _add(close > f, close < f)
        if conf.get("rf"):
            up, down = range_filter(df, int(self.settings["rangefilt_length"]), float(self.settings["range_filt_mult"]))
            _add(up, down)
        if conf.get("ema"):
            e = ema(close, 200)
            _add(close > e, close < e)
        if conf.get("2ma"):
            e50, e200 = ema(close, 50), ema(close, 200)
            _add(e50 > e200, e50 < e200)
        if conf.get("3ma"):
            e9, e21, e55 = ema(close, 9), ema(close, 21), ema(close, 55)
            _add((e9 > e21) & (e21 > e55), (e9 < e21) & (e21 < e55))
        if conf.get("st"):
            _, trend = supertrend(df, 10, 3)
            _add(trend == 1, trend == -1)
        if conf.get("ht"):
            trend = halftrend(df)
            _add(trend == 1, trend == -1)
        if conf.get("rsi"):
            r = rsi(close)
            _add(r > 50, r < 50)
        if conf.get("macd"):
            m, sg, _ = macd(close)
            _add(m > sg, m < sg)
        if conf.get("stoch"):
            k, d = stochastic(df)
            _add(k > d, k < d)
        if conf.get("ichi"):
            senkou_a, _, _ = ichimoku(df)
            _add(close > senkou_a, close < senkou_a)
        if conf.get("ce"):
            long_exit, short_exit = chandelier_exit(df)
            _add(close > long_exit, close < short_exit)

        long_ok = pd.Series(True, index=df.index)
        short_ok = pd.Series(True, index=df.index)
        long_count = pd.Series(0, index=df.index, dtype=int)
        short_count = pd.Series(0, index=df.index, dtype=int)
        for lc, sc in zip(long_conds, short_conds):
            long_ok &= lc
            short_ok &= sc
            long_count += lc.fillna(False).astype(int)
            short_count += sc.fillna(False).astype(int)

        return long_ok.fillna(False), short_ok.fillna(False), long_count, short_count

    def _sl_tp(self, df: pd.DataFrame, long_sig: pd.Series, short_sig: pd.Series,
               rr: float, sw_len: int) -> Tuple[pd.Series, pd.Series]:
        close = df["close"]
        atr_fb = bool(self.settings["atr_fallback"])
        atr_mult = float(self.settings["atr_mult"])

        swing_lo = last_swing_low(df["low"], sw_len)
        swing_hi = last_swing_high(df["high"], sw_len)
        a = atr(df, 14)

        # Long: pivot dip -> ATR fallback -> %2 garantili
        long_sl = np.where(swing_lo < close, swing_lo,
                           np.where(atr_fb, close - atr_mult * a, np.nan))
        long_sl = np.where((long_sl < close) & np.isfinite(long_sl), long_sl, close * 0.98)
        long_tp = close + (close - long_sl) * rr

        # Short: pivot tepe -> ATR fallback -> %2 garantili
        short_sl = np.where(swing_hi > close, swing_hi,
                            np.where(atr_fb, close + atr_mult * a, np.nan))
        short_sl = np.where((short_sl > close) & np.isfinite(short_sl), short_sl, close * 1.02)
        short_tp = close - (short_sl - close) * rr

        sl = np.where(long_sig.to_numpy(), long_sl, np.where(short_sig.to_numpy(), short_sl, np.nan))
        tp = np.where(long_sig.to_numpy(), long_tp, np.where(short_sig.to_numpy(), short_tp, np.nan))

        return pd.Series(sl, index=df.index), pd.Series(tp, index=df.index)
