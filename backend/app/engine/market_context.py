"""Build a compact market context for the ATOS-X agent council.

The context is deliberately exchange-agnostic at this layer, while the
application itself remains Binance USDⓈ-M Futures only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class MarketContext:
    symbol: str
    price: float
    timeframe: str
    data: Any = None
    features: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "timeframe": self.timeframe,
            "features": dict(self.features),
            "metadata": dict(self.metadata),
        }
