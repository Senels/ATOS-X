import asyncio
import os
import urllib3
import pandas as pd
from binance.client import Client
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv()

class BinanceClient:
    def __init__(self):
        self.api_key = os.getenv("BINANCE_API_KEY", "")
        self.api_secret = os.getenv("BINANCE_SECRET_KEY", "")
        self.testnet = os.getenv("BINANCE_TESTNET", "True").lower() == "true"
        self.client = None
        self.last_price = 62789.2
        self.all_symbols = []

    async def connect(self):
        try:
            self.client = Client(self.api_key, self.api_secret, testnet=self.testnet, requests_params={'verify': False, 'timeout': 30})
            print("[BINANCE] testnet baglandi")
            await self.load_all_symbols()
            return True
        except Exception as e:
            print(f"[BINANCE] baglanti hatasi: {e}")
            return False

    async def load_all_symbols(self):
        try:
            if not self.client:
                await self.connect()
            exchange_info = self.client.futures_exchange_info()
            self.all_symbols = [s['symbol'] for s in exchange_info['symbols'] if s['symbol'].endswith('USDT') and s['status'] == 'TRADING']
            print(f"[BINANCE] {len(self.all_symbols)} USDT cifti yuklendi")
            return self.all_symbols
        except Exception as e:
            print(f"[BINANCE] sembol yukleme hatasi: {e}")
            return ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "ADAUSDT"]

    async def get_all_tickers(self):
        try:
            if not self.client:
                await self.connect()
            tickers = self.client.futures_ticker()
            return {t['symbol']: float(t['lastPrice']) for t in tickers if t['symbol'].endswith('USDT')}
        except Exception as e:
            print(f"[BINANCE] ticker hatasi: {e}")
            return {}

    async def get_price(self, symbol: str = "BTCUSDT") -> float:
        if not self.client:
            await self.connect()
        try:
            ticker = self.client.futures_ticker(symbol=symbol)
            price = ticker.get('price') or ticker.get('lastPrice') or ticker.get('bidPrice')
            if price is not None:
                self.last_price = float(price)
                return self.last_price
            return self.last_price
        except Exception as e:
            return self.last_price

    async def get_klines(self, symbol: str = "BTCUSDT", interval: str = "1h",
                         limit: int = 1000) -> pd.DataFrame:
        """Binance futures kline'larini OHLCV DataFrame olarak dondurur.

        Sutunlar: open, high, low, close, volume (index = utc datetime).
        Public endpoint - API anahtari gerektirmez.
        """
        if not self.client:
            await self.connect()
        loop = asyncio.get_running_loop()
        try:
            raw = await loop.run_in_executor(
                None,
                lambda: self.client.futures_klines(symbol=symbol, interval=interval, limit=limit),
            )
        except Exception as e:
            raise Exception(f"Kline cekilemedi {symbol} {interval}: {e}")
        df = pd.DataFrame(raw).iloc[:, :6]
        df.columns = ["open_time", "open", "high", "low", "close", "volume"]
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df = df.set_index("datetime")
        return df[["open", "high", "low", "close", "volume"]]

    async def place_market_order(self, symbol: str, side: str, quantity: float):
        if not self.client:
            await self.connect()
        try:
            return self.client.futures_create_order(symbol=symbol, side=side.upper(), type='MARKET', quantity=quantity)
        except Exception as e:
            raise Exception(f"Emir gönderilemedi: {e}")

    async def close_position(self, symbol: str):
        if not self.client:
            await self.connect()
        try:
            position = self.client.futures_position_information(symbol=symbol)
            if position and float(position[0]['positionAmt']) != 0:
                qty = abs(float(position[0]['positionAmt']))
                side = 'SELL' if float(position[0]['positionAmt']) > 0 else 'BUY'
                return self.client.futures_create_order(symbol=symbol, side=side, type='MARKET', quantity=qty, reduceOnly=True)
            return None
        except Exception as e:
            raise Exception(f"Pozisyon kapatma hatası: {e}")

    async def close(self):
        if self.client:
            self.client.close_connection()
