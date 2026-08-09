"""Safe adapter for the existing Binance Futures WebSocket feed.

This module consumes market prices only. It never creates, modifies, or
cancels exchange orders.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from app.websocket.client import BinanceWebSocket


PriceCallback = Callable[[str, float], Awaitable[None]]


class BinanceMarketFeed:
    def __init__(self, websocket: BinanceWebSocket | None = None):
        self.websocket = websocket or BinanceWebSocket()

    async def subscribe(self, symbols: list[str], callback: PriceCallback) -> None:
        normalized = sorted({s.upper().strip() for s in symbols if s and s.upper().endswith("USDT")})
        await self.websocket.sync(normalized, callback)

    async def stop(self) -> None:
        await self.websocket.stop()
