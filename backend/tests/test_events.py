import asyncio

from app.core.events import EventBus


async def test_publish_dispatch():
    bus = EventBus()
    bus.bind(asyncio.get_running_loop())
    received = []

    async def handler(payload):
        received.append(payload)

    bus.subscribe("kline", handler)
    bus.publish("kline", {"close": 100.0})
    await asyncio.sleep(0.05)
    assert received == [{"close": 100.0}]


async def test_unsubscribe():
    bus = EventBus()
    bus.bind(asyncio.get_running_loop())

    async def handler(payload):
        pass

    bus.subscribe("trade", handler)
    bus.unsubscribe("trade", handler)
    assert bus.subscriber_count("trade") == 0
