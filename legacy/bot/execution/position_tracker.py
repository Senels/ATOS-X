class PositionTracker:
    def __init__(self):
        self.positions = {}

    def open(self, pos_id, symbol, side, entry_price, qty, margin_used, atr, entry_time=None, entry_bar=0):
        self.positions[pos_id] = {
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "qty": qty,
            "margin_used": margin_used,
            "atr": atr,
            "entry_time": entry_time,
            "entry_bar": entry_bar,
            "unrealized_pnl": 0,
            "status": "open"
        }

    def close(self, pos_id, exit_price, reason="stop", exit_time=None, exit_bar=0):
        if pos_id not in self.positions:
            return None, None
        p = self.positions[pos_id]
        if p["side"] == "LONG":
            pnl = (exit_price - p["entry_price"]) * p["qty"]
        else:
            pnl = (p["entry_price"] - exit_price) * p["qty"]
        p["exit_price"] = exit_price
        p["exit_time"] = exit_time
        p["bars_held"] = exit_bar - p["entry_bar"]
        p["realized_pnl"] = pnl
        p["status"] = "closed"
        p["close_reason"] = reason
        return pnl, p

    def update_upnl(self, pos_id, current_price):
        if pos_id not in self.positions:
            return
        p = self.positions[pos_id]
        if p["side"] == "LONG":
            p["unrealized_pnl"] = (current_price - p["entry_price"]) * p["qty"]
        else:
            p["unrealized_pnl"] = (p["entry_price"] - current_price) * p["qty"]

    def get_open_positions(self):
        return {k: v for k, v in self.positions.items() if v["status"] == "open"}

    def is_open(self, symbol, side):
        for p in self.positions.values():
            if p["symbol"] == symbol and p["side"] == side and p["status"] == "open":
                return True
        return False

    def total_open_margin(self):
        return sum(p["margin_used"] for p in self.positions.values() if p["status"] == "open")
