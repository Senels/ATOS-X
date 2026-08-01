import time
from config import Config

class BinanceTrader:
    def __init__(self, testnet=True):
        self.testnet = testnet
        self._client = None

    def _get_client(self):
        if self._client is None:
            from binance.client import Client
            self._client = Client(
                Config.BINANCE_API_KEY,
                Config.BINANCE_API_SECRET,
                testnet=self.testnet
            )
        return self._client

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

    def _calculate_qty(self, symbol, usdt_size):
        try:
            client = self._get_client()
            info = client.futures_exchange_info()
            for s in info["symbols"]:
                if s["symbol"] == symbol:
                    step = float([f for f in s["filters"] if f["filterType"] == "LOT_SIZE"][0]["stepSize"])
                    price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
                    raw = usdt_size * Config.LEVERAGE / price
                    precision = len(step.split(".")[1].rstrip("0")) if "." in step else 0
                    return round(raw // step * step, precision)
            return 0.001
        except Exception as e:
            print(f"  Qty calc error: {e}")
            return 0.001

    def set_tp_sl(self, symbol, position_side, entry_price, sl_price, tp_price):
        try:
            client = self._get_client()
            side = "SELL" if position_side == "LONG" else "BUY"
            orders = []
            if sl_price:
                sl = client.futures_create_order(
                    symbol=symbol, side=side, type="STOP_MARKET",
                    stopPrice=sl_price, closePosition=True
                )
                orders.append(sl)
            if tp_price:
                tp = client.futures_create_order(
                    symbol=symbol, side=side, type="TAKE_PROFIT_MARKET",
                    stopPrice=tp_price, closePosition=True
                )
                orders.append(tp)
            return orders
        except Exception as e:
            print(f"  TP/SL error {symbol}: {e}")
            return None
