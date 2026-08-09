"""OOS evidence-based agent promotion policy."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class PromotionPolicy:
    min_trades: int = 50
    promote_pf: float = 1.20
    promote_sharpe: float = 0.50
    promote_sortino: float = 0.75
    max_drawdown_pct: float = 25.0
    min_positive_windows: float = 0.60
    min_symbol_coverage: float = 0.50


def evaluate_agent(metrics: dict[str, Any], policy: PromotionPolicy | None = None) -> dict[str, Any]:
    p = policy or PromotionPolicy()
    trades = int(metrics.get("trades", 0))
    pf = float(metrics.get("profit_factor", 0.0))
    sharpe = float(metrics.get("sharpe", 0.0))
    sortino = float(metrics.get("sortino", 0.0))
    dd = float(metrics.get("max_drawdown_pct", 100.0))
    positive_windows = float(metrics.get("positive_window_ratio", 0.0))
    coverage = float(metrics.get("symbol_coverage", 0.0))

    hard_fail = trades < p.min_trades or dd > p.max_drawdown_pct
    promote = (
        not hard_fail and pf >= p.promote_pf and sharpe >= p.promote_sharpe
        and sortino >= p.promote_sortino
        and positive_windows >= p.min_positive_windows
        and coverage >= p.min_symbol_coverage
    )
    if promote:
        decision = "PROMOTE"
    elif hard_fail or pf <= 0.0:
        decision = "REJECT"
    else:
        decision = "RETRAIN"
    return {
        "decision": decision,
        "policy": asdict(p),
        "evidence": {
            "trades": trades, "profit_factor": pf, "sharpe": sharpe,
            "sortino": sortino, "max_drawdown_pct": dd,
            "positive_window_ratio": positive_windows,
            "symbol_coverage": coverage,
        },
    }


def ensemble_weights(registry_snapshot: dict[str, Any], symbol: str, timeframe: str) -> dict[str, float]:
    """Derive conservative weights from latest PROMOTED agents only."""
    candidates = []
    for entry in registry_snapshot.get("agents", {}).values():
        if entry.get("symbol") != symbol or entry.get("timeframe") != timeframe or not entry.get("runs"):
            continue
        latest = entry["runs"][-1]
        decision = latest.get("metrics", {}).get("promotion_decision")
        if decision != "PROMOTE":
            continue
        score = float(latest["metrics"].get("ensemble_score", 0.0))
        candidates.append((entry.get("agent", "unknown"), max(score, 0.0)))
    total = sum(score for _, score in candidates)
    if total <= 0:
        return {}
    return {name: score / total for name, score in candidates}
