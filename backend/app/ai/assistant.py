"""ATOS-X Executive AI Assistant.

The assistant is the management/intelligence layer above strategy, AI model,
agent council, risk, market intelligence, portfolio analytics and Binance
runtime state. It can inspect and plan actions across the system, but it does
not bypass the existing risk gate or live-trading kill switch.

Irreversible or market-moving actions are represented as explicit action plans
and require the normal execution/confirmation path. This keeps the assistant
"fully informed and administratively capable" without creating an unrestricted
second order-entry path.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.agents.registry import all_agents, categories
from app.agents.retrain import memory_summary
from app.strategy.analytics import portfolio_stats
from app.strategy.market_intel import analyze as analyze_market
from app.strategy.settings import get_settings


DOMAIN_MAP = {
    "market": "Piyasa rejimi, volatilite, likidite, momentum, trend ve korelasyon",
    "strategy": "Strateji sinyalleri, TradeBot, TTP/SL/TP, MTF ve karar konseyi",
    "ai": "TensorFlow predictor, analog memory, labels, confidence ve model performansı",
    "agents": "Uzman ajan konseyi, quorum, kategori mutabakatı, veto ve feedback",
    "risk": "Drawdown, daily loss, equity floor, concentration, leverage, VaR/CVaR ve stress",
    "execution": "Binance Futures emir durumu, paper/live modu, slippage, fee ve funding",
    "portfolio": "Equity, PnL, win rate, profit factor, Sharpe, Sortino, Calmar ve MDD",
    "data": "OHLCV kalitesi, freshness, funding/OI/orderbook ve veri bütünlüğü",
    "research": "Makro, mikro yapı, coin intelligence ve tarihsel benzerlikler",
    "system": "Runtime, WebSocket, API, database, backups ve güvenlik durumu",
}

READ_CAPABILITIES = {
    "inspect_system", "inspect_market", "inspect_positions", "inspect_risk",
    "inspect_agents", "inspect_ai", "inspect_strategy", "inspect_data",
    "inspect_portfolio", "inspect_backtests", "explain_decision", "plan_action",
}
ACTION_CAPABILITIES = {
    "start_stop_trading", "change_strategy_settings", "retrain_ai",
    "run_backtest", "run_optimization", "update_position_protection",
    "close_position", "change_trading_mode",
}


@dataclass(frozen=True)
class AssistantAction:
    action: str
    risk: str
    reversible: bool
    requires_confirmation: bool
    allowed_by_policy: bool
    reason: str


class ExecutiveAssistant:
    """Single management/intelligence interface for ATOS-X."""

    role = "EXECUTIVE_AI_ADMIN"
    version = "1.0"

    def capabilities(self) -> Dict[str, List[str]]:
        return {
            "read": sorted(READ_CAPABILITIES),
            "action": sorted(ACTION_CAPABILITIES),
            "domains": sorted(DOMAIN_MAP),
        }

    def knowledge_map(self) -> Dict[str, str]:
        return dict(DOMAIN_MAP)

    def _settings(self) -> Dict[str, Any]:
        try:
            return get_settings()
        except Exception:
            return {}

    def system_snapshot(self, auto_trader: Any = None) -> Dict[str, Any]:
        s = self._settings()
        if auto_trader is None:
            return {
                "mode": "unknown", "paper": bool(s.get("PAPER_TRADING", True)),
                "live_enabled": bool(s.get("LIVE_TRADING_ENABLED", False)),
            }
        positions = getattr(auto_trader, "active_positions", {}) or {}
        return {
            "mode": getattr(auto_trader, "trading_mode", "unknown"),
            "running": bool(getattr(auto_trader, "running", False)),
            "paper": bool(getattr(auto_trader, "paper", True)),
            "live_enabled": bool(getattr(auto_trader, "live_trading_enabled", False)),
            "equity": float(getattr(auto_trader, "equity", 0.0) or 0.0),
            "drawdown_pct": float(getattr(auto_trader, "drawdown_pct", 0.0) or 0.0),
            "day_pnl": float(getattr(auto_trader, "day_pnl", 0.0) or 0.0),
            "open_positions": len(positions),
            "positions": list(positions.keys()),
            "risk_halted": bool(getattr(auto_trader, "risk_halted", False)),
            "loss_halted": bool(getattr(auto_trader, "loss_halted", False)),
            "daily_loss_halted": bool(getattr(auto_trader, "daily_loss_halted", False)),
            "equity_halted": bool(getattr(auto_trader, "equity_halted", False)),
            "risk_events": len(getattr(auto_trader, "risk_events", []) or []),
            "priority_symbols": list(getattr(auto_trader, "priority", [])[:20]),
        }

    def portfolio_snapshot(self, auto_trader: Any = None) -> Dict[str, Any]:
        history = list(getattr(auto_trader, "trade_history", []) or []) if auto_trader else []
        stats = portfolio_stats(history)
        if auto_trader is not None:
            stats["equity"] = float(getattr(auto_trader, "equity", 0.0) or 0.0)
            stats["open_positions"] = len(getattr(auto_trader, "active_positions", {}) or {})
        return stats

    def agent_snapshot(self) -> Dict[str, Any]:
        agents = all_agents()
        return {
            "count": len(agents),
            "categories": categories(),
            "tiers": {
                str(t): sum(1 for a in agents if a.tier == t)
                for t in sorted({a.tier for a in agents})
            },
            "agents": [
                {"id": a.agent_id, "name": a.name, "category": a.category,
                 "tier": a.tier, "weight": a.default_weight}
                for a in agents
            ],
        }

    def intelligence_snapshot(self, auto_trader: Any = None) -> Dict[str, Any]:
        s = self._settings()
        return {
            "model": {
                "enabled": bool(s.get("use_ai_model", False)),
                "path": s.get("ai_model_path", "ai_direction"),
                "min_confidence": s.get("ai_min_confidence"),
                "horizon": s.get("ai_horizon"),
            },
            "council": {
                "enabled": bool(s.get("use_agent_council", False)),
                "min_confidence": s.get("agent_min_confidence"),
                "quorum": (s.get("council") or {}).get("agent_min_quorum"),
                "categories": (s.get("council") or {}).get("agent_min_agree_categories"),
            },
            "memory": memory_summary(),
            "market_data": {
                "macro_loaded": bool(getattr(auto_trader, "_agent_macro", {}) if auto_trader else False),
                "micro_symbols": len(getattr(auto_trader, "_agent_micro", {}) or {}) if auto_trader else 0,
                "correlation_loaded": bool(getattr(auto_trader, "_agent_corr", {}) if auto_trader else False),
            },
        }

    def build_snapshot(self, auto_trader: Any = None) -> Dict[str, Any]:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "role": self.role,
            "system": self.system_snapshot(auto_trader),
            "portfolio": self.portfolio_snapshot(auto_trader),
            "agents": self.agent_snapshot(),
            "intelligence": self.intelligence_snapshot(auto_trader),
            "knowledge_domains": sorted(DOMAIN_MAP),
        }

    def explain(self, decision: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Convert raw engine output into a management-grade explanation."""
        verdict = str(decision.get("verdict", decision.get("direction", "HOLD"))).upper()
        confidence = float(decision.get("confidence", 0.0) or 0.0)
        reason = decision.get("reason") or decision.get("hold_reason") or "belirtilmemiş"
        votes = decision.get("votes") or []
        return {
            "verdict": verdict,
            "confidence": confidence,
            "confidence_pct": round(confidence * 100, 1),
            "decision": "APPROVE" if verdict in ("BUY", "SELL") else "HOLD",
            "primary_reason": reason,
            "evidence": votes,
            "context": context or {},
            "management_message": self._management_message(verdict, confidence, reason),
        }

    @staticmethod
    def _management_message(verdict: str, confidence: float, reason: str) -> str:
        if verdict == "HOLD":
            return f"İşlem önerilmiyor. Ana neden: {reason}."
        return f"{verdict} adayı var; model/konsey güveni %{confidence * 100:.1f}. Nihai giriş risk kapısından geçmelidir."

    def plan(self, action: str, *, live: bool = False, confirmation: bool = False) -> AssistantAction:
        action = str(action).strip().lower()
        if action not in ACTION_CAPABILITIES:
            return AssistantAction(action, "unknown", True, True, False, "tanımsız yönetici eylemi")
        if action in {"close_position", "change_trading_mode", "start_stop_trading"}:
            return AssistantAction(action, "critical" if live else "high", False, True, True,
                                    "piyasa etkili eylem; mevcut risk ve kill-switch kuralları korunur")
        if action in {"change_strategy_settings", "update_position_protection"}:
            return AssistantAction(action, "high" if live else "medium", True, live,
                                    True, "ayar/koruma değişikliği risk sözleşmesine tabidir")
        return AssistantAction(action, "medium", True, False, True, "analitik/araştırma eylemi")

    def admin_policy(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "principle": "full operational visibility + controlled administration",
            "can_read": sorted(READ_CAPABILITIES),
            "can_plan": sorted(ACTION_CAPABILITIES),
            "cannot_bypass": [
                "risk_gate", "AI_gate", "agent_veto", "live_trading_kill_switch",
                "API authentication", "audit trail",
            ],
            "confirmation_required": [
                "close_position", "change_trading_mode", "start_stop_trading"
            ],
        }
