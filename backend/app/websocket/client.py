import asyncio
import json
import websockets
from loguru import logger

class BinanceWebSocket:
    def __init__(self):
        self.connections = {}
        self.callbacks = {}
        self.running = False
        self.base_url = "wss://stream.binancefuture.com/ws"
        self.reconnect_delay = 5

    async def connect(self, symbol: str):
        stream = f"{symbol.lower()}@trade"
        url = f"{self.base_url}/{stream}"
        try:
            ws = await websockets.connect(url)
            self.connections[symbol] = ws
            logger.info(f"✅ WebSocket bağlandı: {symbol}")
            asyncio.create_task(self._listen(symbol, ws))
        except Exception as e:
            logger.error(f"❌ WebSocket hatası {symbol}: {e}")
            await asyncio.sleep(self.reconnect_delay)
            await self.connect(symbol)

    async def _listen(self, symbol: str, ws):
        try:
            async for msg in ws:
                data = json.loads(msg)
                if "data" in data:
                    price = float(data["data"]["p"])
                    if symbol in self.callbacks:
                        for cb in self.callbacks[symbol]:
                            try:
                                await cb(symbol, price)
                            except Exception as e:
                                logger.error(f"Callback hatası {symbol}: {e}")
        except websockets.exceptions.ConnectionClosed:
            logger.warning(f"⚠️ WebSocket kapandı: {symbol}, yeniden bağlanılıyor...")
            await self.connect(symbol)
        except Exception as e:
            logger.error(f"❌ WebSocket dinleme hatası {symbol}: {e}")
            await self.connect(symbol)

    def subscribe(self, symbol: str, callback):
        if symbol not in self.callbacks:
            self.callbacks[symbol] = []
        self.callbacks[symbol].append(callback)

    async def start(self, symbols: list):
        self.running = True
        tasks = [self.connect(s) for s in symbols]
        await asyncio.gather(*tasks)

    async def stop(self):
        self.running = False
        for ws in self.connections.values():
            await ws.close()
        logger.info("🔴 WebSocket kapatıldı")
