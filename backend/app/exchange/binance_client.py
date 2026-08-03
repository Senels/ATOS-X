import asyncio
import os
import time
import urllib3
import pandas as pd
from binance.client import Client
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv()

from app.data.loader import is_stablecoin_symbol


class _FuturesOnlyClient(Client):
    """Spot bagimliligini kaldirir: constructor spot ping'ini atlar.

    Sistem sadece Binance USDM Futures kullanir.
    """

    def ping(self):
        return {}


class BinanceClient:
    def __init__(self):
        self.api_key = os.getenv("BINANCE_API_KEY", "")
        self.api_secret = os.getenv("BINANCE_SECRET_KEY", "")
        self.testnet = os.getenv("BINANCE_TESTNET", "True").lower() == "true"
        self.client = None
        self.last_price = 62789.2
        self.all_symbols = []
        self.symbol_filters = {}

    async def connect(self):
        try:
            self.client = _FuturesOnlyClient(
                self.api_key,
                self.api_secret,
                testnet=self.testnet,
                ping=False,
                requests_params={'timeout': 30},
            )
            print("[BINANCE] testnet baglandi")
            await self.load_all_symbols()
            await self._sync_time_offset()
            return True
        except Exception as e:
            print(f"[BINANCE] baglanti hatasi: {e}")
            return False

    async def _sync_time_offset(self):
        """Lokal saat sunucuya gore kayiksa imzalari kaydirir (hata -1021)."""
        if not self.client:
            return
        try:
            server_ms = int((await self._run(self.client.futures_time))['serverTime'])
            offset = server_ms - int(time.time() * 1000)
            if abs(offset) > 500:
                self.client.timestamp_offset = offset
                print(f"  [TIME] offset {offset:+d}ms applied")
        except Exception as e:
            print(f"  [TIME] sync failed: {e}")

    async def _run(self, fn, *args, **kwargs):
        """Senkron python-binance cagrisini olay dongusunu bloke etmeden calistirir."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    async def load_all_symbols(self):
        try:
            if not self.client:
                await self.connect()
            exchange_info = await self._run(self.client.futures_exchange_info)
            self.symbol_filters = {
                s['symbol']: {f['filterType']: f for f in s.get('filters', [])}
                for s in exchange_info['symbols']
            }
            self.all_symbols = [
                s['symbol'] for s in exchange_info['symbols']
                if s['symbol'].endswith('USDT') and s['status'] == 'TRADING'
                and not is_stablecoin_symbol(s['symbol'])
            ]
            print(f"[BINANCE] {len(self.all_symbols)} USDT cifti yuklendi")
            return self.all_symbols
        except Exception as e:
            print(f"[BINANCE] sembol yukleme hatasi: {e}")
            return ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "ADAUSDT"]

    def _filter(self, symbol: str, ftype: str, default: str = "0.00000001"):
        """Sembolun tickSize/stepSize gibi filtre degerini verir."""
        try:
            f = self.symbol_filters.get(symbol, {}).get(ftype, {})
            return f.get('tickSize') or f.get('stepSize') or default
        except Exception:
            return default

    @staticmethod
    def _decimal_places(value: str) -> int:
        """'0.00000001' gibi bir filtre degerinin ondalik basamak sayisi."""
        s = value.rstrip('0').rstrip('.')
        return len(s.split('.')[1]) if '.' in s else 0

    def _price_str(self, symbol: str, price: float) -> str:
        places = self._decimal_places(self._filter(symbol, 'PRICE_FILTER'))
        return f"{price:.{places}f}"

    def _qty_str(self, symbol: str, qty: float) -> str:
        places = self._decimal_places(self._filter(symbol, 'LOT_SIZE'))
        return f"{qty:.{places}f}"

    async def get_all_tickers(self):
        try:
            if not self.client:
                await self.connect()
            tickers = await self._run(self.client.futures_ticker)
            return {t['symbol']: float(t['lastPrice']) for t in tickers if t['symbol'].endswith('USDT')}
        except Exception as e:
            print(f"[BINANCE] ticker hatasi: {e}")
            return {}

    async def get_price(self, symbol: str = "BTCUSDT") -> float:
        if not self.client:
            await self.connect()
        try:
            ticker = await self._run(self.client.futures_ticker, symbol=symbol)
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
        try:
            raw = await self._run(
                self.client.futures_klines, symbol=symbol, interval=interval, limit=limit
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
            return await self._run(
                self.client.futures_create_order,
                symbol=symbol, side=side.upper(), type='MARKET',
                quantity=self._qty_str(symbol, quantity),
            )
        except Exception as e:
            raise Exception(f"Emir gonderilemedi: {e}")

    async def close_position(self, symbol: str):
        if not self.client:
            await self.connect()
        try:
            position = await self._run(self.client.futures_position_information, symbol=symbol)
            if position and float(position[0]['positionAmt']) != 0:
                qty = abs(float(position[0]['positionAmt']))
                side = 'SELL' if float(position[0]['positionAmt']) > 0 else 'BUY'
                return await self._run(
                    self.client.futures_create_order,
                    symbol=symbol, side=side, type='MARKET',
                    quantity=self._qty_str(symbol, qty), reduceOnly=True,
                )
            return None
        except Exception as e:
            raise Exception(f"Pozisyon kapatma hatasi: {e}")

    async def get_open_positions(self) -> list:
        """Borsadaki tum acik pozisyonlari dondurur (positionAmt != 0).

        Hata durumunda bos liste DONMEZ (reconcile yanlis kapanis kaydetmesin);
        hata cagiran tarafa firlatilir.
        """
        if not self.client:
            await self.connect()
        positions = await self._run(self.client.futures_position_information)
        return [p for p in positions if float(p.get('positionAmt', 0)) != 0]

    async def set_tp_sl(self, symbol: str, position_side: str, sl_price: float,
                        tp_price: float) -> dict:
        """Exchange-side SL (STOP_MARKET) + TP (TAKE_PROFIT_MARKET) algo emirleri.

        KOSULLU emirler Algo Order API'ye gider (Binance 2025-12-09 zorunlulugu).
        closePosition=True oldugu icin pozisyon kapandiginda Binance otomatik iptal eder.
        Donen: {'sl': algoId, 'tp': algoId}
        """
        if not self.client:
            await self.connect()
        result = {"sl": None, "tp": None}
        side = 'SELL' if position_side.upper() == 'LONG' else 'BUY'
        if not await self._wait_for_position(symbol):
            print(f"  TP/SL error {symbol}: pozisyon bulunamadi")
            return result
        if sl_price:
            try:
                sl = await self._run(
                    self.client.futures_create_order,
                    symbol=symbol, side=side, type='STOP_MARKET',
                    triggerPrice=self._price_str(symbol, sl_price), closePosition=True,
                )
                result["sl"] = sl.get('algoId') or sl.get('orderId') or sl.get('order_id')
            except Exception as e:
                print(f"  SL error {symbol}: {e}")
        if tp_price:
            try:
                tp = await self._run(
                    self.client.futures_create_order,
                    symbol=symbol, side=side, type='TAKE_PROFIT_MARKET',
                    triggerPrice=self._price_str(symbol, tp_price), closePosition=True,
                )
                result["tp"] = tp.get('algoId') or tp.get('orderId') or tp.get('order_id')
            except Exception as e:
                print(f"  TP error {symbol}: {e}")
        return result

    async def _wait_for_position(self, symbol: str, timeout: float = 5.0) -> bool:
        """Market emri onaylandiktan sonra pozisyonun borsaya yansimasini bekler."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                position = await self._run(
                    self.client.futures_position_information, symbol=symbol
                )
                if position and float(position[0]['positionAmt']) != 0:
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.25)
        return False

    async def cancel_algo_order(self, symbol: str, algo_id):
        """Algo (kosullu) emir iptali; algo_id yoksa no-op."""
        if not algo_id:
            return None
        if not self.client:
            await self.connect()
        try:
            return await self._run(
                self.client.futures_cancel_algo_order, symbol=symbol, algoId=algo_id,
            )
        except Exception as e:
            print(f"  Cancel error {symbol} order {algo_id}: {e}")
            return None

    async def get_open_algo_orders(self, symbol: str | None = None):
        """Acik kosullu (algo) emirleri dondurur."""
        if not self.client:
            await self.connect()
        params = {"symbol": symbol} if symbol else {}
        return await self._run(self.client.futures_get_open_algo_orders, **params)

    async def close(self):
        if self.client:
            await self._run(self.client.close_connection)
