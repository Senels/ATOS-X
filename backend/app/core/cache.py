"""Redis cache — market verisi onbellegi ve durum depolama.

Uygulama olaylarinda (lifespan) bir kez olusturulup `app.state.cache` olarak
paylasilir. Testlerde sahte istemci (fakeredis) enjekte edilebilir.
"""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis


class Cache:
    def __init__(self, url: str = "", client: aioredis.Redis | None = None) -> None:
        self._client = client
        self._url = url

    async def _get(self) -> aioredis.Redis:
        if self._client is None:
            self._client = aioredis.from_url(self._url, decode_responses=True)
        return self._client

    async def get_json(self, key: str, default: Any = None) -> Any:
        client = await self._get()
        raw = await client.get(key)
        if raw is None:
            return default
        return json.loads(raw)

    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        client = await self._get()
        await client.set(key, json.dumps(value, default=str), ex=ttl)

    async def delete(self, key: str) -> None:
        client = await self._get()
        await client.delete(key)

    async def get(self, key: str) -> str | None:
        client = await self._get()
        return await client.get(key)

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        client = await self._get()
        await client.set(key, value, ex=ttl)

    async def ping(self) -> bool:
        try:
            client = await self._get()
            return bool(await client.ping())
        except Exception:
            return False

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
