"""Paper execution ledger; never talks to Binance."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from uuid import uuid4


@dataclass
class PaperOrder:
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    leverage: float
    status: str
    created_at: str

    def as_dict(self):
        return asdict(self)


class PaperExecutionEngine:
    def __init__(self):
        self.orders: dict[str, PaperOrder] = {}
        self.positions: dict[str, PaperOrder] = {}

    def execute(self, *, symbol: str, side: str, quantity: float, price: float, leverage: float):
        if quantity <= 0 or price <= 0:
            raise ValueError("quantity and price must be positive")

        order_id = f"PAPER-{uuid4().hex[:12].upper()}"
        order = PaperOrder(
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            leverage=leverage,
            status="FILLED",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.orders[order_id] = order
        self.positions[symbol] = order
        return order
