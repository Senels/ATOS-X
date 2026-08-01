"""Redis cache modulu: fakeredis ile dogrulama (gercek Redis gerektirmez)."""

import pytest

from app.core.cache import Cache


@pytest.fixture
def cache():
    import fakeredis.aioredis

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    return Cache(client=client)


async def test_json_roundtrip(cache):
    await cache.set_json("candle:BTCUSDT", {"close": 65000.5, "ts": "2026-08-01T12:00:00"})
    value = await cache.get_json("candle:BTCUSDT")
    assert value["close"] == 65000.5


async def test_get_json_default(cache):
    assert await cache.get_json("missing", default=[]) == []


async def test_ttl_expiry(cache):
    await cache.set_json("tmp", {"v": 1}, ttl=1)
    assert await cache.get_json("tmp") == {"v": 1}


async def test_delete(cache):
    await cache.set("key1", "value1")
    await cache.delete("key1")
    assert await cache.get("key1") is None


async def test_ping(cache):
    assert await cache.ping() is True
