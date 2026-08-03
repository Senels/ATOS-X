import asyncio
import json
import websockets
from websockets.exceptions import ConnectionClosed
from loguru import logger

class BinanceWebSocket:
    def __init__(self):
        self.connections = {}
        self.callbacks = {}
        self.running = False
        self.base_url = "wss://stream.binancefuture.com/ws"
        self.reconnect_delay = 5
        self._removed = set()

    async def connect(self, symbol: str):
        stream = f"{symbol.lower()}@trade"
        url = f"{self.base_url}/{stream}"
        try:
            ws = await websockets.connect(url)
            self.connections[symbol] = ws
            logger.info(f"? WebSocket baglandi: {symbol}")
            asyncio.create_task(self._listen(symbol, ws))
        except Exception as e:
            logger.error(f"? WebSocket hatasi {symbol}: {e}")
            asyncio.create_task(self._reconnect(symbol))

    async def _reconnect(self, symbol: str):
        await asyncio.sleep(self.reconnect_delay)
        if self.running:
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
        except ConnectionClosed:
            if symbol in self._removed:
                logger.info(f"WebSocket kapatildi: {symbol}")
                return
            logger.warning(f"⚠️ WebSocket kapandı: {symbol}, yeniden bağlanılıyor...")
            await self.connect(symbol)
        except Exception as e:
            logger.error(f"❌ WebSocket dinleme hatası {symbol}: {e}")
            await self.connect(symbol)

    def subscribe(self, symbol: str, callback):
        if symbol not in self.callbacks:
            self.callbacks[symbol] = []
        self.callbacks[symbol].append(callback)

    async def add(self, symbol: str, callback):
        """Sembol icin baglanti + callback ekler (yoksa)."""
        self._removed.discard(symbol)
        self.subscribe(symbol, callback)
        if symbol not in self.connections:
            await self.connect(symbol)

    async def remove(self, symbol: str):
        """Sembol icin baglanti ve callback'leri kapatir (yeniden baglanmaz)."""
        self._removed.add(symbol)
        ws = self.connections.pop(symbol, None)
        if ws:
            try:
                await ws.close()
            except Exception:
                pass
        self.callbacks.pop(symbol, None)

    async def sync(self, symbols: list, callback):
        """Aktif abonelik setini hedef sembol seti ile hizalar."""
        targets = set(symbols)
        for s in targets:
            if s not in self.callbacks:
                await self.add(s, callback)
        for existing in list(self.connections.keys()):
            if existing not in targets:
                await self.remove(existing)

    async def start(self, symbols: list):
        self.running = True
        for s in symbols:
            asyncio.create_task(self.connect(s))

    async def stop(self):
        self.running = False
        for sym in self.connections:
            self._removed.add(sym)
        for ws in self.connections.values():
            await ws.close()
        logger.info("🔴 WebSocket kapatıldı")
