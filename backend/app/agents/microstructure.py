"""K3 Mikro yapi ajanlari: Binance fapi verileri (OI, funding, L/S, taker,
emir defteri, balina akisi, premium). Girdi `ctx.micro` icinden gelir —
orchestrator BinanceExtraData ile doldurur; veri yoksa ajan cekimser kalir.
"""
from typing import Any, List

from app.agents.base import Agent, AgentResult


def _trend(vals: List[float]) -> float:
    """Son degerin pencere ortalamasina gore egimi (oran)."""
    if len(vals) < 3:
        return 0.0
    avg = sum(vals[:-1]) / (len(vals) - 1)
    return (vals[-1] - avg) / abs(avg) if abs(avg) > 1e-12 else 0.0


class OpenInterestAgent(Agent):
    agent_id = "open_interest_trend"
    name = "Open Interest"
    category = "microstructure"
    tier = 1
    default_weight = 0.2

    def analyze(self, ctx: Any) -> AgentResult:
        oi = (ctx.micro or {}).get("open_interest")
        if not oi or oi.get("history") is None:
            return AgentResult(self.agent_id, None, self.default_weight, "OI verisi yok", confidence=0.3)
        hist = oi["history"]
        oi_trend = _trend(hist)
        price_trend = oi.get("price_trend", 0.0)
        vote = None
        if oi_trend > 0.03 and price_trend > 0:
            vote = "BUY"
            reason = "OI artiyor + fiyat yukari (yeni long)"
        elif oi_trend > 0.03 and price_trend < 0:
            vote = "SELL"
            reason = "OI artiyor + fiyat asagi (short birikimi)"
        elif oi_trend < -0.03 and price_trend < 0:
            vote = "SELL"
            reason = "OI azaliyor + fiyat asagi (long tasfiyesi)"
        else:
            reason = f"OI trend %{oi_trend * 100:.1f}"
        return AgentResult(self.agent_id, vote, self.default_weight, reason,
                           confidence=0.6 if vote else 0.4,
                           meta={"oi_trend": round(oi_trend, 3), "price_trend": round(price_trend, 3)})


class FundingExtremeAgent(Agent):
    agent_id = "funding_extreme"
    name = "Funding Oranı"
    category = "microstructure"
    tier = 1
    default_weight = 0.2

    def analyze(self, ctx: Any) -> AgentResult:
        f = (ctx.micro or {}).get("funding")
        if not f or f.get("last") is None:
            return AgentResult(self.agent_id, None, self.default_weight, "Funding verisi yok", confidence=0.3)
        last = f["last"]
        vote = None
        if last > 0.0001:
            vote = "SELL"
            reason = f"Ayri long fonlamasi ({last * 100:.4f}%)"
        elif last < -0.0001:
            vote = "BUY"
            reason = f"Ayri short fonlamasi ({last * 100:.4f}%)"
        else:
            reason = f"Noktral funding ({last * 100:.4f}%)"
        return AgentResult(self.agent_id, vote, self.default_weight, reason,
                           confidence=min(abs(last) * 4000.0, 0.8) if vote else 0.4,
                           meta={"funding_pct": round(last * 100, 4)})


class LongShortRatioAgent(Agent):
    agent_id = "long_short_ratio"
    name = "Long/Short Oranı"
    category = "microstructure"
    tier = 1
    default_weight = 0.15

    def analyze(self, ctx: Any) -> AgentResult:
        ls = (ctx.micro or {}).get("long_short")
        if not ls or ls.get("last") is None:
            return AgentResult(self.agent_id, None, self.default_weight, "L/S verisi yok", confidence=0.3)
        v = ls["last"]
        vote = "SELL" if v > 1.5 else ("BUY" if v < 0.7 else None)
        return AgentResult(self.agent_id, vote, self.default_weight,
                           f"Long/Short {v:.2f} ({'asiri long' if v > 1.5 else 'asiri short' if v < 0.7 else 'dengeli'})",
                           confidence=min(abs(v - 1.0) * 1.6, 0.75) if vote else 0.4,
                           meta={"ls_ratio": round(v, 3)})


class TakerFlowAgent(Agent):
    agent_id = "taker_flow"
    name = "Taker Akışı"
    category = "microstructure"
    tier = 1
    default_weight = 0.2

    def analyze(self, ctx: Any) -> AgentResult:
        t = (ctx.micro or {}).get("taker")
        if not t or t.get("avg") is None:
            return AgentResult(self.agent_id, None, self.default_weight, "Taker verisi yok", confidence=0.3)
        avg = t["avg"]
        vote = "BUY" if avg > 1.05 else ("SELL" if avg < 0.95 else None)
        return AgentResult(self.agent_id, vote, self.default_weight,
                           f"Alis/Satis orani {avg:.3f} (son 10 periyot)",
                           confidence=min(abs(avg - 1.0) * 8.0, 0.8) if vote else 0.4,
                           meta={"taker_avg": round(avg, 4)})


