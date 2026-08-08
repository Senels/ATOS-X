"""K1 Teknik ajanlar: per-sembol kline tabanli, tier 0 (her tarama).

Mevcut vektorize indikatorleri (tradebot_v23) ve piyasa zekasi modullerini
(coin_intel/market_intel) tekrar kullanir; hicbir ajan kendi kline cekmez.
"""
from typing import Any

import pandas as pd

from app.agents.base import Agent, AgentResult
from app.agents.patterns import detect_patterns
from app.strategy.coin_intel import coin_score
from app.strategy.market_intel import liquidity, trend_regime
from app.strategy.tradebot_v23 import ichimoku, macd, rsi, sma


def _rsi_extreme(close: pd.Series) -> str:
    v = rsi(close, 14).iloc[-1]
    if v <= 30:
        return "BUY"
    if v >= 70:
        return "SELL"
    return ""


class TrendEmaAgent(Agent):
    agent_id = "trend_ema"
    name = "EMA Rejimi"
    category = "technical"
    tier = 0
    default_weight = 0.4

    def analyze(self, ctx: Any) -> AgentResult:
        t = trend_regime(ctx.df)
        vote = {"UP": "BUY", "DOWN": "SELL", "RANGE": None}[t["regime"]]
        conf = min(0.5 + abs(t["slope_pct"]) / 2.0, 0.9) if vote else 0.3
        return AgentResult(self.agent_id, vote, self.default_weight,
                           f"EMA rejim {t['regime']} (egim %{t['slope_pct']:.2f})", confidence=conf, meta=t)


class MomentumAgent(Agent):
    agent_id = "momentum"
    name = "Momentum"
    category = "technical"
    tier = 0
    default_weight = 0.3

    def analyze(self, ctx: Any) -> AgentResult:
        sc = coin_score(ctx.df)
        mom = sc.get("momentum_pct", 0.0)
        vote = "BUY" if mom > 0.1 else ("SELL" if mom < -0.1 else None)
        conf = min(abs(mom) / 1.0, 0.9) if vote else 0.3
        return AgentResult(self.agent_id, vote, self.default_weight,
                           f"Momentum %{mom:.2f} (r20 %{sc.get('r20_pct', 0):.2f})", confidence=conf, meta=sc)


class RsiExtremeAgent(Agent):
    agent_id = "rsi_extreme"
    name = "RSI Aşırı Uç"
    category = "technical"
    tier = 0
    default_weight = 0.2

    def analyze(self, ctx: Any) -> AgentResult:
        v = rsi(ctx.df["close"], 14).iloc[-1]
        vote = "BUY" if v <= 30 else ("SELL" if v >= 70 else None)
        conf = min(abs(50 - v) / 30.0, 0.9) if vote else 0.3
        return AgentResult(self.agent_id, vote, self.default_weight,
                           f"RSI {v:.1f} ({'asiri satim' if vote == 'BUY' else 'asiri alim' if vote == 'SELL' else 'noktral'})",
                           confidence=conf, meta={"rsi": round(v, 2)})


class MacdAgent(Agent):
    agent_id = "macd"
    name = "MACD"
    category = "technical"
    tier = 0
    default_weight = 0.25

    def analyze(self, ctx: Any) -> AgentResult:
        m, sg, hist = macd(ctx.df["close"])
        h = hist.dropna()
        if len(h) < 3:
            return AgentResult(self.agent_id, None, self.default_weight, "Yetersiz veri", confidence=0.3)
        cur, prev, prev2 = h.iloc[-1], h.iloc[-2], h.iloc[-3]
        vote = None
        if cur > 0 and prev <= 0:
            vote = "BUY"
        elif cur < 0 and prev >= 0:
            vote = "SELL"
        elif cur > 0 and cur > prev > prev2:
            vote = "BUY"
        elif cur < 0 and cur < prev < prev2:
            vote = "SELL"
        conf = min(abs(cur) / abs(h).max() + 0.3, 0.9) if vote and abs(h).max() > 0 else 0.3
        return AgentResult(self.agent_id, vote, self.default_weight,
                           f"MACD hist {cur:.6f} ({'yukari' if cur > 0 else 'asagi'})", confidence=conf,
                           meta={"hist": round(float(cur), 8)})


