import asyncio
import json

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


def test_sync_aligns_subscriptions(monkeypatch):
    connected = []

    async def fake_connect(url, **kw):
        connected.append(url)
        return _FakeWS([])

    monkeypatch.setattr(ws_mod.websockets, "connect", fake_connect)

    async def main():
        ws = ws_mod.BinanceWebSocket()
        ws.reconnect_delay = 0.01

        async def cb(symbol, price):
            pass

        await ws.sync(["BTCUSDT", "ETHUSDT"], cb)
        await ws.sync(["ETHUSDT", "SOLUSDT"], cb)
        await asyncio.sleep(0.05)
        assert set(ws.connections) == {"ETHUSDT", "SOLUSDT"}
        assert set(ws.callbacks) == {"ETHUSDT", "SOLUSDT"}
        ws.running = False
        await asyncio.sleep(0.02)

    asyncio.run(main())
    assert any("btcusdt@trade" in u for u in connected)
    assert any("ethusdt@trade" in u for u in connected)
    assert any("solusdt@trade" in u for u in connected)


def test_removed_symbol_does_not_reconnect(monkeypatch):
    class _FakeWSClosed:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise ws_mod.websockets.exceptions.ConnectionClosed(rcvd=None, sent=None)

        async def close(self):
            pass

    connect_calls = []

    async def fake_connect(url, **kw):
        connect_calls.append(url)
        return _FakeWSClosed()

    monkeypatch.setattr(ws_mod.websockets, "connect", fake_connect)

    async def main():
        ws = ws_mod.BinanceWebSocket()
        ws.reconnect_delay = 0.01

        async def cb(symbol, price):
            pass

        await ws.sync(["BTCUSDT"], cb)
        await ws.remove("BTCUSDT")
        await asyncio.sleep(0.05)
        ws.running = False
        await asyncio.sleep(0.02)

    asyncio.run(main())
    assert connect_calls == ["wss://stream.binancefuture.com/ws/btcusdt@trade"]
