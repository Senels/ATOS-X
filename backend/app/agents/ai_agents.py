"""K6 AI/Makine Ogrenmesi ajanlari: mevcut TF modeli, rejim siniflandirici,
etiket egilimi, geri bildirim istatistikleri ve analog (benzesik) bellek
ajanlari. `ctx.extra` icinden predictor, analog sonuclari ve ajan
istatistikleri enjekte edilir; veri/model yoksa ajanlar cekimser kalir.
"""
from typing import Any, Dict, Optional

from app.agents.base import Agent, AgentResult
from app.strategy.market_intel import trend_regime, volatility_regime
from app.strategy.tradebot_v23 import atr as atr_series


def _prediction(ctx: Any) -> Optional[Dict[str, Any]]:
    predictor = (ctx.extra or {}).get("predictor")
    if predictor is None or ctx.df is None:
        return None
    try:
        res = predictor.predict(ctx.df)
        if not res or not res.get("loaded"):
            return None
        return res
    except Exception:
        return None


class AiDirectionAgent(Agent):
    agent_id = "ai_direction"
    name = "AI Yön Tahmini"
    category = "ai"
    tier = 0
    default_weight = 0.5

    def analyze(self, ctx: Any) -> AgentResult:
        pred = _prediction(ctx)
        if not pred:
            return AgentResult(self.agent_id, None, self.default_weight, "AI modeli yok", confidence=0.3)
        direction = pred.get("direction")
        vote = direction if direction in ("BUY", "SELL") else None
        conf = float(pred.get("confidence", 0.5))
        probs = pred.get("probabilities")
        return AgentResult(self.agent_id, vote, self.default_weight,
                           f"AI yon {direction} (guven %{conf * 100:.0f})",
                           confidence=max(conf, 0.3),
                           meta={"probabilities": probs})


class RegimeClassifierAgent(Agent):
    agent_id = "regime_classifier"
    name = "Rejim Sınıflandırıcı"
    category = "ai"
    tier = 0
    default_weight = 0.25

    def analyze(self, ctx: Any) -> AgentResult:
        trend = trend_regime(ctx.df)["regime"]
        vol = volatility_regime(ctx.df)["regime"]
        vote = None
        if trend == "UP" and vol in ("LOW", "NORMAL"):
            vote = "BUY"
            reason = "Trend yukari + normal vol — uygun ortam"
        elif trend == "DOWN" and vol in ("LOW", "NORMAL"):
            vote = "SELL"
            reason = "Trend asagi + normal vol — uygun ortam"
        elif vol == "EXTREME":
            reason = "EXTREME vol — sürpriz rejim"
        elif trend == "RANGE":
            reason = "Yatay rejim — trend yok"
        else:
            reason = f"Karisk rejim ({trend}/{vol})"
        return AgentResult(self.agent_id, vote, self.default_weight, reason,
                           confidence=0.6 if vote else 0.35,
                           meta={"trend": trend, "volatility": vol})


class HistoricalWinrateAgent(Agent):
    agent_id = "historical_winrate"
    name = "Geçmiş İsabet"
    category = "ai"
    tier = 0
    default_weight = 0.2

    def analyze(self, ctx: Any) -> AgentResult:
        stats = (ctx.extra or {}).get("agent_stats")
        if not stats:
            return AgentResult(self.agent_id, None, self.default_weight, "Istatistik yok", confidence=0.3)
        hits = sum(s.get("hits", 0) for s in stats.values())
        total = sum(s.get("resolved", 0) for s in stats.values())
        if total < 20:
            return AgentResult(self.agent_id, None, self.default_weight,
                               "Yeterli geri bildirim yok", confidence=0.4, meta={"resolved": total})
        rate = hits / total
        if rate < 0.45:
            return AgentResult(self.agent_id, None, self.default_weight,
                               f"Konsey isabeti %{rate * 100:.0f} — genel boyut kucultme",
                               confidence=0.7, adjustments={"size_mult": 0.7},
                               meta={"winrate": rate, "resolved": total})
        return AgentResult(self.agent_id, None, self.default_weight,
                           f"Konsey isabeti %{rate * 100:.0f} ({total} tahmin)",
                           confidence=0.5, meta={"winrate": rate, "resolved": total})


class AnomalyDetectorAgent(Agent):
    agent_id = "anomaly_detector"
    name = "Anomali Tespiti"
    category = "ai"
    tier = 0
    default_weight = 0.25

    def analyze(self, ctx: Any) -> AgentResult:
        df = ctx.df
        if df is None or len(df) < 30:
            return AgentResult(self.agent_id, None, self.default_weight, "Yetersiz veri", confidence=0.3)
        r = df["close"].pct_change().dropna()
        last = r.iloc[-1]
        mu, sd = r.mean(), r.std()
        z = (last - mu) / sd if sd and sd > 0 else 0.0
        vol_last = df["volume"].iloc[-1]
        vol_avg = df["volume"].tail(30).iloc[:-1].mean()
        vol_z = (vol_last / vol_avg - 1) if vol_avg and vol_avg > 0 else 0.0
        if abs(z) > 4 or (vol_z > 4 and abs(z) > 2):
            return AgentResult(self.agent_id, None, self.default_weight,
                               f"Anomali: fiyat z {z:.1f}, hacim %{vol_z * 100:.0f} — giris engeli",
                               confidence=0.8, adjustments={"block": True},
                               meta={"price_z": round(float(z), 2), "vol_dev_pct": round(vol_z * 100, 1)})
        return AgentResult(self.agent_id, None, self.default_weight,
                           f"Anomali yok (fiyat z {z:.2f})", confidence=0.4,
                           meta={"price_z": round(float(z), 2)})


