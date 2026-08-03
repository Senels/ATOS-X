import asyncio
import json

import pytest

import app.websocket.client as ws_mod


class _FakeWS:
    def __init__(self, messages):
        self._messages = messages

    async def __aiter__(self):
        for m in self._messages:
            yield m

    async def close(self):
        pass


def test_start_returns_when_connect_fails(monkeypatch):
    async def fail(url, **kw):
        raise ConnectionError("network down")

    monkeypatch.setattr(ws_mod.websockets, "connect", fail)
    ws = ws_mod.BinanceWebSocket()
    ws.reconnect_delay = 0.01

    asyncio.run(ws.start(["BTCUSDT", "ETHUSDT"]))
    ws.running = False


def test_trade_message_triggers_callback(monkeypatch):
    received = []

    async def cb(symbol, price):
        received.append((symbol, price))

    async def fake_connect(url, **kw):
        return _FakeWS([json.dumps({"data": {"p": "65000.5"}})])

    monkeypatch.setattr(ws_mod.websockets, "connect", fake_connect)

    async def main():
        ws = ws_mod.BinanceWebSocket()
        ws.subscribe("BTCUSDT", cb)
        await ws.connect("BTCUSDT")
        await asyncio.sleep(0.05)
        ws.running = False
        await asyncio.sleep(0.02)

    asyncio.run(main())
    assert received == [("BTCUSDT", 65000.5)]
