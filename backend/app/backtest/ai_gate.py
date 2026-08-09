"""AI gate and trade-decision ledger for backtests.

Paper/backtest only. No exchange order submission is performed here.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

@dataclass(frozen=True)
class AIDecision:
    timestamp: Any
    symbol: str
    signal: str
    approved: bool
    confidence: float
    reason: str
    model_type: str = "unknown"

@dataclass(frozen=True)
class TradeCost:
    fee_rate: float = 0.0005
    slippage_rate: float = 0.0002
    funding_rate: float = 0.0
    def total_rate(self) -> float:
        return self.fee_rate + self.slippage_rate + self.funding_rate

def evaluate_signal(*, timestamp: Any, symbol: str, signal: str, confidence: float,
                    min_confidence: float = 0.60,
                    allowed_signals: tuple[str, ...] = ("BUY", "SELL"),
                    model_type: str = "unknown") -> AIDecision:
    """Approve only actionable model directions above the confidence gate."""
    normalized = str(signal).upper()
    confidence = float(confidence)
    if normalized not in allowed_signals:
        return AIDecision(timestamp, symbol, normalized, False, confidence, "non_actionable_signal", model_type)
    if not 0.0 <= confidence <= 1.0:
        return AIDecision(timestamp, symbol, normalized, False, confidence, "invalid_confidence", model_type)
    if confidence < min_confidence:
        return AIDecision(timestamp, symbol, normalized, False, confidence, "below_confidence_threshold", model_type)
    return AIDecision(timestamp, symbol, normalized, True, confidence, "approved", model_type)

def net_return(gross_return: float, costs: TradeCost, turnover: float = 1.0) -> float:
    return float(gross_return) - costs.total_rate() * float(turnover)

def decision_record(decision: AIDecision, gross_return: Optional[float] = None,
                    costs: Optional[TradeCost] = None, turnover: float = 1.0) -> Dict[str, Any]:
    row = asdict(decision)
    row["gross_return"] = gross_return
    row["net_return"] = None
    if gross_return is not None and costs is not None and decision.approved:
        row["net_return"] = net_return(gross_return, costs, turnover)
    if costs is not None:
        row.update({"fee_rate": costs.fee_rate, "slippage_rate": costs.slippage_rate,
                    "funding_rate": costs.funding_rate, "turnover": turnover})
    return row
