"""Binance fapi ek verileri: open interest, funding, L/S orani, taker akisi,
premium ve emir defteri. Mevcut BinanceClient'in `_run` asenkron sarmalayicisini
kullanir; testnet'te bulunmayan veri turlerinde (L/S orani vb.) None doner —
ajanlar eksik veride cekimser kalir (graceful degrade).
"""
from typing import Any, Dict, Optional


class BinanceExtraData:
    def __init__(self, binance_client) -> None:
        self._bc = binance_client

    async def _run(self, fn, *args, **kwargs) -> Any:
        if not self._bc or not self._bc.client:
            return None
        try:
            return await self._bc._run(fn, *args, **kwargs)
        except Exception:
            return None

    async def open_interest(self, symbol: str) -> Optional[Dict[str, Any]]:
        res = await self._run(self._bc.client.futures_open_interest, symbol)
        if not res:
            return None
        try:
            return {"oi": float(res.get("openInterest", 0.0)),
                    "time": res.get("time")}
        except (TypeError, ValueError):
            return None

    async def funding_rate(self, symbol: str, limit: int = 10) -> Optional[Dict[str, Any]]:
        res = await self._run(self._bc.client.futures_funding_rate, symbol=symbol, limit=limit)
        if not res:
            return None
        rates = [float(r.get("fundingRate", 0.0)) for r in res if isinstance(r, dict)]
        if not rates:
            return None
        return {"last": rates[-1], "avg10": sum(rates) / len(rates),
                "min": min(rates), "max": max(rates)}

    async def long_short_ratio(self, symbol: str, period: str = "1h", limit: int = 10) -> Optional[Dict[str, Any]]:
        res = await self._run(self._bc.client.futures_top_longshort_account_ratio,
                              symbol=symbol, period=period, limit=limit)
        if not res:
            return None
        vals = [float(r.get("longShortRatio", 0.0)) for r in res if isinstance(r, dict)]
        if not vals:
            return None
        return {"last": vals[-1], "avg": sum(vals) / len(vals)}

    async def taker_flow(self, symbol: str, period: str = "1h", limit: int = 10) -> Optional[Dict[str, Any]]:
        res = await self._run(self._bc.client.futures_takerlongshort_ratio,
                              symbol=symbol, period=period, limit=limit)
        if not res:
            return None
        vals = [float(r.get("buySellRatio", 0.0)) for r in res if isinstance(r, dict)]
        if not vals:
            return None
        return {"last": vals[-1], "avg": sum(vals) / len(vals)}

    async def premium_index(self, symbol: str) -> Optional[Dict[str, Any]]:
        res = await self._run(self._bc.client.futures_premium_index, symbol=symbol)
        if not res:
            return None
        try:
            mark = float(res.get("markPrice", 0.0))
            index = float(res.get("indexPrice", 0.0))
            return {"mark": mark, "index": index,
                    "premium_pct": (mark / index - 1) * 100.0 if index else 0.0,
                    "last_funding": float(res.get("lastFundingRate", 0.0))}
        except (TypeError, ValueError):
            return None

    async def orderbook(self, symbol: str, depth: int = 10) -> Optional[Dict[str, Any]]:
        res = await self._run(self._bc.client.futures_order_book, symbol=symbol, limit=depth)
        if not res:
            return None
        bids = res.get("bids") or []
        asks = res.get("asks") or []
        bid_vol = sum(float(b[1]) for b in bids[:depth])
        ask_vol = sum(float(a[1]) for a in asks[:depth])
        return {"bid_vol": bid_vol, "ask_vol": ask_vol,
                "imbalance": bid_vol / ask_vol if ask_vol > 0 else 1.0}
