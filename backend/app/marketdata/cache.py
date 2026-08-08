"""TTL cache: pahali/uzak veri kaynaklarini (Binance fapi, Stooq) tamponlar.

Thread-safe (threading.Lock): auto_trader dongusu + ThreadPool worker'lari ayni
cache'i kullanabilir. `get_or_compute` tek sorgu deseni icin yeterlidir.
"""
import threading
import time
from typing import Any, Callable, Dict, Tuple

_MISSING = object()


class TTLCache:
    def __init__(self) -> None:
        self._store: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def set(self, key: str, value: Any, ttl: float) -> None:
        with self._lock:
            self._store[key] = (time.monotonic() + ttl, value)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            item = self._store.get(key)
        if item is None:
            return default
        exp, value = item
        if exp < time.monotonic():
            with self._lock:
                self._store.pop(key, None)
            return default
        return value

    def get_or_compute(self, key: str, ttl: float, compute: Callable[[], Any]) -> Any:
        found = self.get(key, _MISSING)
        if found is not _MISSING:
            return found
        value = compute()
        self.set(key, value, ttl)
        return value

    def remove(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    @property
    def keys(self) -> list:
        with self._lock:
            return list(self._store.keys())