class AiLabelBiasAgent(Agent):
    agent_id = "ai_label_bias"
    name = "Etiket Eğilimi"
    category = "ai"
    tier = 0
    default_weight = 0.3

    def analyze(self, ctx: Any) -> AgentResult:
        df = ctx.df
        if df is None or len(df) < 120:
            return AgentResult(self.agent_id, None, self.default_weight, "Yetersiz veri", confidence=0.3)
        close = df["close"]
        horizon = int((ctx.settings or {}).get("ai_horizon", 24) or 24)
        atr_mult = float((ctx.settings or {}).get("ai_atr_mult", 1.0) or 1.0)
        fwd = close.shift(-horizon) / close - 1.0
        thr = atr_series(df, 14) / close * atr_mult
        valid = fwd.notna()
        if int(valid.sum()) < 48:
            return AgentResult(self.agent_id, None, self.default_weight,
                               "Yeterli etiket yok", confidence=0.3)
        recent = df.index[-96:]
        buys = int(((fwd > thr) & valid & df.index.isin(recent)).sum())
        sells = int(((fwd < -thr) & valid & df.index.isin(recent)).sum())
        total = buys + sells
        if total < 24:
            return AgentResult(self.agent_id, None, self.default_weight,
                               f"Etiket dengesi noktral ({buys}B/{sells}S)", confidence=0.35,
                               meta={"buys": buys, "sells": sells})
        bias = (buys - sells) / total
        vote = "BUY" if bias >= 0.5 else ("SELL" if bias <= -0.5 else None)
        return AgentResult(self.agent_id, vote, self.default_weight,
                           f"Etiket egilimi %{bias * 100:.0f} BUY ({buys}B/{sells}S, h{horizon})",
                           confidence=min(abs(bias) * 1.5, 0.8) if vote else 0.35,
                           meta={"bias": round(bias, 3), "buys": buys, "sells": sells})


def _analog(ctx: Any, key: str) -> Optional[Dict[str, Any]]:
    return ((ctx.extra or {}).get("analog") or {}).get(key)


class _AnalogBase(Agent):
    """Analog bellek oyu: gecmis benzesik orneklerin ortalama ileri getirisi."""

    category = "ai"
    tier = 0
    key = "trend"
    analog_name = "trend"
    min_neighbors = 5
    default_weight = 0.4

    def analyze(self, ctx: Any) -> AgentResult:
        res = _analog(ctx, self.key)
        if not res:
            return AgentResult(self.agent_id, None, self.default_weight,
                               "Analog bellek yok", confidence=0.3)
        neighbors = int(res.get("neighbors", 0) or 0)
        if neighbors < self.min_neighbors:
            return AgentResult(self.agent_id, None, self.default_weight,
                               f"Analog bellek yetersiz ({neighbors} komşu)",
                               confidence=0.3, meta=res)
        mean = float(res.get("mean_fwd_pct", 0.0) or 0.0)
        threshold = float((ctx.settings or {}).get("analog_min_fwd_pct", 1.0) or 1.0)
        vote = "BUY" if mean >= threshold else ("SELL" if mean <= -threshold else None)
        conf = min(abs(mean) / 3.0, 0.85) * min(float(res.get("confidence", 0.5)), 1.0) if vote else 0.3
        return AgentResult(self.agent_id, vote, self.default_weight,
                           f"Benzesik {self.analog_name}: ileri ort %{mean:.2f} ({neighbors} komşu)",
                           confidence=conf, meta=res)


class AnalogTrendAgent(_AnalogBase):
    agent_id = "analog_trend"
    name = "Benzeşik Trend"
    key = "trend"
    analog_name = "trend"


class AnalogMomentumAgent(_AnalogBase):
    agent_id = "analog_momentum"
    name = "Benzeşik Momentum"
    key = "momentum"
    analog_name = "momentum"


class AnalogReversalAgent(_AnalogBase):
    agent_id = "analog_reversal"
    name = "Benzeşik Dönüş"
    key = "reversal"
    analog_name = "dönüş"


class AnalogRegimeAgent(_AnalogBase):
    agent_id = "analog_regime"
    name = "Benzeşik Rejim"
    key = "regime"
    analog_name = "rejim"


AGENT_CLASSES = [AiDirectionAgent, RegimeClassifierAgent, HistoricalWinrateAgent, AnomalyDetectorAgent,
                 AiLabelBiasAgent, AnalogTrendAgent, AnalogMomentumAgent, AnalogReversalAgent,
                 AnalogRegimeAgent]
