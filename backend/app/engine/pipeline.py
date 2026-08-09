"""End-to-end paper decision pipeline.

Signal -> validation -> optional external decision -> risk -> paper fill.
Live Binance execution is intentionally outside this pipeline.
"""
from __future__ import annotations

from typing import Any, Mapping

from .paper_execution import PaperExecutionEngine
from .risk_engine import RiskEngine
from .signal_engine import normalize_signal


class DecisionPipeline:
    def __init__(self, risk: RiskEngine | None = None, executor: PaperExecutionEngine | None = None):
        self.risk = risk or RiskEngine()
        self.executor = executor or PaperExecutionEngine()

    def process(
        self,
        payload: Mapping[str, Any],
        *,
        equity: float,
        decision: Mapping[str, Any] | None = None,
        stop_price: float | None = None,
        leverage: float = 1.0,
        max_positions: int = 10,
    ) -> dict[str, Any]:
        signal = normalize_signal(payload)
        d = dict(decision or {})
        verdict = str(d.get("verdict", "HOLD")).upper()

        if verdict not in {"BUY", "SELL"}:
            return {"status": "REJECTED", "stage": "decision", "reason": "decision is HOLD"}

        expected_side = "LONG" if verdict == "BUY" else "SHORT"
        if expected_side != signal.side:
            return {"status": "REJECTED", "stage": "decision", "reason": "decision direction disagrees with signal"}

        confidence = float(d.get("confidence", 0.0))
        if confidence < 0.50:
            return {"status": "REJECTED", "stage": "decision", "reason": "AI/council confidence below paper threshold"}

        if signal.price is None:
            return {"status": "REJECTED", "stage": "validation", "reason": "paper execution requires a signal price"}

        risk = self.risk.evaluate(
            equity=equity,
            entry_price=signal.price,
            stop_price=stop_price,
            leverage=leverage,
            existing_positions=len(self.executor.positions),
            max_positions=max_positions,
        )
        if not risk.approved:
            return {"status": "REJECTED", "stage": "risk", "reason": risk.reason, "risk": risk.as_dict()}

        order = self.executor.execute(
            symbol=signal.symbol,
            side=signal.side,
            quantity=risk.quantity,
            price=signal.price,
            leverage=risk.leverage,
        )
        return {
            "status": "PAPER_FILLED",
            "signal": signal.as_dict(),
            "decision": d,
            "risk": risk.as_dict(),
            "order": order.as_dict(),
        }
