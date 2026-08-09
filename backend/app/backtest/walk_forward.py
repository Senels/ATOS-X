"""Walk-forward evaluation primitives for Binance Global USDⓈ-M Futures.

This module is deliberately model-agnostic: it evaluates a chronological
prediction stream and applies explicit trading costs. It never places orders.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable, Sequence


@dataclass(frozen=True)
class CostModel:
    fee_rate: float = 0.0005
    slippage_rate: float = 0.0002
    funding_rate_per_bar: float = 0.0

    @property
    def round_trip_rate(self) -> float:
        return 2.0 * (self.fee_rate + self.slippage_rate)


@dataclass(frozen=True)
class WalkForwardWindow:
    train_start: int
    train_end: int
    test_start: int
    test_end: int


def make_windows(n_samples: int, train_size: int, test_size: int, step: int | None = None, purge: int = 0) -> list[WalkForwardWindow]:
    """Create non-overlapping chronological train/test windows."""
    if min(n_samples, train_size, test_size) <= 0:
        raise ValueError("sample and window sizes must be positive")
    if purge < 0:
        raise ValueError("purge must be >= 0")
    step = test_size if step is None else step
    if step <= 0:
        raise ValueError("step must be positive")

    windows: list[WalkForwardWindow] = []
    train_end = train_size
    while train_end + purge + test_size <= n_samples:
        test_start = train_end + purge
        windows.append(WalkForwardWindow(0, train_end, test_start, test_start + test_size))
        train_end += step
    if not windows:
        raise ValueError("parameters leave no walk-forward window")
    return windows


def trade_return(raw_return: float, *, direction: int, bars_held: int, costs: CostModel) -> float:
    """Convert a raw directional return into net return after costs/funding."""
    if direction not in (-1, 0, 1):
        raise ValueError("direction must be -1, 0 or 1")
    if bars_held < 0:
        raise ValueError("bars_held must be >= 0")
    if direction == 0:
        return 0.0
    gross = direction * raw_return
    funding = bars_held * costs.funding_rate_per_bar
    return gross - costs.round_trip_rate - funding


def summarize_returns(returns: Sequence[float]) -> dict:
    """Return deterministic, cost-aware performance statistics."""
    vals = [float(x) for x in returns]
    if not vals:
        return {"trades": 0, "total_return": 0.0, "win_rate": 0.0, "avg_return": 0.0, "profit_factor": 0.0}
    wins = [x for x in vals if x > 0]
    losses = [x for x in vals if x < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "trades": len(vals),
        "total_return": sum(vals),
        "win_rate": len(wins) / len(vals),
        "avg_return": sum(vals) / len(vals),
        "profit_factor": gross_profit / gross_loss if gross_loss else float("inf") if gross_profit else 0.0,
    }


def evaluate_walk_forward(
    predictions: Iterable[tuple[int, float, int]],
    *,
    costs: CostModel | None = None,
) -> dict:
    """Evaluate `(direction, realized_return, bars_held)` predictions.

    The caller supplies predictions produced strictly inside each test window.
    ``direction`` is -1/0/1; realized_return is the signed market return in the
    test interval before strategy direction and costs are applied.
    """
    costs = costs or CostModel()
    returns = [trade_return(r, direction=d, bars_held=b, costs=costs) for d, r, b in predictions if d != 0]
    return {"cost_model": asdict(costs), **summarize_returns(returns)}
