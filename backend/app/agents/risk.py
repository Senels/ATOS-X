"""K4 Risk/Pozisyon ajanlari: portfoy baglami ve pozisyon boyutlandirma
mudahaleleri. `adjustments` uzerinden size/sl carpani ve `block` doner —
orchestrator + auto_trader bunlari uygular. `ctx.extra` icinden risk durumu
(drawdown, halt bayraklari) ve `ctx.portfolio` acik pozisyonlar gelir.
"""
from datetime import datetime, timezone
from typing import Any

from app.agents.base import Agent, AgentResult
from app.agents.macro import sector_of
from app.strategy.market_intel import volatility_regime


def _ext(ctx: Any, key: str, default: Any = None) -> Any:
    return (ctx.extra or {}).get(key, default)


class PositionSizerAgent(Agent):
    agent_id = "position_sizer"
    name = "Pozisyon Boyutlandırıcı"
    category = "risk"
    tier = 0
    default_weight = 0.3

    def analyze(self, ctx: Any) -> AgentResult:
        vol = volatility_regime(ctx.df)
        regime = vol["regime"]
        size = 1.0
        if regime == "EXTREME":
            size = 0.5
            reason = f"EXTREME ATR (%{vol['atr_pct']:.1f}) — boyut yari"
        elif regime == "HIGH":
            size = 0.75
            reason = f"Yuksek ATR (%{vol['atr_pct']:.1f}) — boyut %75"
        else:
            reason = f"Noktral rejim (%{vol['atr_pct']:.1f})"
        return AgentResult(self.agent_id, None, self.default_weight, reason, confidence=0.5,
                           adjustments={"size_mult": size}, meta={"regime": regime})


class VolatilityRegimeAgent(Agent):
    agent_id = "volatility_regime"
    name = "Volatilite Rejimi"
    category = "risk"
    tier = 0
    default_weight = 0.3

    def analyze(self, ctx: Any) -> AgentResult:
        vol = volatility_regime(ctx.df)
        regime = vol["regime"]
        if regime == "EXTREME":
            return AgentResult(self.agent_id, None, self.default_weight,
                               "EXTREME volatilite — giris veto", confidence=0.8,
                               adjustments={"block": True}, meta={"regime": regime})
        if regime == "HIGH":
            return AgentResult(self.agent_id, None, self.default_weight,
                               "Yuksek volatilite — boyut kucultuldu", confidence=0.6,
                               adjustments={"size_mult": 0.7}, meta={"regime": regime})
        return AgentResult(self.agent_id, None, self.default_weight,
                           f"Vol rejim {regime}", confidence=0.4, meta={"regime": regime})


class DrawdownGuardAgent(Agent):
    agent_id = "drawdown_guard"
    name = "Drawdown Koruması"
    category = "risk"
    tier = 0
    default_weight = 0.4

    def analyze(self, ctx: Any) -> AgentResult:
        dd = _ext(ctx, "drawdown_pct", 0.0)
        max_dd = float((ctx.settings or {}).get("max_drawdown_pct", 20.0)) or 20.0
        halted = _ext(ctx, "risk_halted", False)
        if halted or dd >= max_dd * 0.95:
            return AgentResult(self.agent_id, None, self.default_weight,
                               f"Drawdown %{dd:.1f} — giris engeli", confidence=0.85,
                               adjustments={"block": True}, meta={"drawdown_pct": dd})
        if dd >= max_dd * 0.7:
            return AgentResult(self.agent_id, None, self.default_weight,
                               f"Drawdown %{dd:.1f} — boyut %60", confidence=0.7,
                               adjustments={"size_mult": 0.6}, meta={"drawdown_pct": dd})
        return AgentResult(self.agent_id, None, self.default_weight,
                           f"Drawdown %{dd:.1f} normal", confidence=0.4, meta={"drawdown_pct": dd})


class CorrelationRiskAgent(Agent):
    agent_id = "correlation_risk"
    name = "Korelasyon Riski"
    category = "risk"
    tier = 2
    default_weight = 0.3

    def analyze(self, ctx: Any) -> AgentResult:
        corr = ctx.corr or {}
        matrix = corr.get("matrix", {})
        my_row = matrix.get(ctx.symbol, {})
        if not my_row:
            return AgentResult(self.agent_id, None, self.default_weight, "Korelasyon verisi yok", confidence=0.3)
        held = [p for p in ctx.portfolio if p.get("status", "OPEN") == "OPEN"]
        if not held:
            return AgentResult(self.agent_id, None, self.default_weight, "Acik pozisyon yok", confidence=0.4)
        values = [abs(my_row.get(p["symbol"])) for p in held if p["symbol"] in my_row]
        if not values:
            return AgentResult(self.agent_id, None, self.default_weight, "Eslesen korelasyon yok", confidence=0.4)
        avg = sum(values) / len(values)
        size = 1.0
        if avg > 0.85:
            size = 0.4
            reason = f"Pozisyonlarla ort. korelasyon {avg:.2f} — agir carpisma"
        elif avg > 0.65:
            size = 0.7
            reason = f"Pozisyonlarla ort. korelasyon {avg:.2f}"
        else:
            reason = f"Pozisyonlarla ort. korelasyon {avg:.2f} — cesitlendirme uygun"
        return AgentResult(self.agent_id, None, self.default_weight, reason,
                           confidence=0.7 if size < 1.0 else 0.4,
                           adjustments={"size_mult": size}, meta={"avg_corr": avg})


