"""AI-gated adapter around the existing ATOS-X backtest engine.

The existing engine remains responsible for execution mechanics. This adapter
adds an explicit AI approval/rejection ledger before the engine is run, without
changing the strategy's signal-generation logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

import numpy as np
import pandas as pd

from app.backtest.ai_gate import AIDecision, TradeCost, decision_record, evaluate_signal
from app.backtest.engine import BacktestEngine


@dataclass(frozen=True)
class AIGateConfig:
    min_confidence: float = 0.60
    fee_rate: float = 0.0005
    slippage_rate: float = 0.0002
    funding_rate: float = 0.0


def _signal_name(value: int) -> str:
    return "BUY" if int(value) > 0 else "SELL" if int(value) < 0 else "HOLD"


def build_ai_blocks(
    orders: pd.DataFrame,
    *,
    symbol: str,
    confidence: Iterable[float] | np.ndarray,
    timestamps: Optional[Iterable[Any]] = None,
    model_type: str = "unknown",
    config: AIGateConfig = AIGateConfig(),
) -> tuple[np.ndarray, list[dict]]:
    """Convert model confidence into engine-compatible AI blocks + ledger."""
    sig = orders["signal"].to_numpy(int)
    conf = np.asarray(list(confidence), dtype=float)
    if len(conf) != len(sig):
        raise ValueError("confidence length must equal orders length")
    ts = list(timestamps) if timestamps is not None else list(range(len(sig)))
    if len(ts) != len(sig):
        raise ValueError("timestamps length must equal orders length")

    blocks = np.zeros(len(sig), dtype=bool)
    ledger: list[dict] = []
    costs = TradeCost(
        fee_rate=config.fee_rate,
        slippage_rate=config.slippage_rate,
        funding_rate=config.funding_rate,
    )
    for i, (s, c, t) in enumerate(zip(sig, conf, ts)):
        decision: AIDecision = evaluate_signal(
            timestamp=t,
            symbol=symbol,
            signal=_signal_name(s),
            confidence=float(c),
            min_confidence=config.min_confidence,
            model_type=model_type,
        )
        blocks[i] = not decision.approved
        ledger.append(decision_record(decision, costs=costs))
    return blocks, ledger


def run_ai_backtest(
    df: pd.DataFrame,
    orders: pd.DataFrame,
    *,
    symbol: str,
    confidence: Iterable[float] | np.ndarray,
    interval: str = "4h",
    timestamps: Optional[Iterable[Any]] = None,
    model_type: str = "unknown",
    gate: AIGateConfig = AIGateConfig(),
    engine: Optional[BacktestEngine] = None,
) -> Dict[str, Any]:
    """Run the existing execution engine with an explicit AI approval gate."""
    if len(df) != len(orders):
        raise ValueError("df and orders must have equal length")
    ai_blocks, ledger = build_ai_blocks(
        orders,
        symbol=symbol,
        confidence=confidence,
        timestamps=timestamps,
        model_type=model_type,
        config=gate,
    )
    bt = engine or BacktestEngine(fee_rate=gate.fee_rate, slippage=gate.slippage_rate)
    result = bt.run(df, orders, interval=interval, ai_blocks=ai_blocks)

    approved = sum(1 for x in ledger if x["approved"])
    rejected = len(ledger) - approved
    result["ai_gate"] = {
        "min_confidence": gate.min_confidence,
        "approved_signals": approved,
        "rejected_signals": rejected,
        "approval_rate_pct": round(approved / len(ledger) * 100, 2) if ledger else 0.0,
        "model_type": model_type,
        "cost_model": {
            "fee_rate": gate.fee_rate,
            "slippage_rate": gate.slippage_rate,
            "funding_rate": gate.funding_rate,
        },
        "ledger": ledger,
    }
    result["ai_trade_count"] = sum(1 for t in result.get("trades", []) if t.get("pnl") is not None)
    return result
