"""Model/agent OOS scorecard utilities."""
from __future__ import annotations

from typing import Any, Mapping


def score_result(name: str, result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "trades": int(result.get("trades", 0)),
        "return_pct": float(result.get("return_pct", 0.0)),
        "profit_factor": float(result.get("profit_factor", 0.0)),
        "expectancy": float(result.get("expectancy", 0.0)),
        "sharpe": float(result.get("sharpe", 0.0)),
        "sortino": float(result.get("sortino", 0.0)),
        "max_drawdown_pct": float(result.get("max_drawdown_pct", 0.0)),
        "win_rate": float(result.get("win_rate", 0.0)),
        "fees": float(result.get("fees", 0.0)),
        "funding": float(result.get("funding", 0.0)),
    }


def rank_scorecards(results: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Rank without claiming statistical significance.

    The score is a screening score only; promotion should also require a
    minimum OOS trade count and independent robustness checks.
    """
    rows = [score_result(str(r.get("name", "unknown")), r) for r in results]
    for row in rows:
        pf = min(row["profit_factor"], 5.0)
        row["screen_score"] = (
            0.35 * pf +
            0.25 * row["sharpe"] +
            0.20 * row["sortino"] +
            0.20 * row["return_pct"] / 10.0 -
            0.20 * row["max_drawdown_pct"] / 10.0
        )
    return sorted(rows, key=lambda x: x["screen_score"], reverse=True)
