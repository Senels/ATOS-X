"""Balina islem takipcisi: buyuk (>= esik) trade'lerin son 12 saatlik akisi.

WS `@trade`/`@aggTrade` akisindan `record()` ile beslenir (taker yonu `m`
bayragindan: m=False -> alici taker). `flow()` sembol bazli net alis/satis
USDT hacmi verir — `whale_flow` mikro yapi ajani bunu kullanir.
"""
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Optional


class WhaleTracker:
    THRESHOLD_USDT = 100_000.0
    WINDOW_HOURS = 12.0

    def __init__(self, threshold: float = THRESHOLD_USDT, window_hours: float = WINDOW_HOURS) -> None:
        self.threshold = threshold
        self.window = window_hours * 3600.0
        self._events: Deque[Dict[str, Any]] = deque()
        self._lock = threading.Lock()

    def record(self, symbol: str, price: float, qty: float, is_taker_buy: bool) -> None:
        usdt = float(price) * float(qty)
        if usdt < self.threshold:
            return
        now = time.monotonic()
        with self._lock:
            self._events.append({
                "symbol": symbol, "usdt": usdt, "buy": bool(is_taker_buy), "ts": now,
            })

    def _prune(self) -> None:
        now = time.monotonic()
        while self._events and now - self._events[0]["ts"] > self.window:
            self._events.popleft()

    def flow(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Son pencere icin sembolun net alis/satis USDT akisi (None = veri yok)."""
        with self._lock:
            self._prune()
            items = [e for e in self._events if e["symbol"] == symbol]
        if not items:
            return None
        buy = sum(e["usdt"] for e in items if e["buy"])
        sell = sum(e["usdt"] for e in items if not e["buy"])
        top = sorted(items, key=lambda e: -e["usdt"])[:5]
        return {
            "net_usdt": round(buy - sell, 2),
            "buy_usdt": round(buy, 2),
            "sell_usdt": round(sell, 2),
            "count": len(items),
            "top": [{"usdt": round(e["usdt"], 2), "buy": e["buy"]} for e in top],
        }
