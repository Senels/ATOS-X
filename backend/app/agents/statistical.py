"""K5 Istatistik ajanlar: per-sembol istatistiksel sinyaller, tier 0.

Getiri dagilimi, otokorelasyon, ADX benzeri trend gucu, z-skoru donusu ve
destek/direnc mesafeleri uzerinden oy uretir. Hepsi vektorize ve deterministik.
"""
from typing import Any, Tuple

import numpy as np
import pandas as pd

from app.agents.base import Agent, AgentResult
from app.strategy.market_intel import liquidity
from app.strategy.tradebot_v23 import rma, sma, true_range


def _adx(df: pd.DataFrame, n: int = 14) -> Tuple[pd.Series, pd.Series, pd.Series]:
    tr = rma(true_range(df), n)
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    pdi = 100 * rma(pd.Series(plus_dm, index=df.index), n) / tr
    mdi = 100 * rma(pd.Series(minus_dm, index=df.index), n) / tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return pdi, mdi, rma(dx, n)


def _returns(df: pd.DataFrame) -> pd.Series:
    return df["close"].pct_change()


class MeanReversionAgent(Agent):
    agent_id = "mean_reversion"
    name = "Ortalama Dönüşü"
    category = "statistical"
    tier = 0
    default_weight = 0.2

    def analyze(self, ctx: Any) -> AgentResult:
        close = ctx.df["close"]
        ma = sma(close, 20)
        sd = close.rolling(20).std().iloc[-1]
        z = (close.iloc[-1] - ma.iloc[-1]) / sd if sd and sd > 0 else 0.0
        vote = "SELL" if z >= 2.0 else ("BUY" if z <= -2.0 else None)
        return AgentResult(self.agent_id, vote, self.default_weight,
                           f"z-skor {z:.2f} (ortalama {'ustu' if z > 0 else 'alti'})",
                           confidence=min(abs(z) / 3.0, 0.8) if vote else 0.3, meta={"zscore": round(float(z), 3)})


class TrendStrengthAgent(Agent):
    agent_id = "trend_strength"
    name = "Trend Gücü"
    category = "statistical"
    tier = 0
    default_weight = 0.25

    def analyze(self, ctx: Any) -> AgentResult:
        pdi, mdi, adx = _adx(ctx.df)
        a = adx.iloc[-1]
        vote = "BUY" if pdi.iloc[-1] > mdi.iloc[-1] else ("SELL" if mdi.iloc[-1] > pdi.iloc[-1] else None)
        if a < 18:
            vote = None
        conf = min(a / 45.0, 0.85) if vote else 0.3
        return AgentResult(self.agent_id, vote, self.default_weight,
                           f"ADX {a:.0f} (+DI {pdi.iloc[-1]:.0f} / -DI {mdi.iloc[-1]:.0f})",
                           confidence=conf, meta={"adx": round(float(a), 1)})


class VolForecastAgent(Agent):
    agent_id = "vol_forecast"
    name = "Volatilite Tahmini"
    category = "statistical"
    tier = 0
    default_weight = 0.2

    def analyze(self, ctx: Any) -> AgentResult:
        r = _returns(ctx.df).dropna()
        if len(r) < 60:
            return AgentResult(self.agent_id, None, self.default_weight, "Yetersiz veri", confidence=0.3)
        sq = (r ** 2)
        fast = sq.ewm(span=10, adjust=False).mean().iloc[-1]
        slow = sq.ewm(span=50, adjust=False).mean().iloc[-1]
        ratio = fast / slow if slow > 0 else 1.0
        vote = None
        if ratio > 1.6:
            reason = f"Volatilite artiyor (oran {ratio:.1f}x)"
            conf = 0.7
        elif ratio < 0.6:
            reason = f"Volatilite azaliyor (oran {ratio:.1f}x)"
            conf = 0.55
        else:
            reason = f"Vol rejimi stabil (oran {ratio:.1f}x)"
            conf = 0.4
        return AgentResult(self.agent_id, vote, self.default_weight, reason, confidence=conf,
                           meta={"ratio": round(float(ratio), 2)})


class AutocorrelationAgent(Agent):
    agent_id = "autocorrelation"
    name = "Otokorelasyon"
    category = "statistical"
    tier = 0
    default_weight = 0.15

    def analyze(self, ctx: Any) -> AgentResult:
        r = _returns(ctx.df).dropna()
        if len(r) < 30:
            return AgentResult(self.agent_id, None, self.default_weight, "Yetersiz veri", confidence=0.3)
        a, b = r.iloc[:-1].to_numpy(), r.iloc[1:].to_numpy()
        if a.std() < 1e-12 or b.std() < 1e-12:
            return AgentResult(self.agent_id, None, self.default_weight, "Sabit seri", confidence=0.3)
        corr = float(np.corrcoef(a, b)[0, 1])
        last = r.iloc[-1]
        vote = None
        if corr > 0.25:
            vote = "BUY" if last > 0 else "SELL"
            reason = f"Momentum surekli (lag1 r {corr:.2f})"
        elif corr < -0.25:
            vote = "SELL" if last > 0 else "BUY"
            reason = f"Donus egilimi (lag1 r {corr:.2f})"
        else:
            reason = f"Rastgele yuru (lag1 r {corr:.2f})"
        return AgentResult(self.agent_id, vote, self.default_weight, reason,
                           confidence=min(abs(corr) * 1.6, 0.8) if vote else 0.3,
                           meta={"lag1_corr": round(corr, 3)})