class SectorRiskAgent(Agent):
    agent_id = "sector_risk"
    name = "Sektör Konsantrasyonu"
    category = "risk"
    tier = 2
    default_weight = 0.2

    def analyze(self, ctx: Any) -> AgentResult:
        sector = sector_of(ctx.symbol)
        if sector is None:
            return AgentResult(self.agent_id, None, self.default_weight, "Sektor atanmamis", confidence=0.3)
        held = [p for p in ctx.portfolio if p.get("status", "OPEN") == "OPEN"]
        if not held:
            return AgentResult(self.agent_id, None, self.default_weight, "Acik pozisyon yok", confidence=0.4)
        same = sum(1 for p in held if sector_of(p["symbol"]) == sector)
        ratio = same / len(held)
        size = 0.6 if ratio >= 0.4 else (0.8 if ratio >= 0.25 else 1.0)
        return AgentResult(self.agent_id, None, self.default_weight,
                           f"Sektor {sector}: pozisyonlarin %{ratio * 100:.0f}i ayni sektorde",
                           confidence=0.6 if size < 1.0 else 0.4,
                           adjustments={"size_mult": size}, meta={"sector": sector, "same_ratio": ratio})


class ExposureBalanceAgent(Agent):
    agent_id = "exposure_balance"
    name = "Yön Dengesi"
    category = "risk"
    tier = 0
    default_weight = 0.2

    def analyze(self, ctx: Any) -> AgentResult:
        held = [p for p in ctx.portfolio if p.get("status", "OPEN") == "OPEN"]
        if not held:
            return AgentResult(self.agent_id, None, self.default_weight, "Acik pozisyon yok", confidence=0.4)
        longs = sum(1 for p in held if p.get("side") == "LONG")
        total = len(held)
        long_ratio = longs / total
        size = 1.0
        if long_ratio > 0.7:
            size = 0.7
            reason = f"Portfoy %{long_ratio * 100:.0f} long — yeni longlar kucultuldu"
        elif long_ratio < 0.3:
            size = 0.7
            reason = f"Portfoy %{(1 - long_ratio) * 100:.0f} short — yeni shortlar kucultuldu"
        else:
            reason = f"Yon dengesi ok (long %{long_ratio * 100:.0f})"
        return AgentResult(self.agent_id, None, self.default_weight, reason,
                           confidence=0.6 if size < 1.0 else 0.4,
                           adjustments={"size_mult": size}, meta={"long_ratio": long_ratio})


class TimeRiskAgent(Agent):
    agent_id = "time_risk"
    name = "Zaman Riski"
    category = "risk"
    tier = 0
    default_weight = 0.1

    def analyze(self, ctx: Any) -> AgentResult:
        hour = datetime.now(timezone.utc).hour
        if 21 <= hour <= 23:
            return AgentResult(self.agent_id, None, self.default_weight,
                               "UTC 21-23 arasi — piyasa kapanis oncesi temkinli",
                               confidence=0.5, adjustments={"size_mult": 0.85},
                               meta={"hour": hour})
        return AgentResult(self.agent_id, None, self.default_weight,
                           f"Zaman riski normal (UTC {hour:02d}:00)", confidence=0.3,
                           meta={"hour": hour})


class PortfolioCorrWatchAgent(Agent):
    agent_id = "portfolio_corr_watch"
    name = "Portföy Korelasyonu"
    category = "risk"
    tier = 2
    default_weight = 0.2

    def analyze(self, ctx: Any) -> AgentResult:
        corr = ctx.corr or {}
        avg = corr.get("avg_level")
        if avg is None:
            return AgentResult(self.agent_id, None, self.default_weight, "Korelasyon verisi yok", confidence=0.3)
        if avg > 0.7:
            return AgentResult(self.agent_id, None, self.default_weight,
                               f"Piyasa korelasyonu cok yuksek ({avg:.2f}) — cesitlendirme zayif",
                               confidence=0.65, adjustments={"size_mult": 0.7},
                               meta={"avg_level": avg})
        return AgentResult(self.agent_id, None, self.default_weight,
                           f"Piyasa korelasyonu {avg:.2f}", confidence=0.4, meta={"avg_level": avg})


class EquityFloorAgent(Agent):
    agent_id = "equity_floor"
    name = "Equity Tabanı"
    category = "risk"
    tier = 0
    default_weight = 0.4

    def analyze(self, ctx: Any) -> AgentResult:
        equity = _ext(ctx, "equity", None)
        min_eq = float((ctx.settings or {}).get("min_equity", 0.0) or 0.0)
        if min_eq <= 0:
            return AgentResult(self.agent_id, None, self.default_weight,
                               "Equity tabani devre disi", confidence=0.4)
        if equity is None:
            return AgentResult(self.agent_id, None, self.default_weight,
                               "Equity verisi yok", confidence=0.4)
        if equity < min_eq:
            return AgentResult(self.agent_id, None, self.default_weight,
                               f"Equity {equity:.0f} < taban {min_eq:.0f} — giris engeli",
                               confidence=0.85, adjustments={"block": True},
                               meta={"equity": round(float(equity), 2),
                                     "min_equity": min_eq})
        return AgentResult(self.agent_id, None, self.default_weight,
                           f"Equity {equity:.0f} tabanin ustunde", confidence=0.4,
                           meta={"equity": round(float(equity), 2)})


AGENT_CLASSES = [PositionSizerAgent, VolatilityRegimeAgent, DrawdownGuardAgent, CorrelationRiskAgent,
                 SectorRiskAgent, ExposureBalanceAgent, TimeRiskAgent, PortfolioCorrWatchAgent,
                 EquityFloorAgent]