class BbReversionAgent(Agent):
    agent_id = "bb_reversion"
    name = "Bollinger Dönüş"
    category = "technical"
    tier = 0
    default_weight = 0.2

    def analyze(self, ctx: Any) -> AgentResult:
        close = ctx.df["close"]
        ma = sma(close, 20)
        sd = close.rolling(20).std()
        up, lo = ma + 2 * sd, ma - 2 * sd
        rng = (up - lo).iloc[-1]
        if rng <= 0 or pd.isna(rng):
            return AgentResult(self.agent_id, None, self.default_weight, "Yetersiz veri", confidence=0.3)
        pb = (close.iloc[-1] - lo.iloc[-1]) / rng
        vote = "BUY" if pb <= 0.1 else ("SELL" if pb >= 0.9 else None)
        return AgentResult(self.agent_id, vote, self.default_weight,
                           f"%B {pb:.2f} ({'alt band' if vote == 'BUY' else 'ust band' if vote == 'SELL' else 'orta'})",
                           confidence=0.55 if vote else 0.3, meta={"pb": round(float(pb), 3)})


class IchimokuAgent(Agent):
    agent_id = "ichimoku"
    name = "Ichimoku"
    category = "technical"
    tier = 0
    default_weight = 0.25

    def analyze(self, ctx: Any) -> AgentResult:
        sa, tenkan, kijun = ichimoku(ctx.df)
        close = ctx.df["close"].iloc[-1]
        vote = None
        if close > sa.iloc[-1] and tenkan.iloc[-1] > kijun.iloc[-1]:
            vote = "BUY"
        elif close < sa.iloc[-1] and tenkan.iloc[-1] < kijun.iloc[-1]:
            vote = "SELL"
        return AgentResult(self.agent_id, vote, self.default_weight,
                           f"Fiyat bulut {'ustunde' if close > sa.iloc[-1] else 'altinda'}",
                           confidence=0.55 if vote else 0.3,
                           meta={"tenkan_kijun": round(float(tenkan.iloc[-1] - kijun.iloc[-1]), 6)})


class ChartPatternAgent(Agent):
    agent_id = "chart_pattern"
    name = "Grafik Deseni"
    category = "technical"
    tier = 0
    default_weight = 0.25

    def analyze(self, ctx: Any) -> AgentResult:
        res = detect_patterns(ctx.df)
        if res["pattern"] is None:
            return AgentResult(self.agent_id, None, self.default_weight, "Desen yok", confidence=0.3, meta=res)
        return AgentResult(self.agent_id, res["direction"], self.default_weight,
                           f"Desen: {res['pattern']}", confidence=res["confidence"], meta=res)


class VolumeProfileAgent(Agent):
    agent_id = "volume_profile"
    name = "Hacim Profili"
    category = "technical"
    tier = 0
    default_weight = 0.25

    def analyze(self, ctx: Any) -> AgentResult:
        df = ctx.df.tail(50)
        if len(df) < 25:
            return AgentResult(self.agent_id, None, self.default_weight, "Yetersiz veri", confidence=0.3)
        hlc3 = (df["high"] + df["low"] + df["close"]) / 3
        vwap = (hlc3 * df["volume"]).cumsum() / df["volume"].cumsum()
        close = df["close"].iloc[-1]
        liq = liquidity(ctx.df)
        z = liq["zscore"]
        ret = close / df["close"].iloc[-4] - 1 if len(df) >= 4 else 0.0
        vote = None
        reason = f"VWAP sapma %{(close / vwap.iloc[-1] - 1) * 100:.2f}, hacim z {z:.1f}"
        if close > vwap.iloc[-1] * 1.004 and z > 0.3 and ret > 0.005:
            vote = "BUY"
        elif close < vwap.iloc[-1] * 0.996 and z > 0.3 and ret < -0.005:
            vote = "SELL"
        return AgentResult(self.agent_id, vote, self.default_weight, reason,
                           confidence=0.5 if vote else 0.3,
                           meta={"vwap_dev_pct": round(float(close / vwap.iloc[-1] - 1) * 100, 3), "zscore": z})


AGENT_CLASSES = [TrendEmaAgent, MomentumAgent, RsiExtremeAgent, MacdAgent,
                 BbReversionAgent, IchimokuAgent, ChartPatternAgent, VolumeProfileAgent]