class SkewnessAgent(Agent):
    agent_id = "skewness"
    name = "Çarpıklık"
    category = "statistical"
    tier = 0
    default_weight = 0.15

    def analyze(self, ctx: Any) -> AgentResult:
        r = _returns(ctx.df).tail(30).dropna()
        if len(r) < 20 or r.std() < 1e-12:
            return AgentResult(self.agent_id, None, self.default_weight, "Yetersiz veri", confidence=0.3)
        n = len(r)
        s = float(((r - r.mean()) ** 3).sum() / (n * r.std() ** 3))
        vote = "SELL" if s < -0.4 else ("BUY" if s > 0.4 else None)
        return AgentResult(self.agent_id, vote, self.default_weight,
                           f"Getiri carpikligi {s:.2f} ({'negatif - kayip kuyrugu' if s < -0.4 else 'pozitif - kazanc kuyrugu' if s > 0.4 else 'simetrik'})",
                           confidence=min(abs(s), 0.7) if vote else 0.3, meta={"skew": round(s, 3)})


class KurtosisAgent(Agent):
    agent_id = "kurtosis"
    name = "Basıklık"
    category = "statistical"
    tier = 0
    default_weight = 0.15

    def analyze(self, ctx: Any) -> AgentResult:
        r = _returns(ctx.df).tail(30).dropna()
        if len(r) < 20 or r.std() < 1e-12:
            return AgentResult(self.agent_id, None, self.default_weight, "Yetersiz veri", confidence=0.3)
        n = len(r)
        k = float(((r - r.mean()) ** 4).sum() / (n * r.std() ** 4))
        vote = None
        if k > 6:
            reason = f"Agir kuyruklar (kurtosis {k:.1f}) - ani hareket riski"
            conf = 0.65
        else:
            reason = f"Normal dagilim yakin (kurtosis {k:.1f})"
            conf = 0.4
        return AgentResult(self.agent_id, vote, self.default_weight, reason, confidence=conf,
                           meta={"kurtosis": round(float(k), 2)})


class SupportResistanceAgent(Agent):
    agent_id = "support_resistance"
    name = "Destek/Direnç"
    category = "statistical"
    tier = 0
    default_weight = 0.25

    def analyze(self, ctx: Any) -> AgentResult:
        df = ctx.df.tail(50)
        close = df["close"].iloc[-1]
        hi = df["high"].iloc[:-1].max()
        lo = df["low"].iloc[:-1].min()
        vote = None
        if close > hi:
            vote = "BUY"
            reason = f"50-bar direnc kirildi (ustu %{(close / hi - 1) * 100:.2f})"
        elif close < lo:
            vote = "SELL"
            reason = f"50-bar destek kirildi (alti %{(1 - close / lo) * 100:.2f})"
        elif (close - lo) / close <= 0.012:
            vote = "BUY"
            reason = f"Destege yakin (mesafe %{(close / lo - 1) * 100:.2f})"
        elif (hi - close) / close <= 0.012:
            vote = "SELL"
            reason = f"Dirence yakin (mesafe %{(1 - close / hi) * 100:.2f})"
        else:
            reason = "Destek/direnc ortasinda"
        return AgentResult(self.agent_id, vote, self.default_weight, reason,
                           confidence=0.6 if vote else 0.3,
                           meta={"support": round(float(lo), 4), "resistance": round(float(hi), 4)})


class LiquidityScoreAgent(Agent):
    agent_id = "liquidity_score"
    name = "Likidite"
    category = "statistical"
    tier = 0
    default_weight = 0.2

    def analyze(self, ctx: Any) -> AgentResult:
        liq = liquidity(ctx.df)
        z = liq["zscore"]
        close = ctx.df["close"]
        ret3 = close.iloc[-1] / close.iloc[-4] - 1 if len(close) >= 4 else 0.0
        vote = None
        if z >= 0.5:
            vote = "BUY" if ret3 > 0 else "SELL"
            reason = f"Yuksek likidite (hacim z {z:.1f}), son 3 bar %{ret3 * 100:.2f}"
        elif z <= -1.0:
            reason = f"Dusuk likidite (hacim z {z:.1f}) - kayma riski"
            conf = 0.55
        else:
            reason = f"Noktral likidite (hacim z {z:.1f})"
            conf = 0.4
        return AgentResult(self.agent_id, vote, self.default_weight, reason,
                           confidence=0.5 if vote else locals().get("conf", 0.4), meta=liq)


AGENT_CLASSES = [MeanReversionAgent, TrendStrengthAgent, VolForecastAgent, AutocorrelationAgent,
                 SkewnessAgent, KurtosisAgent, SupportResistanceAgent, LiquidityScoreAgent]
