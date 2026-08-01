import numpy as np
from config import Config

class TrailingStopManager:
    def __init__(self):
        self.stops = {}

    def open_position(self, pos_id, entry_price, atr, side):
        activation_dist = atr * Config.TRAIL_ACTIVATION_MULT
        trail_dist = atr * Config.TRAIL_DISTANCE_MULT
        if side == "LONG":
            activation_price = entry_price + activation_dist
            initial_stop = entry_price - trail_dist
        else:
            activation_price = entry_price - activation_dist
            initial_stop = entry_price + trail_dist
        self.stops[pos_id] = {
            "side": side,
            "entry": entry_price,
            "atr": atr,
            "activation_price": activation_price,
            "trail_dist": trail_dist,
            "current_stop": initial_stop,
            "trailing_active": False,
            "is_hit": False,
            "sl_order_id": None,
            "tp_order_id": None
        }
        return initial_stop

    def update_price(self, pos_id, current_price):
        if pos_id not in self.stops or self.stops[pos_id]["is_hit"]:
            return None
        s = self.stops[pos_id]
        if s["side"] == "LONG":
            if not s["trailing_active"] and current_price >= s["activation_price"]:
                s["trailing_active"] = True
                s["current_stop"] = current_price - s["trail_dist"]
                return ("trail_activated", s["current_stop"])
            if s["trailing_active"] and current_price - s["trail_dist"] > s["current_stop"]:
                s["current_stop"] = current_price - s["trail_dist"]
                return ("trail_updated", s["current_stop"])
            if current_price <= s["current_stop"]:
                s["is_hit"] = True
                return ("hit", s["current_stop"])
        else:
            if not s["trailing_active"] and current_price <= s["activation_price"]:
                s["trailing_active"] = True
                s["current_stop"] = current_price + s["trail_dist"]
                return ("trail_activated", s["current_stop"])
            if s["trailing_active"] and current_price + s["trail_dist"] < s["current_stop"]:
                s["current_stop"] = current_price + s["trail_dist"]
                return ("trail_updated", s["current_stop"])
            if current_price >= s["current_stop"]:
                s["is_hit"] = True
                return ("hit", s["current_stop"])
        return None

    def get_stop(self, pos_id):
        if pos_id in self.stops and not self.stops[pos_id]["is_hit"]:
            return self.stops[pos_id]["current_stop"]
        return None

    def set_orders(self, pos_id, sl_order_id=None, tp_order_id=None):
        if pos_id in self.stops:
            if sl_order_id is not None:
                self.stops[pos_id]["sl_order_id"] = sl_order_id
            if tp_order_id is not None:
                self.stops[pos_id]["tp_order_id"] = tp_order_id

    def get_sl_order_id(self, pos_id):
        if pos_id in self.stops:
            return self.stops[pos_id]["sl_order_id"]
        return None

    def get_tp_order_id(self, pos_id):
        if pos_id in self.stops:
            return self.stops[pos_id]["tp_order_id"]
        return None

    def close_position(self, pos_id):
        if pos_id in self.stops:
            del self.stops[pos_id]
