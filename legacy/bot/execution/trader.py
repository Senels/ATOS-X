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
        Bot ölse bile pozisyon korunur. Dönen: {'sl': orderId, 'tp': orderId}"""
        try:
            client = self._get_client()
            side = "SELL" if position_side == "LONG" else "BUY"
            result = {"sl": None, "tp": None}
            if sl_price:
                sl = client.futures_create_order(
                    symbol=symbol, side=side, type="STOP_MARKET",
                    stopPrice=sl_price, closePosition=True
                )
                result["sl"] = sl.get("orderId") or sl.get("order_id")
            if tp_price:
                tp = client.futures_create_order(
                    symbol=symbol, side=side, type="TAKE_PROFIT_MARKET",
                    stopPrice=tp_price, closePosition=True
                )
                result["tp"] = tp.get("orderId") or tp.get("order_id")
            return result
        except Exception as e:
            print(f"  TP/SL error {symbol}: {e}")
            return None

    def cancel_order(self, symbol, order_id):
        if not order_id:
            return None
        try:
            client = self._get_client()
            return client.futures_cancel_order(symbol=symbol, orderId=order_id)
        except Exception as e:
            print(f"  Cancel error {symbol} order {order_id}: {e}")
            return None

    def replace_stop(self, symbol, position_side, old_sl_order_id, new_sl_price, entry_price):
        """Trail ilerledikce eski SL emrini iptal edip yenisini koyar."""
        self.cancel_order(symbol, old_sl_order_id)
        return self.set_tp_sl(symbol, position_side, entry_price, new_sl_price, None)

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
                    step = float([f for f in s["filters"] if f["filterType"] == "LOT_SIZE"][0]["stepSize"])
                    price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
                    raw = usdt_size * Config.LEVERAGE / price
                    precision = len(step.split(".")[1].rstrip("0")) if "." in step else 0
                    return round(raw // step * step, precision)
            return 0.001
        except Exception as e:
            print(f"  Qty calc error: {e}")
            return 0.001
