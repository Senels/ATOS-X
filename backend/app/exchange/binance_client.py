from typing import Any

import aiohttp

from app.core.config import get_settings


class BinanceClient:
    """Async Binance USDM Futures REST client (public endpoints only)."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": "atosx/0.1"},
                timeout=aiohttp.ClientTimeout(total=15),
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        session = await self._get_session()
        url = f"{self._settings.BINANCE_REST_BASE}{path}"
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def exchange_info(self) -> dict[str, Any]:
        data = await self.get("/fapi/v1/exchangeInfo")
        symbols = {}
        for s in data.get("symbols", []):
            filters = {f["filterType"]: f for f in s.get("filters", [])}
            step = float(filters.get("LOT_SIZE", {}).get("stepSize", 0.001))
            tick = float(filters.get("PRICE_FILTER", {}).get("tickSize", 0.01))
            symbols[s["symbol"]] = {"step_size": step, "tick_size": tick}
        return symbols

    async def klines(self, symbol: str, interval: str, limit: int = 500) -> list[dict[str, Any]]:
        data = await self.get(
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )
        return [
            {
                "open_time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": int(k[6]),
                "quote_volume": float(k[7]),
                "trades": int(k[8]),
            }
            for k in data
        ]

    async def mark_price(self, symbol: str) -> float:
        data = await self.get("/fapi/v1/premiumIndex", {"symbol": symbol})
        return float(data.get("markPrice", 0))


class BinanceStreamClient:
    """Websocket stream client (Sprint 3: aggregate trades, mark price, user data)."""

    def __init__(self) -> None:
        self._settings = get_settings()

    def stream_url(self, stream: str) -> str:
        return f"{self._settings.BINANCE_WS_BASE}/ws/{stream}"

    async def watch(self, streams: list[str], handler) -> None:
        raise NotImplementedError("Sprint 3: websocket feed pipeline")
