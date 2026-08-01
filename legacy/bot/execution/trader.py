import time
from config import Config

from binance.client import Client as _BinanceClient


class FuturesOnlyClient(_BinanceClient):
    """Spot bagimliligini kaldirir: constructor spot ping'ini atlar.
    Sistem sadece Binance USDM Futures kullanir."""

    def ping(self):
        return {}


class BinanceTrader:
    def __init__(self, testnet=True):
        self.testnet = testnet
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = FuturesOnlyClient(
                Config.BINANCE_API_KEY,
                Config.BINANCE_API_SECRET,
                testnet=self.testnet
            )
            self._sync_time_offset(self._client)
        return self._client

    def _sync_time_offset(self, client):
        """Lokal saat sunucuya gore kayiksa imzalari kaydirir (hata -1021)."""
        try:
            server_ms = int(client.futures_time()["serverTime"])
            offset = server_ms - int(time.time() * 1000)
            if abs(offset) > 500:
                client.timestamp_offset = offset
                print(f"  [TIME] offset {offset:+d}ms applied")
        except Exception as e:
            print(f"  [TIME] sync failed: {e}")

    def set_leverage(self, symbol, leverage=Config.LEVERAGE):
        try:
            client = self._get_client()
            return client.futures_change_leverage(symbol=symbol, leverage=leverage)
        except Exception as e:
            print(f"  Leverage error {symbol}: {e}")
            return None

    def market_open(self, symbol, side, usdt_size):
        try:
            client = self._get_client()
            qty = self._calculate_qty(symbol, usdt_size)
            order = client.futures_create_order(
                symbol=symbol,
                side="BUY" if side == "LONG" else "SELL",
                type="MARKET",
                quantity=qty
            )
            return order
        except Exception as e:
            print(f"  Order error {symbol} {side}: {e}")
            return None

    def market_close(self, symbol, side, qty):
        """Gerçek kapanış emri: reduceOnly ile pozisyonu kapatir."""
        try:
            client = self._get_client()
            if qty is None or qty <= 0:
                print(f"  Close error {symbol} {side}: invalid qty {qty}")
                return None
            order = client.futures_create_order(
                symbol=symbol,
                side="SELL" if side == "LONG" else "BUY",
                type="MARKET",
                quantity=qty,
                reduceOnly=True
            )
            return order
        except Exception as e:
            print(f"  Close error {symbol} {side}: {e}")
            return None

    def set_tp_sl(self, symbol, position_side, entry_price, sl_price, tp_price):
        """Exchange-side SL (STOP_MARKET) + TP (TAKE_PROFIT_MARKET) emirleri koyar.
        Koşullu emirler Algo Order API'ye gider (Binance 2025-12-09 zorunluluğu).
        Dönen: {'sl': algoId, 'tp': algoId}"""
        try:
            client = self._get_client()
            side = "SELL" if position_side == "LONG" else "BUY"
            result = {"sl": None, "tp": None}
            if sl_price:
                sl = client.futures_create_order(
                    symbol=symbol, side=side, type="STOP_MARKET",
                    stopPrice=sl_price, closePosition=True
                )
                result["sl"] = sl.get("algoId") or sl.get("orderId") or sl.get("order_id")
            if tp_price:
                tp = client.futures_create_order(
                    symbol=symbol, side=side, type="TAKE_PROFIT_MARKET",
                    stopPrice=tp_price, closePosition=True
                )
                result["tp"] = tp.get("algoId") or tp.get("orderId") or tp.get("order_id")
            return result
        except Exception as e:
            print(f"  TP/SL error {symbol}: {e}")
            return None

    def cancel_order(self, symbol, order_id, algo=False):
        """Algo (koşullu) emirlerde algo=True verilir; normal emirlerde varsayilan."""
        if not order_id:
            return None
        try:
            client = self._get_client()
            if algo:
                return client.futures_cancel_algo_order(symbol=symbol, algoId=order_id)
            return client.futures_cancel_order(symbol=symbol, orderId=order_id)
        except Exception as e:
            print(f"  Cancel error {symbol} order {order_id}: {e}")
            return None

    def replace_stop(self, symbol, position_side, old_sl_order_id, new_sl_price, entry_price):
        """Trail ilerledikce eski SL emrini iptal edip yenisini koyar (Algo API)."""
        self.cancel_order(symbol, old_sl_order_id, algo=True)
        return self.set_tp_sl(symbol, position_side, entry_price, new_sl_price, None)

    def get_open_algo_orders(self, symbol=None):
        """Acik kosullu (algo) emirleri dondurur."""
        client = self._get_client()
        params = {"symbol": symbol} if symbol else {}
        return client.futures_get_open_algo_orders(**params)

    def sync_open_positions(self):
        """Borsadaki gerçek açık pozisyonlari dondurur.
        Dondurulen: [{'symbol','side','qty','entry_price'}]"""
        client = self._get_client()
        raw = client.futures_position_information()
        positions = []
        for p in raw:
            amt = float(p.get("positionAmt", 0))
            if abs(amt) < 1e-12:
                continue
            positions.append({
                "symbol": p["symbol"],
                "side": "LONG" if amt > 0 else "SHORT",
                "qty": abs(amt),
                "entry_price": float(p.get("entryPrice", 0))
            })
        return positions

    def _calculate_qty(self, symbol, usdt_size):
        try:
            client = self._get_client()
            info = client.futures_exchange_info()
            for s in info["symbols"]:
                if s["symbol"] == symbol:
                    filters = {f["filterType"]: f for f in s["filters"]}
                    step_str = filters["LOT_SIZE"]["stepSize"]
                    step = float(step_str)
                    price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
                    raw = usdt_size * Config.LEVERAGE / price
                    precision = len(step_str.split(".")[1].rstrip("0")) if "." in step_str else 0
                    return round(raw // step * step, precision)
            return 0.001
        except Exception as e:
            print(f"  Qty calc error: {e}")
            return 0.001
