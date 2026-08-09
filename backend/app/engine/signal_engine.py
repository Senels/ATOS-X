"""Normalize and validate incoming trading signals before any decision."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Mapping


VALID_SIDES = {"LONG", "SHORT", "BUY", "SELL"}


@dataclass(frozen=True)
class Signal:
    symbol: str
    side: str
    timeframe: str
    price: float | None
    strategy: str
    signal_id: str | None = None
    received_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_signal(payload: Mapping[str, Any]) -> Signal:
    if not isinstance(payload, Mapping):
        raise ValueError("signal payload must be an object")

    symbol = str(payload.get("symbol", "")).upper().strip()
    side = str(payload.get("side", payload.get("action", ""))).upper().strip()
    if side == "BUY":
        side = "LONG"
    elif side == "SELL":
        side = "SHORT"

    if not symbol.endswith("USDT"):
        raise ValueError("only Binance USDⓈ-M USDT symbols are accepted")
    if side not in {"LONG", "SHORT"}:
        raise ValueError("side must be LONG/SHORT")

    price_raw = payload.get("price")
    price = None if price_raw in (None, "") else float(price_raw)
    if price is not None and price <= 0:
        raise ValueError("price must be positive")

    timeframe = str(payload.get("timeframe", "")).strip()
    strategy = str(payload.get("strategy", "external")).strip() or "external"
    received_at = str(payload.get("received_at") or datetime.now(timezone.utc).isoformat())

    return Signal(
        symbol=symbol,
        side=side,
        timeframe=timeframe,
        price=price,
        strategy=strategy,
        signal_id=payload.get("signal_id"),
        received_at=received_at,
    )
