"""Compact operational dashboard view-model.

Keeps the home screen decision-focused and separates detailed telemetry from
primary trading decisions. No exchange side effects occur here.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable


def _first(items: Iterable[dict], limit: int):
    return list(items)[:limit]


def build_operational_view(state: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize runtime state into the small set of fields used by Home."""
    risk = state.get("risk", {}) or {}
    ai = state.get("ai", {}) or {}
    system = state.get("system", {}) or {}
    portfolio = state.get("portfolio", {}) or {}
    positions = state.get("positions", []) or []
    opportunities = state.get("opportunities", []) or []
    alerts = state.get("alerts", []) or []

    return {
        "system": {
            "connection": system.get("connection", "UNKNOWN"),
            "data_freshness_ms": system.get("data_freshness_ms"),
            "mode": system.get("mode", "PAPER"),
            "kill_switch": bool(system.get("kill_switch", False)),
        },
        "portfolio": {
            "equity": portfolio.get("equity"),
            "today_pnl": portfolio.get("today_pnl"),
            "drawdown": portfolio.get("drawdown"),
            "open_positions": len(positions),
            "net_exposure": portfolio.get("net_exposure"),
            "margin_utilization": portfolio.get("margin_utilization"),
        },
        "risk": {
            "state": risk.get("state", "UNKNOWN"),
            "daily_loss_limit": risk.get("daily_loss_limit"),
            "drawdown_state": risk.get("drawdown_state"),
            "concentration": risk.get("concentration"),
            "margin_warning": risk.get("margin_warning", False),
            "halt_reason": risk.get("halt_reason"),
        },
        "ai": {
            "symbol": ai.get("symbol"),
            "direction": ai.get("direction", "HOLD"),
            "confidence": ai.get("confidence", 0.0),
            "decision": ai.get("decision", "REJECTED"),
            "reason": ai.get("reason"),
            "model_version": ai.get("model_version"),
        },
        "opportunities": _first(opportunities, 8),
        "positions": _first(positions, 12),
        "alerts": sorted(
            alerts,
            key=lambda a: {"CRITICAL": 0, "HIGH": 1, "WARNING": 2}.get(a.get("severity", "WARNING"), 3),
        )[:8],
    }
