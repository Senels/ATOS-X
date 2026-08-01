"""Critical stop-order fix tests: mocked Binance client."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from execution.trader import BinanceTrader
from execution.trailing_stop import TrailingStopManager


class FakeClient:
    def __init__(self):
        self.orders = []

    def futures_create_order(self, **kwargs):
        self.orders.append(kwargs)
        return {"orderId": len(self.orders) * 100 + 1, "order_id": len(self.orders) * 100 + 1}

    def futures_cancel_order(self, **kwargs):
        return {"status": "CANCELED"}

    def futures_position_information(self):
        return [
            {"symbol": "BTCUSDT", "positionAmt": "1.5", "entryPrice": "65000.0"},
            {"symbol": "ETHUSDT", "positionAmt": "-2.0", "entryPrice": "3500.0"},
            {"symbol": "SOLUSDT", "positionAmt": "0", "entryPrice": "0"},
        ]


class TraderFixTest(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.trader = BinanceTrader(testnet=True)
        self.trader._client = self.client

    def test_market_close_uses_reduce_only(self):
        order = self.trader.market_close("BTCUSDT", "LONG", 1.5)
        self.assertIsNotNone(order)
        call = self.client.orders[-1]
        self.assertEqual(call["side"], "SELL")
        self.assertEqual(call["quantity"], 1.5)
        self.assertEqual(call["type"], "MARKET")
        self.assertTrue(call["reduceOnly"])

    def test_market_close_short_side(self):
        self.trader.market_close("ETHUSDT", "SHORT", 2.0)
        self.assertEqual(self.client.orders[-1]["side"], "BUY")
        self.assertTrue(self.client.orders[-1]["reduceOnly"])

    def test_market_close_invalid_qty(self):
        order = self.trader.market_close("BTCUSDT", "LONG", 0)
        self.assertIsNone(order)
        self.assertEqual(len(self.client.orders), 0)

    def test_set_tp_sl_places_both(self):
        result = self.trader.set_tp_sl("BTCUSDT", "LONG", 65000, 64000, 68000)
        self.assertEqual(len(self.client.orders), 2)
        sl, tp = self.client.orders
        self.assertEqual(sl["type"], "STOP_MARKET")
        self.assertEqual(sl["stopPrice"], 64000)
        self.assertTrue(sl["closePosition"])
        self.assertEqual(tp["type"], "TAKE_PROFIT_MARKET")
        self.assertEqual(tp["stopPrice"], 68000)
        self.assertTrue(tp["closePosition"])
        self.assertIsNotNone(result["sl"])
        self.assertIsNotNone(result["tp"])

    def test_set_tp_sl_sl_only(self):
        result = self.trader.set_tp_sl("BTCUSDT", "SHORT", 65000, 66000, None)
        self.assertEqual(len(self.client.orders), 1)
        self.assertEqual(self.client.orders[0]["side"], "BUY")
        self.assertIsNotNone(result["sl"])
        self.assertIsNone(result["tp"])

    def test_replace_stop_cancels_old(self):
        self.trader.replace_stop("BTCUSDT", "LONG", 101, 64500, 65000)
        self.assertEqual(len(self.client.orders), 1)
        self.assertEqual(self.client.orders[0]["stopPrice"], 64500)

    def test_sync_open_positions_filters_flat(self):
        positions = self.trader.sync_open_positions()
        self.assertEqual(len(positions), 2)
        btc = [p for p in positions if p["symbol"] == "BTCUSDT"][0]
        self.assertEqual(btc["side"], "LONG")
        self.assertEqual(btc["qty"], 1.5)
        eth = [p for p in positions if p["symbol"] == "ETHUSDT"][0]
        self.assertEqual(eth["side"], "SHORT")
        self.assertEqual(eth["qty"], 2.0)


class TrailingStopManagerTest(unittest.TestCase):
    def test_order_ids_roundtrip(self):
        m = TrailingStopManager()
        m.open_position("p1", 100.0, 2.0, "LONG")
        m.set_orders("p1", sl_order_id=42, tp_order_id=43)
        self.assertEqual(m.get_sl_order_id("p1"), 42)
        self.assertEqual(m.get_tp_order_id("p1"), 43)

    def test_trail_hit_flow(self):
        m = TrailingStopManager()
        m.open_position("p1", 100.0, 2.0, "LONG")
        result = m.update_price("p1", 110.0)
        self.assertEqual(result[0], "trail_activated")
        result = m.update_price("p1", 115.0)
        self.assertEqual(result[0], "trail_updated")
        stop = m.get_stop("p1")
        self.assertIsNotNone(stop)
        self.assertGreater(stop, 100.0)
        result = m.update_price("p1", 90.0)
        self.assertEqual(result[0], "hit")

    def test_short_trail_flow(self):
        m = TrailingStopManager()
        m.open_position("p2", 100.0, 2.0, "SHORT")
        result = m.update_price("p2", 90.0)
        self.assertEqual(result[0], "trail_activated")
        result = m.update_price("p2", 120.0)
        self.assertEqual(result[0], "hit")


if __name__ == "__main__":
    unittest.main()
