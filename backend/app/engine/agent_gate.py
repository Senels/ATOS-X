"""Adapter between the existing Agent Council and the deterministic engine."""
from __future__ import annotations

from typing import Any, Mapping

from app.agents.orchestrator import run_council

from .market_context import MarketContext


class AgentGate:
    def evaluate(self, context: MarketContext, settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
        cfg = dict(settings or {})
        results, verdict = run_council(context, cfg)
        return {
            "decision": verdict,
            "agents": [
                {
                    "agent_id": r.agent_id,
                    "category": r.category,
                    "vote": r.vote,
                    "confidence": r.confidence,
                    "weight": r.weight,
                    "adjustments": r.adjustments,
                    "reason": r.reason,
                }
                for r in results
            ],
        }
