"""Automatic OOS -> registry -> promotion -> ensemble update cycle.

This module is research/paper-trading orchestration only. It never places
exchange orders and never promotes an agent without explicit OOS evidence.
"""
from __future__ import annotations

from typing import Any

from app.ai.agent_registry import AgentRegistry
from app.ai.promotion import PromotionPolicy, ensemble_weights, evaluate_agent


def process_oos_result(
    registry: AgentRegistry,
    *,
    agent: str,
    symbol: str,
    timeframe: str,
    metrics: dict[str, Any],
    model_version: str = "unknown",
    policy: PromotionPolicy | None = None,
) -> dict[str, Any]:
    decision = evaluate_agent(metrics, policy)
    stored_metrics = dict(metrics)
    stored_metrics["promotion_decision"] = decision["decision"]
    registry.record(agent, symbol, timeframe, stored_metrics, model_version)

    weights = ensemble_weights(registry.snapshot(), symbol, timeframe)
    return {
        "agent": agent,
        "symbol": symbol,
        "timeframe": timeframe,
        "decision": decision,
        "ensemble_weights": weights,
    }


def process_batch(registry: AgentRegistry, results: list[dict[str, Any]],
                  policy: PromotionPolicy | None = None) -> list[dict[str, Any]]:
    """Process a batch deterministically; results are independently gated."""
    output = []
    for result in results:
        output.append(process_oos_result(registry, policy=policy, **result))
    return output