class OrderbookImbalanceAgent(Agent):
    agent_id = "orderbook_imbalance"
    name = "Emir Defteri Dengesi"
    category = "microstructure"
    tier = 1
    default_weight = 0.15

    def analyze(self, ctx: Any) -> AgentResult:
        ob = (ctx.micro or {}).get("orderbook")
        if not ob or ob.get("imbalance") is None:
            return AgentResult(self.agent_id, None, self.default_weight, "Derinlik verisi yok", confidence=0.3)
        imb = ob["imbalance"]
        vote = "BUY" if imb > 1.3 else ("SELL" if imb < 0.75 else None)
        return AgentResult(self.agent_id, vote, self.default_weight,
                           f"Bid/Ask derinlik {imb:.2f}",
                           confidence=min(abs(imb - 1.0) * 1.8, 0.75) if vote else 0.4,
                           meta={"imbalance": round(imb, 3)})


class WhaleFlowAgent(Agent):
    agent_id = "whale_flow"
    name = "Balina Akışı"
    category = "microstructure"
    tier = 1
    default_weight = 0.2

    def analyze(self, ctx: Any) -> AgentResult:
        w = (ctx.micro or {}).get("whale")
        if not w or w.get("net_usdt") is None:
            return AgentResult(self.agent_id, None, self.default_weight, "Balina verisi yok", confidence=0.3)
        net = w["net_usdt"]
        vote = "BUY" if net > 500_000 else ("SELL" if net < -500_000 else None)
        return AgentResult(self.agent_id, vote, self.default_weight,
                           f"Balina net {net / 1000:.0f}K USDT ({w.get('count', 0)} islem)",
                           confidence=min(abs(net) / 3_000_000.0, 0.8) if vote else 0.4,
                           meta={"net_usdt_k": round(net / 1000, 1)})


class PremiumIndexAgent(Agent):
    agent_id = "premium_index"
    name = "Premium (Mark/Index)"
    category = "microstructure"
    tier = 1
    default_weight = 0.1

    def analyze(self, ctx: Any) -> AgentResult:
        p = (ctx.micro or {}).get("premium")
        if not p or p.get("premium_pct") is None:
            return AgentResult(self.agent_id, None, self.default_weight, "Premium verisi yok", confidence=0.3)
        prem = p["premium_pct"]
        vote = "BUY" if prem > 0.05 else ("SELL" if prem < -0.05 else None)
        return AgentResult(self.agent_id, vote, self.default_weight,
                           f"Mark/Index %{prem:.3f}",
                           confidence=min(abs(prem) * 4.0, 0.7) if vote else 0.4,
                           meta={"premium_pct": round(prem, 4)})


class LiquidationProximityAgent(Agent):
    agent_id = "liquidation_proximity"
    name = "Likidasyon Yakınlığı"
    category = "microstructure"
    tier = 1
    default_weight = 0.1

    def analyze(self, ctx: Any) -> AgentResult:
        liq = (ctx.micro or {}).get("liquidation")
        if not liq or liq.get("position_pct") is None:
            return AgentResult(self.agent_id, None, self.default_weight, "Veri yok", confidence=0.3)
        pos = liq["position_pct"]
        oi_high = liq.get("oi_high", False)
        vote = None
        if pos > 0.92 and oi_high:
            vote = "SELL"
            reason = "24h tepesine yakin + yuksek OI — long likidasyon riski"
        elif pos < 0.08 and oi_high:
            vote = "BUY"
            reason = "24h tabanina yakin + yuksek OI — short likidasyon riski"
        else:
            reason = f"Fiyat 24h araliginda %{pos * 100:.0f} konumda"
        return AgentResult(self.agent_id, vote, self.default_weight, reason,
                           confidence=0.6 if vote else 0.4,
                           meta={"position_pct": round(pos, 3), "oi_high": oi_high})


AGENT_CLASSES = [OpenInterestAgent, FundingExtremeAgent, LongShortRatioAgent, TakerFlowAgent,
                 OrderbookImbalanceAgent, WhaleFlowAgent, PremiumIndexAgent, LiquidationProximityAgent]
