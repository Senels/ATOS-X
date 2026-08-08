"""Üçüncü taraf veri sağlayıcıları (OpenBB / FMP vb.) — ATOS X dashboard makro veri sekmesi için.

GitHub: https://site.financialmodelingprep.com/developer/docs/stable/economics-indicators
FMP Free tier: 250 req/day, FMP_API_KEY gerekir (https://site.financialmodelingprep.com/)
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

import httpx

_FMP_BASE = "https://financialmodelingprep.com/stable"
_API_KEY = os.getenv("FMP_API_KEY", "")
_TIMEOUT = 15.0


class FMPProvider:
    """Financial Modeling Prep ekonomik göstergeleri sağlayıcısı.

    Ücretsiz tier (250 req/gün). API key `backend/.env` → `FMP_API_KEY`.
    """

    name = "fmp"
    rate = _FMP_BASE
    timeout = _TIMEOUT
    ratelimit = 250  # FMP free tier: 250 requests/day

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or _API_KEY

    # ------------------------------------------------------------------ #
    def _params(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if self.api_key:
            params["apikey"] = self.api_key
        if extra:
            params.update(extra)
        return params

    async def economic_indicator(
        self, name: str = "GDP", limit: int = 12
    ) -> List[Dict[str, Any]]:
        """Ekonomik gösterge zaman serileri.

        Args:
            name: Gösterge adı (GDP, CPI, Unemployment Rate, ...).
            limit: Döndürülecek son kayıt sayısı.
        """
        if not self.api_key:
            return []
        url = f"{_FMP_BASE}/economic-indicators"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(url, params=self._params({"name": name}))
                if resp.status_code != 200:
                    return []
                data: List[Any] = resp.json()
        except (httpx.HTTPError, ValueError):
            return []
        return data[-limit:] if isinstance(data, list) else []

    async def treasury_rates(self, limit: int = 6) -> List[Dict[str, Any]]:
        """Tahvil faiz oranları zaman serisi."""
        if not self.api_key:
            return []
        url = f"{_FMP_BASE}/treasury-rates"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(url, params=self._params())
                if resp.status_code != 200:
                    return []
                data: List[Any] = resp.json()
        except (httpx.HTTPError, ValueError):
            return []
        return data[-limit:] if isinstance(data, list) else []

    async def economic_calendar(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Yaklaşan makro ekonomik veri takvimi."""
        if not self.api_key:
            return []
        url = f"{_FMP_BASE}/economic-calendar"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(url, params=self._params())
                if resp.status_code != 200:
                    return []
                data: List[Any] = resp.json()
        except (httpx.HTTPError, ValueError):
            return []
        return data[:limit] if isinstance(data, list) else []

    async def quote(self, symbol: str) -> Dict[str, Any] | None:
        """Tek bir sembol için quote."""
        if not self.api_key:
            return None
        url = f"{_FMP_BASE}/quote"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(url, params=self._params({"symbol": symbol}))
                if resp.status_code != 200:
                    return None
                data = resp.json()
        except (httpx.HTTPError, ValueError):
            return None
        return data[0] if isinstance(data, list) and data else None
