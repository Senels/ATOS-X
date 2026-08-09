"""Deterministic pre-trade risk gate.

This module deliberately does not submit exchange orders. It only decides
whether a normalized signal is eligible for paper execution and calculates
an auditable quantity proposal.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    quantity: float
    notional: float
    leverage: float
    risk_amount: float

    def as_dict(self):
        return asdict(self)


class RiskEngine:
    def __init__(
        self,
        risk_per_trade_pct: float = 1.0,
        max_position_pct: float = 25.0,
        max_leverage: float = 10.0,
        min_notional: float = 5.0,
    ):
        self.risk_per_trade_pct = float(risk_per_trade_pct)
        self.max_position_pct = float(max_position_pct)
        self.max_leverage = float(max_leverage)
        self.min_notional = float(min_notional)

    def evaluate(
        self,
        *,
        equity: float,
        entry_price: float,
        stop_price: float | None,
        leverage: float = 1.0,
        existing_positions: int = 0,
        max_positions: int = 10,
    ) -> RiskDecision:
        if equity <= 0 or entry_price <= 0:
            return RiskDecision(False, "invalid equity or price", 0.0, 0.0, 0.0, 0.0)
        if existing_positions >= max_positions:
            return RiskDecision(False, "maximum open positions reached", 0.0, 0.0, 0.0, 0.0)

        lev = min(max(float(leverage), 1.0), self.max_leverage)
        risk_amount = equity * self.risk_per_trade_pct / 100.0

        if stop_price is not None and stop_price > 0 and stop_price != entry_price:
            stop_distance = abs(entry_price - stop_price) / entry_price
            quantity = risk_amount / abs(entry_price - stop_price)
        else:
            # Without an SL, cap notional by portfolio exposure rather than
            # pretending a precise loss risk can be known.
            max_notional = equity * self.max_position_pct / 100.0 * lev
            quantity = max_notional / entry_price

        max_notional = equity * self.max_position_pct / 100.0 * lev
        quantity = min(quantity, max_notional / entry_price)
        notional = quantity * entry_price

        if notional < self.min_notional:
            return RiskDecision(False, "calculated notional below minimum", 0.0, 0.0, lev, risk_amount)

        return RiskDecision(True, "risk checks passed", quantity, notional, lev, risk_amount)
